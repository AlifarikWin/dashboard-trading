#!/usr/bin/env python3
"""
Hitung seluruh data dashboard lalu tulis sebagai file JSON statis.

Dipakai oleh GitHub Actions (situs statis di GitHub Pages) dan oleh app.py (server lokal/Render).

  python build.py --out site            # data asli
  DEMO=1 python build.py --out site     # data sintetis (tanpa internet)
  python build.py --out site --heavy    # sertakan backtest swing 2 tahun (lebih lama)

Hasil: <out>/data/{meta,signals,check,daytrade,backtest}.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

import data as datasrc
import daytrade as dtm
import analysis as an
import idx as idxsrc
import news
import notify
import risk as rk
import track
import strategy as st
from data import WIB

BASE = Path(__file__).resolve().parent
ORDER = {"BUY": 0, "SELL": 1, "WATCH": 2, "NETRAL": 3}


def load_cfg() -> dict:
    return json.loads(Path(os.getenv("BOT_CONFIG", BASE / "config.json")).read_text(encoding="utf-8"))


def _clean(o):
    """Ubah tipe numpy/pandas agar bisa ditulis sebagai JSON."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if (o != o or o in (float("inf"), float("-inf"))) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    return o


def write(out: Path, name: str, obj) -> None:
    p = out / "data" / f"{name}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(_clean(obj), ensure_ascii=False, separators=(",", ":")))
    tmp.replace(p)


def yahoo_panel(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Cadangan bila data BEI gagal: panel dari Yahoo (tanpa data asing)."""
    rows = []
    for code, df in frames.items():
        d = df.copy()
        d["prev"] = d["Close"].shift(1)
        d = d.dropna()
        rows.append(pd.DataFrame({"code": code, "date": d.index, "open": d["Open"].values, "high": d["High"].values,
                                  "low": d["Low"].values, "close": d["Close"].values, "prev": d["prev"].values,
                                  "volume": d["Volume"].values, "value": (d["Close"] * d["Volume"]).values,
                                  "freq": 0.0, "fbuy": 0.0, "fsell": 0.0, "remarks": ""}))
    return idxsrc.to_panel(pd.concat(rows, ignore_index=True)) if rows else pd.DataFrame()


def live_status(plan: dict, df: pd.DataFrame | None, today) -> dict | None:
    if df is None or df.index[-1].date() != today:
        return None
    r = df.iloc[-1]
    lv = {"last": float(r["Close"]), "high": float(r["High"]), "low": float(r["Low"]), "open": float(r["Open"])}
    if lv["high"] < plan["trigger"]:
        s = "belum terpicu"
    elif lv["open"] > plan["limit"]:
        s = "gap di atas limit — tidak terisi"
    elif lv["low"] <= plan["sl"] and lv["last"] <= plan["sl"]:
        s = "kena SL"
    elif lv["high"] >= plan["tp"]:
        s = "kena TP"
    else:
        s = "terpicu — berjalan"
    return {**lv, "status": s}


def _load_laptop(idx_dir: Path, now: datetime) -> dict:
    """Data resmi BEI yang dikirim runner laptop (bila ada)."""
    out = {}
    for name in ("screener", "suspend", "calendar", "announcements"):
        f = idx_dir / f"{name}.json"
        if f.exists():
            try:
                j = json.loads(f.read_text())
                age_h = (now - datetime.fromisoformat(j["fetched_at"])).total_seconds() / 3600
                out[name] = {"rows": j.get("results") or [], "fetched_at": j["fetched_at"], "age_h": round(age_h, 1)}
            except (ValueError, KeyError, json.JSONDecodeError):
                pass
    lr = idx_dir / "last_run.json"
    out["last_run"] = json.loads(lr.read_text()) if lr.exists() else None
    return out


def _stops(regime: dict, tuning: dict | None, picks: list) -> list[str]:
    s = []
    if regime.get("level") == "bad":
        s.append("IHSG sedang turun")
    if tuning and tuning.get("status") == "tidak_layak":
        s.append("Strategi merugi di uji terakhir")
    if not picks:
        s.append("Tidak ada saham yang lolos syarat")
    return s


def _demo_news(codes: list[str], now: datetime) -> list[dict]:
    titles = ["{c} umumkan dividen jumbo, cum date pekan depan", "Laba bersih {c} kuartal III naik 18%",
              "{c} raih kontrak baru senilai Rp1,2 triliun", "Saham {c} anjlok, investor asing lepas saham",
              "{c} rencanakan rights issue untuk ekspansi"]
    out = []
    for i, c in enumerate(codes[:8]):
        out.append({"code": c, "title": titles[i % len(titles)].format(c=c), "url": "https://example.com/demo",
                    "source": "Demo", "via": "Demo", "published": (now - timedelta(hours=6 + 9 * i)).isoformat()})
    return out


def build_all(out: Path, heavy: bool = False, demo: bool = False, cfg: dict | None = None) -> dict:
    t0 = time.time()
    cfg = cfg or load_cfg()
    s, risk, dcfg0 = cfg["strategy"], cfg["risk"], cfg["daytrade"]
    fee = risk["fee_buy_pct"] + risk["fee_sell_pct"]
    now = datetime.now(WIB)
    today = now.date()
    mstatus, progress = st.market_state(now, cfg["session"])
    market_open = mstatus in ("buka", "istirahat")
    store = Path(os.getenv("STORE", BASE / "cache"))
    store.mkdir(parents=True, exist_ok=True)
    idx_dir = store / "idx"
    errors: list[str] = []

    # ---------- 0. Data resmi BEI dari runner laptop (notasi, UMA, suspensi, kalender, pengumuman) ----------
    laptop = {} if demo else _load_laptop(idx_dir, now)
    scr = (laptop.get("screener") or {})
    sus = (laptop.get("suspend") or {})
    flags_idx = rk.idx_flags(scr.get("rows") if scr.get("age_h", 999) <= 96 else None,
                             sus.get("rows") if sus.get("age_h", 999) <= 96 else None, today)
    sector_map = {(r.get("stockCode") or "").strip(): r.get("sector") for r in scr.get("rows") or []}

    # ---------- 1. Watchlist + IHSG (Yahoo), lalu validasi data ----------
    tickers = cfg["watchlist"] + ["IHSG"]
    data, stamps = (datasrc.demo(tickers, now) if demo
                    else datasrc.fetch(tickers, "2y" if heavy else "1y", with_intraday=mstatus != "pra-buka"))
    ihsg = data.pop("IHSG", None)
    regime = st.market_regime(ihsg)
    ihsg_close = ihsg["Close"] if ihsg is not None else None
    if not data:
        errors.append("Data harga watchlist (Yahoo) gagal diunduh.")
    validation: dict[str, dict] = {}
    for t in list(data):
        clean, fatal, warn = rk.validate_frame(t, data[t], today, market_open)
        if fatal or warn:
            validation[t] = {"fatal": fatal, "warn": warn}
        if clean is None:
            data.pop(t)
        else:
            data[t] = clean
    invalid = {t for t, v in validation.items() if v["fatal"]}

    def flags_for(code: str) -> list[dict]:
        fl = list(flags_idx.get(code, []))
        v = validation.get(code)
        if v:
            fl += [{"kind": "data", "text": x, "block": True} for x in v["fatal"]]
            fl += [{"kind": "data", "text": x, "block": False} for x in v["warn"]]
        return fl

    rows, radars, checks, weekly = [], [], {}, {}
    for t, df in data.items():
        try:
            is_today = df.index[-1].date() == today
            prog = progress if is_today else 1.0
            d = st.add_indicators(df, s)
            weekly[t] = (bool(d["wk_up"].iloc[-1]), bool(d["wk_down"].iloc[-1]))
            ev = st.evaluate(t, d, s, risk, prog)
            ev["as_of"] = stamps.get(t, "")
            if ev["signal"] == "BUY" and regime["level"] == "bad":
                ev["reasons"].append("⚠ IHSG sedang turun — risiko gagal breakout lebih tinggi")
            fl = flags_for(t)
            if ev["signal"] == "BUY" and any(f["block"] for f in fl):
                ev["signal"], ev["plan"] = "WATCH", None
                ev["reasons"].append("⚠ Ada penanda risiko BEI — tidak disarankan dibeli")
            ev["flags"] = fl
            rows.append(ev)
            complete = df.iloc[:-1] if is_today and mstatus != "tutup" else df
            rd = st.radar(t, complete, s, risk)
            rd["price"], rd["change_pct"], rd["flags"] = ev["price"], ev["change_pct"], fl
            radars.append(rd)
            checks[t] = st.check_stock(t, df, s, risk, regime, prog)
        except Exception as e:
            errors.append(f"{t}: {type(e).__name__}")
    rows.sort(key=lambda r: (ORDER[r["signal"]], -r["rvol"]))
    radars.sort(key=lambda r: (-r["kandidat"], -r["score"]))

    # ---------- 2. Daftar Pagi (data resmi BEI, seluruh saham) ----------
    no_foreign = False
    if demo:
        end = (pd.Timestamp(today) - pd.offsets.BDay(1)).date() if mstatus != "tutup" else today
        panel, idx_err = idxsrc.demo_panel(dcfg0["history_days"], 150, end=end), []
    else:
        client = idxsrc.IdxClient(idx_dir / "summary")
        panel, idx_err = client.history(dcfg0["history_days"])
        if client.blocked and client.last_error:
            idx_err = [f"{e} ({client.last_error})" for e in idx_err[:1]]
    source = "BEI (idx.co.id)"
    if panel.empty:
        errors.append("Data BEI tidak dapat diunduh" + (f" [{idx_err[0]}]" if idx_err else "")
                      + "; Daftar Pagi memakai watchlist dari Yahoo tanpa data asing.")
        panel, no_foreign, source = yahoo_panel(data), True, "Yahoo Finance (watchlist, tanpa data asing)"
        if not panel.empty and mstatus != "tutup":
            panel = panel[panel["date"] < pd.Timestamp(today)]  # bar hari ini belum lengkap
    else:
        last_idx = panel["date"].max().date()
        expect = today if mstatus == "tutup" else (pd.Timestamp(today) - pd.offsets.BDay(1)).date()
        if last_idx < expect:
            errors.append(f"Data BEI terakhir {last_idx} (runner laptop belum mengirim data terbaru).")
        if mstatus != "tutup":
            panel = panel[panel["date"] < pd.Timestamp(today)]
    dcfg = {**dcfg0, "no_foreign": no_foreign}
    blocked = invalid | {c for c, fl in flags_idx.items() if any(f["block"] for f in fl)}

    opt, dt_res, dt_bt = None, {"date": None, "picks": [], "scanned": 0, "eligible": 0}, {"trades": 0}
    if not panel.empty:
        key = hashlib.md5(json.dumps([str(panel["date"].max()), dcfg, fee, panel["code"].nunique(), bool(ihsg is not None)],
                                     sort_keys=True, default=str).encode()).hexdigest()[:12]
        opt_dir = store / "opt"
        opt_file = opt_dir / f"{key}.json"
        if dcfg0.get("auto_tune", True):
            if opt_file.exists() and not demo:
                opt = json.loads(opt_file.read_text())
            else:
                opt = _clean(dtm.optimize(panel, dcfg, fee, ihsg=ihsg_close))
                if not demo:
                    opt_dir.mkdir(parents=True, exist_ok=True)
                    opt_file.write_text(json.dumps(opt))
                    for old in sorted(opt_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)[:-10]:
                        old.unlink()
        use = {**dcfg, **(opt["params"] if opt and opt.get("applied") else {})}
        dt_res = dtm.morning_list(panel, use, risk["capital_idr"], risk["risk_per_trade_pct"], fee,
                                  ihsg=ihsg_close, blocked=blocked)
        dt_bt = dtm.backtest(panel, use, fee, ihsg=ihsg_close)
        dt_res["params"] = {k: use.get(k) for k in ("tp_pct", "sl_pct", "min_score", "top_n", "min_value_idr", "rs_filter")}

    for_today = bool(dt_res["date"]) and dt_res["date"] < today.isoformat() and mstatus not in ("tutup", "libur akhir pekan")
    target = track.target_date(dt_res["date"], for_today, today)
    picks = dt_res["picks"]
    pdata: dict = {}
    if picks:
        codes = [p["code"] for p in picks]
        try:
            if demo:
                for k, p in enumerate(picks):  # harga simulasi di sekitar penutupan kemarin
                    r = np.random.default_rng(int(now.timestamp() // 60) + k)
                    o = p["close"] * (1 + r.normal(0.003, 0.008))
                    path = o * np.cumprod(1 + r.normal(0.0008, 0.006, 60))
                    idx_ = pd.bdate_range(end=pd.Timestamp(today), periods=80)
                    base = p["close"] * np.cumprod(1 + r.normal(0.001, 0.015, 80))
                    base = base / base[-2] * p["close"]
                    df = pd.DataFrame({"Open": base, "High": base * 1.01, "Low": base * 0.99, "Close": base,
                                       "Volume": 1e7}, index=idx_)
                    df.iloc[-1] = [o, max(path.max(), o), min(path.min(), o), path[-1], 5e6]
                    pdata[p["code"]] = df
            else:
                pdata, _ = datasrc.fetch(codes, "1y", with_intraday=mstatus != "pra-buka")
                for c in list(pdata):
                    clean, fatal, warn = rk.validate_frame(c, pdata[c], today, market_open)
                    if fatal or warn:
                        validation[c] = {"fatal": fatal, "warn": warn}
                    if clean is None:
                        pdata.pop(c)
                    else:
                        pdata[c] = clean
        except Exception:
            traceback.print_exc()
        for p in picks:
            df = pdata.get(p["code"])
            p["live"] = live_status(p["plan"], df, today) if for_today else None
            lim = rk.ar_limits(p["close"], datetime.fromisoformat(target).date())
            p["ar"] = lim
            fl = flags_for(p["code"])
            if p["plan"]["tp"] > lim["ara"]:
                fl.append({"kind": "ara", "text": f"TP {p['plan']['tp']} melewati batas ARA {lim['ara']}", "block": False})
            if p["plan"]["sl"] < lim["arb"]:
                fl.append({"kind": "arb", "text": f"SL {p['plan']['sl']} di bawah batas ARB {lim['arb']} — bisa tidak tereksekusi", "block": False})
            p["flags"] = fl
            if df is not None and p["code"] not in checks:
                try:
                    is_today = df.index[-1].date() == today
                    checks[p["code"]] = st.check_stock(p["code"], df, s, risk, regime, progress if is_today else 1.0)
                except Exception:
                    pass
    frames = {**pdata, **data}

    # ---------- 3. Kalender peristiwa (BEI dari laptop + Yahoo) ----------
    cal_file = store / "calendar_yahoo.json"
    ycal = {"events": []}
    try:
        if demo:
            ycal = {"events": [{"code": c, "date": str(today + timedelta(days=2 + i)), "kind": "Ex-date dividen", "source": "Demo"}
                               for i, c in enumerate(list(frames)[:3])]}
        else:
            if cal_file.exists():
                ycal = json.loads(cal_file.read_text())
            stale = (now - datetime.fromisoformat(ycal.get("fetched_at", "2000-01-01T00:00+07:00"))).total_seconds() > 20 * 3600
            if heavy or stale or set(frames) - set(ycal.get("codes", [])):
                ev_, cerr = news.yahoo_calendar(sorted(frames))
                ycal = {"fetched_at": now.isoformat(timespec="minutes"), "codes": sorted(frames), "events": ev_}
                cal_file.write_text(json.dumps(ycal))
                errors += cerr
    except Exception as e:
        errors.append(f"Kalender: {type(e).__name__}")
    events = ycal.get("events", []) + news.idx_calendar((laptop.get("calendar") or {}).get("rows"))
    coming = news.upcoming(events, today, days=10)
    soon = {}
    horizon = str(track.next_bday(track.next_bday(today)))
    for e in coming:
        if e["date"] <= horizon:
            soon.setdefault(e["code"], []).append(e)
    for p in picks:
        for e in soon.get(p["code"], []):
            p["flags"].append({"kind": "event", "text": f"{e['kind']} {e['date']}", "block": False})

    # ---------- 4. Analisis per saham ----------
    fund_file = store / "fundamentals.json"
    try:
        if demo:
            funds = {t: an.demo_fundamental(t) for t in frames}
        elif heavy or not fund_file.exists() or set(frames) - set(json.loads(fund_file.read_text())):
            funds = an.fetch_fundamentals(sorted(frames), fund_file)
        else:
            funds = json.loads(fund_file.read_text())
    except Exception:
        traceback.print_exc()
        funds = {}
        errors.append("Data fundamental gagal diunduh.")
    # sektor: utamakan data BEI, cadangan Yahoo
    sect = {t: sector_map.get(t) or (funds.get(t) or {}).get("sector") for t in frames}
    rets = {t: (df["Close"].iloc[-1] / df["Close"].iloc[-21] - 1) * 100 for t, df in frames.items() if len(df) > 21}
    sector_rs = {}
    for name in {v for v in sect.values() if v}:
        mem = [rets[t] for t, v in sect.items() if v == name and t in rets]
        sector_rs[name] = {"name": name, "n": len(mem), "avg_ret20": round(float(np.mean(mem)), 2) if mem else None}
    zmap, alist = {}, []

    # ---------- 5. Stream berita ----------
    news_dir = store / "news"
    news_dir.mkdir(parents=True, exist_ok=True)
    arch_file = news_dir / "archive.json"
    archive = json.loads(arch_file.read_text()) if arch_file.exists() else {}
    state_file = news_dir / "state.json"
    nstate = json.loads(state_file.read_text()) if state_file.exists() else {}
    names = {t: (funds.get(t) or {}).get("longName") for t in frames}
    fresh_items, nerr = [], []
    due = (now - datetime.fromisoformat(nstate.get("fetched_at", "2000-01-01T00:00+07:00"))).total_seconds() > 25 * 60
    try:
        if demo:
            fresh_items = _demo_news(sorted(frames), now)
        elif due or heavy:
            fresh_items, nerr = news.collect(sorted(frames), names, (laptop.get("announcements") or {}).get("rows"))
            nstate["fetched_at"] = now.isoformat(timespec="minutes")
            nstate["last_count"] = len(fresh_items)
    except Exception as e:
        nerr.append(f"Berita: {type(e).__name__} {str(e)[:60]}")
    errors += nerr
    enriched_new = news.enrich(fresh_items, frames)
    archive = news.merge_archive(archive, enriched_new)
    if not demo:
        arch_file.write_text(json.dumps(archive, ensure_ascii=False))
        state_file.write_text(json.dumps(nstate))
    week_ago = (now - timedelta(days=7)).isoformat()
    recent = news.enrich([dict(v) for v in archive.values() if v["published"] >= week_ago], frames)
    stats = news.event_stats(archive, frames)
    by_code: dict[str, list] = {}
    for it in recent:
        by_code.setdefault(it["code"], []).append(it)

    for t, df in frames.items():
        try:
            a = an.analyze(t, df, funds.get(t))
            wu, wd = weekly.get(t, (None, None))
            if t not in weekly:
                dd = st.add_indicators(df, s)
                wu, wd = bool(dd["wk_up"].iloc[-1]), bool(dd["wk_down"].iloc[-1])
            an.enrich_technical(a, wu, wd, an.rs_info(df, ihsg), sector_rs.get(sect.get(t)))
            nxt = track.next_bday(today) if mstatus == "tutup" else today
            ref = float(df["Close"].iloc[-1]) if mstatus == "tutup" else float(df["Close"].iloc[-2] if df.index[-1].date() == today else df["Close"].iloc[-1])
            a["ar"] = {**rk.ar_limits(ref, nxt), "for": str(nxt), "ref": ref}
            a["flags"] = flags_for(t)
            a["events"] = [e for e in coming if e["code"] == t]
            a["news"] = by_code.get(t, [])[:12]
            write(out, f"analysis/{t}", a)
            zmap[t] = an.card_zones(a)
            alist.append({"ticker": t, "name": (a["fundamental"] or {}).get("name"), "price": a["price"]})
        except Exception as e:
            errors.append(f"Analisis {t}: {type(e).__name__}")
    write(out, "analysis/index", sorted(alist, key=lambda x: x["ticker"]))
    fund_ok = sum(1 for t in frames if an.fundamental(funds.get(t), 1.0).get("available"))
    coverage = {"analysis": len(alist), "fundamental": fund_ok, "of": len(frames), "news_7d": len(recent),
                "news_archive": len(archive), "flags_idx": len(flags_idx)}
    for r in rows:
        r["zones"] = zmap.get(r["ticker"])
    for r in radars:
        r["zones"] = zmap.get(r["ticker"])
    for p in picks:
        p["zones"] = zmap.get(p["code"])
        p["news"] = by_code.get(p["code"], [])[:3]
    for t, c in checks.items():
        c["zones"] = zmap.get(t)
        c["flags"] = flags_for(t)

    tuning = None
    if opt:
        tuning = {k: opt.get(k) for k in ("status", "applied", "days", "train_days", "test_days", "params",
                                          "train_avg", "train_trades", "note")}
        tuning["test"] = {k: (opt.get("test") or {}).get(k) for k in ("trades", "win_rate", "avg_pct", "total_pct")}
    stops = _stops(regime, tuning, picks)

    # ---------- 6. Track record nyata ----------
    trk = {"days": [], "summary": {}}
    try:
        if dt_res["date"]:
            track.save_snapshot(store, target, now, mstatus, dt_res, tuning, regime, stops)
        need = track.codes_to_evaluate(store, today) - set(frames)
        bars = dict(frames)
        if need and not demo:
            more, _ = datasrc.fetch(sorted(need), "3mo", with_intraday=False)
            bars.update(more)
        trk = track.evaluate_all(store, bars, today, mstatus == "tutup", fee)
    except Exception as e:
        traceback.print_exc()
        errors.append(f"Track record: {type(e).__name__}")

    # ---------- 7. Notifikasi ntfy ----------
    if not demo:
        try:
            once = notify.Once(store)
            url = os.getenv("SITE_URL", "")
            if for_today and mstatus in ("pra-buka", "buka") and dt_res["date"]:
                body = ("JANGAN TRADING: " + ", ".join(stops)) if stops else \
                    "\n".join(f"{p['code']}: beli ≥ {p['plan']['trigger']}, SL {p['plan']['sl']}, TP {p['plan']['tp']}" for p in picks)
                once(f"pagi-{target}", f"Daftar Pagi {target}", body, tags="sunrise", click=url)
            for p in picks:
                lv = p.get("live")
                if lv and not stops and lv["status"] in ("terpicu — berjalan", "kena TP", "kena SL"):
                    tag = {"kena TP": "white_check_mark", "kena SL": "x"}.get(lv["status"], "rocket")
                    once(f"live-{today}-{p['code']}-{lv['status']}", f"{p['code']}: {lv['status']}",
                         f"Last {lv['last']:.0f} · SL {p['plan']['sl']} · TP {p['plan']['tp']}", tags=tag,
                         priority="high", click=url)
            lr = laptop.get("last_run")
            if lr and (now - datetime.fromisoformat(lr["at"])).total_seconds() > 2 * 86400 and now.weekday() < 5:
                once(f"laptop-{today}", "Data BEI belum diperbarui", "Runner laptop belum mengirim data lebih dari 2 hari. "
                     "Pastikan laptop menyala dan runner aktif.", tags="warning")
            once.save()
        except Exception as e:
            errors.append(f"Notifikasi: {type(e).__name__}")

    # ---------- 8. Tulis semua file ----------
    stamp = now.strftime("%Y-%m-%d %H:%M WIB")
    meta = {"generated_at": stamp, "market": {"status": mstatus, "progress": round(progress, 3)},
            "demo": demo, "errors": errors, "build_seconds": None, "risk": risk,
            "watchlist": cfg["watchlist"], "session": cfg["session"],
            "validation": validation, "laptop": {k: {kk: vv for kk, vv in (v or {}).items() if kk != "rows"}
                                                  for k, v in laptop.items() if k != "last_run"} | {"last_run": laptop.get("last_run")},
            "ar_rules": rk.ar_pct(1000, today)[2]}
    write(out, "signals", {"generated_at": stamp, "market": meta["market"], "regime": regime, "demo": demo,
                           "counts": {k: sum(r["signal"] == k for r in rows) for k in ORDER},
                           "signals": rows, "radar": radars, "radar_min": st.RADAR_MIN,
                           "missing": sorted(set(cfg["watchlist"]) - set(data)), "risk": risk,
                           "watchlist": sorted(set(cfg["watchlist"]) | set(checks)), "server_error": None})
    write(out, "check", {"generated_at": stamp, "regime": regime, "stocks": checks})
    write(out, "daytrade", {**dt_res, "for_today": for_today, "target": target, "market": mstatus, "regime": regime,
                            "tuning": tuning, "dt_backtest": dt_bt, "source": source, "no_foreign": no_foreign,
                            "stops": stops, "demo": demo, "fee_total_pct": fee, "updated": stamp,
                            "error": "; ".join(e for e in errors if "BEI" in e) or None})
    write(out, "stream", {"generated_at": stamp, "items": recent, "upcoming": coming, "stats": stats,
                          "min_n": 10, "archive_size": len(archive), "fetched_at": nstate.get("fetched_at")})
    write(out, "track", {"generated_at": stamp, **trk})

    saved = store / "backtest.json"
    if heavy:
        try:
            trades = []
            for t, df in data.items():
                trades += st.backtest_ticker(t, df, cfg)
            trades.sort(key=lambda x: x.entry_date)
            write(out, "backtest", {"summary": st.summarize(trades), "tickers": len(data),
                                    "radar": st.radar_validation(data, s), "period": cfg["backtest"]["period"],
                                    "trades": [asdict(x) for x in trades[-200:]], "generated_at": stamp})
            shutil.copy(out / "data" / "backtest.json", saved)
        except Exception as e:
            errors.append(f"Backtest swing gagal: {type(e).__name__}")
    elif saved.exists():
        shutil.copy(saved, out / "data" / "backtest.json")
    elif not (out / "data" / "backtest.json").exists():
        write(out, "backtest", {"pending": True})

    meta["build_seconds"] = round(time.time() - t0, 1)
    meta["coverage"] = coverage
    meta["errors"] = errors
    write(out, "meta", meta)
    return meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    ap.add_argument("--heavy", action="store_true", help="sertakan backtest swing 2 tahun")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy(BASE / "static" / "index.html", out / "index.html")
    shutil.copytree(BASE / "static" / "vendor", out / "vendor", dirs_exist_ok=True)
    (out / ".nojekyll").write_text("")
    meta = build_all(out, heavy=a.heavy, demo=os.getenv("DEMO") == "1")
    print(json.dumps(meta, ensure_ascii=False, indent=1)[:1500])


if __name__ == "__main__":
    main()
