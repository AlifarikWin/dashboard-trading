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
import universe as uni
import bsjp
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


def _read_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    except (ValueError, OSError):
        return default


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
    if data:
        for t in cfg["watchlist"]:
            if t not in data:
                validation[t] = {"fatal": ["Yahoo tidak mengirim cukup data harga"], "warn": []}
    for t in list(data):
        clean, fatal, warn = rk.validate_frame(t, data[t], today, market_open)
        if fatal or warn:
            validation[t] = {"fatal": fatal, "warn": warn}
        if clean is None:
            data.pop(t)
        else:
            data[t] = clean
    invalid = {t for t, v in validation.items() if v["fatal"]}

    uflags: dict[str, list] = {}

    def flags_for(code: str) -> list[dict]:
        fl = list(flags_idx.get(code, [])) + list(uflags.get(code, []))
        v = validation.get(code)
        if v:
            fl += [{"kind": "data", "text": x, "block": True} for x in v["fatal"]]
            fl += [{"kind": "data", "text": x, "block": False} for x in v["warn"]]
        return fl

    # ---------- 1b. Seluruh emiten (Yahoo): dihitung sekali per hari bursa, disimpan di cache ----------
    all_dir = Path(os.getenv("ALL_CACHE", BASE / "cache_all"))
    all_dir.mkdir(parents=True, exist_ok=True)
    ustate = _read_json(all_dir / "state.json", {})
    last_close = None
    if ihsg is not None and len(ihsg):
        li = ihsg.index[-1].date()
        last_close = ihsg.index[-2].date() if (li == today and mstatus != "tutup" and len(ihsg) > 1) else li
    fund_file = store / "fundamentals.json"
    div_file = store / "dividends.json"
    div_stats = _read_json(div_file, {}).get("stats", {})
    need_univ = (bool(ustate.get("demo")) != demo or not ustate.get("date") or (last_close and ustate["date"] < str(last_close))
                 or not (all_dir / "universe.json").exists() or os.getenv("UNIVERSE_REBUILD") == "1")
    if need_univ:
        try:
            tu = time.time()
            lst_file = all_dir / "listing.json"
            lst = _read_json(lst_file, {})
            if demo:
                lst = {"fetched_at": now.isoformat(), "rows": uni.demo_list(sorted(set(cfg["watchlist"]) | {f"DM{i:02d}" for i in range(40)}))}
            elif not lst.get("rows") or (now - datetime.fromisoformat(lst["fetched_at"])).days >= 7:
                rows_ = uni.fetch_list()
                if len(rows_) > 300:  # jangan timpa daftar lama dengan hasil yang jelas tidak lengkap
                    lst = {"fetched_at": now.isoformat(timespec="minutes"), "rows": rows_}
            listing = lst.get("rows") or {}
            if listing:
                lst_file.write_text(json.dumps(lst, ensure_ascii=False), encoding="utf-8")
                codes_all = sorted(listing)
                if demo:
                    ufr, _ = datasrc.demo(codes_all, now)
                else:
                    ufr = uni.download(codes_all)
                    for t, df in data.items():  # watchlist sudah diunduh lebih lengkap
                        ufr.setdefault(t, df)
                ufr = {c: (df[df.index.date < today] if mstatus != "tutup" else df) for c, df in ufr.items()}
                ufr = {c: df for c, df in ufr.items() if len(df) >= 60}
                if not demo:
                    try:
                        detail = an.fetch_fundamentals(codes_all, fund_file, limit=150 if heavy else 0, priority=cfg["watchlist"])
                    except Exception:
                        detail = _read_json(fund_file, {})
                    dv = _read_json(div_file, {})
                    if heavy and (not dv.get("fetched_at") or (now - datetime.fromisoformat(dv["fetched_at"])).days >= 7):
                        try:
                            st_ = uni.fetch_dividend_stats(codes_all)
                            if st_:
                                dv = {"fetched_at": now.isoformat(timespec="minutes"), "stats": st_}
                                div_file.write_text(json.dumps(dv), encoding="utf-8")
                        except Exception:
                            traceback.print_exc()
                    div_stats = dv.get("stats", {})
                else:
                    detail, div_stats = {}, {c: {"n": 5, "up": 4, "avg_run": 2.4, "avg_drop": -3.1, "pre_days": 10, "events": []}
                                             for c in codes_all[:10]}
                nxt_u = track.next_bday(last_close or today)
                ustate = uni.build(all_dir, listing, ufr, ihsg, cfg, regime, today, detail, div_stats, nxt_u)
                ustate["seconds"] = round(time.time() - tu, 1)
                ustate["demo"] = demo  # cache demo tidak boleh dipakai oleh build data asli
                try:  # BSJP: uji 1 tahun, daftar final dari bar terakhir, track record
                    ublk = {r["c"] for r in _read_json(all_dir / "universe.json", {"rows": []})["rows"] if r.get("blk")}
                    bstats, bbase = bsjp.evaluate_history(ufr, fee)
                    bfinal = bsjp.candidates_daily(ufr, {c: v.get("name") for c, v in listing.items()}, ublk,
                                                   last_close or today)
                    (all_dir / "bsjp.json").write_text(json.dumps(uni._clean({"stats": bstats, "base": bbase, "final": bfinal}),
                                                                  ensure_ascii=False), encoding="utf-8")
                    if not demo:
                        bsjp.save_snapshot(store, bfinal, bstats.get("status"))
                        bsjp.evaluate_snapshots(store, ufr, fee)
                except Exception as e:
                    traceback.print_exc()
                    errors.append(f"BSJP: {type(e).__name__}")
                (all_dir / "state.json").write_text(json.dumps(ustate))
                (all_dir / ".updated").write_text(now.isoformat())
        except Exception as e:
            traceback.print_exc()
            errors.append(f"Data seluruh emiten gagal diperbarui: {type(e).__name__}")
    uindex = _read_json(all_dir / "universe.json", {"rows": []})
    uflags = _read_json(all_dir / "flags.json", {})
    usector = _read_json(all_dir / "sector_rs.json", {})
    if not uindex["rows"]:
        errors.append("Daftar seluruh emiten belum tersedia; pencarian terbatas pada watchlist.")

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
    lr0 = laptop.get("last_run") or {}
    use_idx = bool(lr0.get("ok")) and (now - datetime.fromisoformat(lr0["at"])).days <= 4
    panel, idx_err = pd.DataFrame(), []
    if demo:
        end = (pd.Timestamp(today) - pd.offsets.BDay(1)).date() if mstatus != "tutup" else today
        panel = idxsrc.demo_panel(dcfg0["history_days"], 150, end=end)
    elif use_idx:  # data resmi BEI (bila suatu saat tersedia dari runner laptop)
        client = idxsrc.IdxClient(idx_dir / "summary")
        panel, idx_err = client.history(dcfg0["history_days"])
    source = "BEI (idx.co.id)"
    if panel.empty:
        panel, no_foreign, source = uni.load_panel(all_dir), True, "Yahoo Finance (seluruh bursa, tanpa data asing)"
        if panel.empty:
            panel, source = yahoo_panel(data), "Yahoo Finance (watchlist, tanpa data asing)"
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
    blocked = invalid | {c for c, fl in flags_idx.items() if any(f["block"] for f in fl)} \
        | {r["c"] for r in uindex["rows"] if r.get("blk")}

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
    for e in coming:
        dsx = div_stats.get(e["code"])
        if dsx and "dividen" in e["kind"].lower():
            e["pattern"] = {k: dsx[k] for k in ("n", "up", "avg_run", "avg_drop", "pre_days")}
    soon = {}
    horizon = str(track.next_bday(track.next_bday(today)))
    for e in coming:
        if e["date"] <= horizon:
            soon.setdefault(e["code"], []).append(e)
    for p in picks:
        for e in soon.get(p["code"], []):
            p["flags"].append({"kind": "event", "text": f"{e['kind']} {e['date']}", "block": False})

    # ---------- 4. Analisis per saham ----------
    try:
        if demo:
            funds = {t: an.demo_fundamental(t) for t in frames}
        elif not fund_file.exists() or set(frames) - set(json.loads(fund_file.read_text())):
            funds = an.fetch_fundamentals(sorted(frames), fund_file)
        else:
            funds = json.loads(fund_file.read_text())
    except Exception:
        traceback.print_exc()
        funds = {}
        errors.append("Data fundamental gagal diunduh.")
    # sektor: utamakan data BEI, lalu daftar emiten Yahoo, lalu data fundamental rinci
    listing = _read_json(all_dir / "listing.json", {}).get("rows", {})
    sect = {t: sector_map.get(t) or (listing.get(t) or {}).get("sector") or (funds.get(t) or {}).get("sector") for t in frames}
    rets = {t: (df["Close"].iloc[-1] / df["Close"].iloc[-21] - 1) * 100 for t, df in frames.items() if len(df) > 21}
    sector_rs = {}
    for name in {v for v in sect.values() if v}:
        mem = [rets[t] for t, v in sect.items() if v == name and t in rets]
        sector_rs[name] = {"name": name, "n": len(mem), "avg_ret20": round(float(np.mean(mem)), 2) if mem else None}
    if usector:  # median seluruh saham di sektor yang sama (lebih bermakna daripada watchlist saja)
        sector_rs.update(usector)
    zmap, alist = {}, []
    # salin analisis harian seluruh emiten; watchlist & Daftar Pagi ditimpa versi terbaru di bawah
    if (all_dir / "analysis").exists():
        shutil.copytree(all_dir / "analysis", out / "data" / "analysis", dirs_exist_ok=True)

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
    stories = news.summarize(recent, frames)
    stories_by: dict[str, list] = {}
    for st_ in stories:
        stories_by.setdefault(st_["code"], []).append(st_)
    stats = news.event_stats(archive, frames)
    by_code: dict[str, list] = {}
    for it in recent:
        by_code.setdefault(it["code"], []).append(it)

    for t, df in frames.items():
        try:
            info = listing.get(t)
            a = an.analyze(t, df, uni.merge_fund(uni.basic_fund(info) if info else None, funds.get(t)))
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
            a["stories"] = stories_by.get(t, [])[:6]
            a["name"] = (info or {}).get("name") or (a["fundamental"] or {}).get("name")
            a["dividend"] = div_stats.get(t)
            a["scope"] = "intraday"
            zc = an.card_zones(a)
            uni.compact_ohlc(a)
            write(out, f"analysis/{t}", a)
            zmap[t] = zc
            alist.append({"ticker": t, "name": (a["fundamental"] or {}).get("name"), "price": a["price"]})
        except Exception as e:
            errors.append(f"Analisis {t}: {type(e).__name__}")
    write(out, "analysis/index", sorted(alist, key=lambda x: x["ticker"]))
    # daftar seluruh emiten untuk pencarian & menu Semua Saham (harga watchlist diperbarui intraday)
    urows = {r["c"]: r for r in uindex["rows"]}
    for t, df in frames.items():
        if t in urows and len(df) > 1:
            urows[t] = {**urows[t], "p": float(df["Close"].iloc[-1]),
                        "ch": round(float(df["Close"].iloc[-1] / df["Close"].iloc[-2] - 1) * 100, 2), "d": str(df.index[-1].date())}
    # gerak hari ini (harga terkini seluruh emiten): saat bursa buka, istirahat, dan setelah tutup
    mv = {"market": mstatus, "updated": None, "items": [], "delay_min": None}
    qs = {}
    if uindex["rows"]:
        try:
            qs = uni.demo_quotes(uindex["rows"], int(now.timestamp() // 900)) if demo else uni.live_quotes()
            mv["items"] = uni.movers(uindex["rows"], qs, today, progress if mstatus in ("buka", "istirahat") else 1.0)
            mv["session"] = mv["items"][0]["d"] if mv["items"] else None
            dl = [q.get("delay") for q in qs.values() if q.get("delay") is not None]
            mv["delay_min"] = int(np.median(dl)) if dl else None
            mv["updated"] = now.strftime("%H:%M")
        except Exception as e:
            traceback.print_exc()
            errors.append(f"Gerak hari ini: {type(e).__name__}")
    write(out, "movers", mv)

    # BSJP: daftar sementara menjelang penutupan, daftar final setelah tutup, pemantauan besok pagi
    bj = _read_json(all_dir / "bsjp.json", {})
    final = bj.get("final") or {}
    hm = now.hour * 60 + now.minute
    live_session = mv.get("session") == str(today)
    bs = {"stats": bj.get("stats"), "final": final, "phase": "final", "provisional": None, "monitor": None,
          "market": mstatus, "updated": now.strftime("%H:%M"), "delay_min": mv.get("delay_min")}
    try:
        names = {r["c"]: r["n"] for r in uindex["rows"]}
        ublk = {r["c"] for r in uindex["rows"] if r.get("blk")}
        if mstatus in ("buka", "istirahat") and hm >= 15 * 60 and qs and live_session and bj.get("base"):
            bs["phase"] = "sore"
            bs["provisional"] = bsjp.candidates_live(qs, bj["base"], names, ublk, progress, today)
        elif final.get("date") and final["date"] < str(today) and mstatus in ("buka", "istirahat") and live_session:
            bs["phase"] = "pagi"
            bs["monitor"] = bsjp.monitor(final.get("up", []), qs)
        elif final.get("date") and final["date"] < str(today) and mstatus == "pra-buka":
            bs["phase"] = "pra-buka"
        bs["track"] = bsjp.track_summary(store)
    except Exception as e:
        traceback.print_exc()
        errors.append(f"BSJP: {type(e).__name__}")
    write(out, "bsjp", bs)
    write(out, "universe", {"date": uindex.get("date"), "rows": sorted(urows.values(), key=lambda r: r["c"]),
                            "watchlist": cfg["watchlist"], "state": ustate, "div": {c: [v["up"], v["n"], v["avg_run"]] for c, v in div_stats.items()}})
    fund_ok = sum(1 for t in frames if an.fundamental(funds.get(t), 1.0).get("available"))
    coverage = {"analysis": len(alist), "fundamental": fund_ok, "of": len(frames), "universe": len(urows), "news_7d": len(recent),
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
            prov = (bs.get("provisional") or {}).get("up") or []
            if bs.get("phase") == "sore" and 15 * 60 + 25 <= hm < 15 * 60 + 58:
                stat = (bs.get("stats") or {}).get("status")
                head = "" if stat == "layak" else "UJI HISTORIS BELUM POSITIF — jangan pakai uang sungguhan.\n"
                dl = f"±{mv['delay_min']} menit" if mv.get("delay_min") else "tertunda"
                body = head + (f"Data harga {dl}. Beli 15.50–16.00, jual besok 09.00–09.15:\n" +
                               "\n".join(f"{p['c']} ≤ {p['plan']['buy_limit']:,.0f} ({p['chg']:+.1f}%)".replace(",", ".") for p in prov)
                               if prov else "Tidak ada saham yang memenuhi syarat BSJP sore ini.")
                key = "bsjp1" if hm < 15 * 60 + 40 else "bsjp2"
                codes = ",".join(p["c"] for p in prov)
                prev_codes = once.sent.get(f"bsjp-codes-{today}", "|").split("|", 1)[1]
                if key == "bsjp1" or codes != prev_codes:
                    once(f"{key}-{today}", f"BSJP sementara {now:%H:%M}", body, tags="hourglass", click=url + "#besok")
                once.sent[f"bsjp-codes-{today}"] = f"{now.isoformat(timespec='minutes')}|{codes}"
            if now.weekday() == 0 and mstatus in ("pra-buka", "buka"):
                title, body = track.weekly_report(trk.get("days", []), today, (tuning or {}).get("status"),
                                                  regime.get("status"), coming)
                once(f"senin-{today}", title, body, tags="calendar", click=url)
            for p in picks:
                lv = p.get("live")
                if lv and not stops and lv["status"] in ("terpicu — berjalan", "kena TP", "kena SL"):
                    tag = {"kena TP": "white_check_mark", "kena SL": "x"}.get(lv["status"], "rocket")
                    once(f"live-{today}-{p['code']}-{lv['status']}", f"{p['code']}: {lv['status']}",
                         f"Last {lv['last']:.0f} · SL {p['plan']['sl']} · TP {p['plan']['tp']}", tags=tag,
                         priority="high", click=url)
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
    write(out, "stream", {"generated_at": stamp, "items": recent, "stories": stories, "upcoming": coming, "stats": stats,
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
    meta["universe"] = ustate
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
    # salin seluruh file aplikasi (halaman, PWA, library) — folder data dibuat oleh build
    shutil.copytree(BASE / "static", out, dirs_exist_ok=True, ignore=shutil.ignore_patterns("data"))
    (out / ".nojekyll").write_text("")
    meta = build_all(out, heavy=a.heavy, demo=os.getenv("DEMO") == "1")
    print(json.dumps({k: meta.get(k) for k in ("generated_at", "build_seconds", "errors", "coverage", "universe")},
                     ensure_ascii=False, indent=1))
    # ringkasan pemeriksaan hasil (tampil di log Actions)
    try:
        u = json.loads((out / "data" / "universe.json").read_text(encoding="utf-8"))
        rows = u["rows"]
        adir = out / "data" / "analysis"
        files = list(adir.glob("*.json"))
        kinds: dict = {}
        for r in rows:
            for k in r["fl"]:
                kinds[k] = kinds.get(k, 0) + 1
        fund = sum(1 for r in rows if r.get("pe") or r.get("roe"))
        print(json.dumps({"universe_rows": len(rows), "analysis_files": len(files),
                          "analysis_mb": round(sum(f.stat().st_size for f in files) / 1e6, 1),
                          "blocked": sum(r["blk"] for r in rows), "flags": kinds, "with_fundamental": fund,
                          "with_dividend_pattern": len(u.get("div", {})),
                          "bsjp": (lambda b: {"phase": b.get("phase"), "status": (b.get("stats") or {}).get("status"),
                                              "up": (b.get("stats") or {}).get("up"), "recent": (b.get("stats") or {}).get("up_recent"),
                                              "baseline": (b.get("stats") or {}).get("baseline"), "down": (b.get("stats") or {}).get("down"),
                                              "final": [(x["c"], x["chg"]) for x in (b.get("final") or {}).get("up", [])],
                                              "final_date": (b.get("final") or {}).get("date")})(
                              json.loads((out / "data" / "bsjp.json").read_text(encoding="utf-8"))),
                          "movers": (lambda m: {"items": len(m["items"]), "session": m.get("session"), "delay_min": m.get("delay_min"),
                                                "top": [(x["c"], x["ch"]) for x in m["items"][:3]]})(
                              json.loads((out / "data" / "movers.json").read_text(encoding="utf-8"))), "sectors": len({r["s"] for r in rows if r["s"]}),
                          "sample": next((r for r in rows if r["c"] == "TLKM"), rows[0] if rows else None)},
                         ensure_ascii=False, indent=1))
    except Exception as e:
        print("verifikasi universe:", type(e).__name__, e)


if __name__ == "__main__":
    main()
