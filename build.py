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
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import data as datasrc
import daytrade as dtm
import idx as idxsrc
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


def build_all(out: Path, heavy: bool = False, demo: bool = False, cfg: dict | None = None) -> dict:
    t0 = time.time()
    cfg = cfg or load_cfg()
    s, risk, dcfg0 = cfg["strategy"], cfg["risk"], cfg["daytrade"]
    fee = risk["fee_buy_pct"] + risk["fee_sell_pct"]
    now = datetime.now(WIB)
    mstatus, progress = st.market_state(now, cfg["session"])
    cache = Path(os.getenv("IDX_CACHE", BASE / "cache" / "idx"))
    errors: list[str] = []

    # ---------- 1. Watchlist + IHSG (Yahoo) ----------
    tickers = cfg["watchlist"] + ["IHSG"]
    data, stamps = (datasrc.demo(tickers, now) if demo
                    else datasrc.fetch(tickers, "2y" if heavy else "1y", with_intraday=mstatus != "pra-buka"))
    ihsg = data.pop("IHSG", None)
    regime = st.market_regime(ihsg)
    if not data:
        errors.append("Data harga watchlist (Yahoo) gagal diunduh.")

    rows, radars, checks = [], [], {}
    for t, df in data.items():
        try:
            is_today = df.index[-1].date() == now.date()
            prog = progress if is_today else 1.0
            ev = st.evaluate(t, st.add_indicators(df, s), s, risk, prog)
            ev["as_of"] = stamps.get(t, "")
            if ev["signal"] == "BUY" and regime["level"] == "bad":
                ev["reasons"].append("⚠ IHSG sedang turun — risiko gagal breakout lebih tinggi")
            rows.append(ev)
            complete = df.iloc[:-1] if is_today and mstatus != "tutup" else df
            rd = st.radar(t, complete, s, risk)
            rd["price"], rd["change_pct"] = ev["price"], ev["change_pct"]
            radars.append(rd)
            checks[t] = st.check_stock(t, df, s, risk, regime, prog)
        except Exception as e:
            errors.append(f"{t}: {type(e).__name__}")
    rows.sort(key=lambda r: (ORDER[r["signal"]], -r["rvol"]))
    radars.sort(key=lambda r: (-r["kandidat"], -r["score"]))

    # ---------- 2. Daftar Pagi (data resmi BEI, seluruh saham) ----------
    no_foreign = False
    if demo:
        end = (pd.Timestamp(now.date()) - pd.offsets.BDay(1)).date() if mstatus != "tutup" else now.date()
        panel, idx_err = idxsrc.demo_panel(dcfg0["history_days"], 150, end=end), []
    else:
        panel, idx_err = idxsrc.IdxClient(cache).history(dcfg0["history_days"])
    source = "BEI (idx.co.id)"
    if panel.empty:
        errors.append("Data BEI tidak dapat diunduh; Daftar Pagi memakai watchlist dari Yahoo tanpa data asing.")
        panel, no_foreign, source = yahoo_panel(data), True, "Yahoo Finance (watchlist, tanpa data asing)"
    elif idx_err:
        errors.append(f"Sebagian tanggal BEI gagal diunduh: {', '.join(idx_err[:5])}")
    dcfg = {**dcfg0, "no_foreign": no_foreign}

    opt, dt_res, dt_bt = None, {"date": None, "picks": [], "scanned": 0, "eligible": 0}, {"trades": 0}
    if not panel.empty:
        # penyetelan otomatis disimpan per tanggal data agar tidak dihitung ulang tiap 15 menit
        key = hashlib.md5(json.dumps([str(panel["date"].max()), dcfg, fee, panel["code"].nunique()],
                                     sort_keys=True, default=str).encode()).hexdigest()[:12]
        opt_file = cache.parent / f"opt-{key}.json"
        if dcfg0.get("auto_tune", True):
            if opt_file.exists() and not demo:
                opt = json.loads(opt_file.read_text())
            else:
                opt = _clean(dtm.optimize(panel, dcfg, fee))
                if not demo:
                    opt_file.parent.mkdir(parents=True, exist_ok=True)
                    opt_file.write_text(json.dumps(opt))
        use = {**dcfg, **(opt["params"] if opt and opt.get("applied") else {})}
        dt_res = dtm.morning_list(panel, use, risk["capital_idr"], risk["risk_per_trade_pct"], fee)
        dt_bt = dtm.backtest(panel, use, fee)
        dt_res["params"] = {k: use[k] for k in ("tp_pct", "sl_pct", "min_score", "top_n", "min_value_idr")}

    for_today = bool(dt_res["date"]) and dt_res["date"] < now.date().isoformat() \
        and mstatus not in ("tutup", "libur akhir pekan")
    picks = dt_res["picks"]
    if picks:
        codes = [p["code"] for p in picks]
        pdata = {}
        try:
            if demo:
                for k, p in enumerate(picks):  # harga simulasi di sekitar penutupan kemarin
                    r = np.random.default_rng(int(now.timestamp() // 60) + k)
                    o = p["close"] * (1 + r.normal(0.003, 0.008))
                    path = o * np.cumprod(1 + r.normal(0.0008, 0.006, 60))
                    idx_ = pd.bdate_range(end=pd.Timestamp(now.date()), periods=80)
                    base = p["close"] * np.cumprod(1 + r.normal(0.001, 0.015, 80))
                    base = base / base[-2] * p["close"]
                    df = pd.DataFrame({"Open": base, "High": base * 1.01, "Low": base * 0.99, "Close": base,
                                       "Volume": 1e7}, index=idx_)
                    df.iloc[-1] = [o, max(path.max(), o), min(path.min(), o), path[-1], 5e6]
                    pdata[p["code"]] = df
            else:
                pdata, _ = datasrc.fetch(codes, "1y", with_intraday=mstatus != "pra-buka")
        except Exception:
            traceback.print_exc()
        for p in picks:
            df = pdata.get(p["code"])
            p["live"] = live_status(p["plan"], df, now.date()) if for_today else None
            if df is not None and p["code"] not in checks:
                try:
                    is_today = df.index[-1].date() == now.date()
                    checks[p["code"]] = st.check_stock(p["code"], df, s, risk, regime, progress if is_today else 1.0)
                except Exception:
                    pass

    stamp = now.strftime("%Y-%m-%d %H:%M WIB")
    meta = {"generated_at": stamp, "market": {"status": mstatus, "progress": round(progress, 3)},
            "demo": demo, "errors": errors, "build_seconds": None, "risk": risk,
            "watchlist": cfg["watchlist"], "session": cfg["session"]}

    write(out, "signals", {"generated_at": stamp, "market": meta["market"], "regime": regime, "demo": demo,
                           "counts": {k: sum(r["signal"] == k for r in rows) for k in ORDER},
                           "signals": rows, "radar": radars, "radar_min": st.RADAR_MIN,
                           "missing": sorted(set(cfg["watchlist"]) - set(data)), "risk": risk,
                           "watchlist": sorted(set(cfg["watchlist"]) | set(checks)), "server_error": None})
    write(out, "check", {"generated_at": stamp, "regime": regime, "stocks": checks})
    tuning = None
    if opt:
        tuning = {k: opt.get(k) for k in ("status", "applied", "days", "train_days", "test_days", "params",
                                          "train_avg", "train_trades", "note")}
        tuning["test"] = {k: (opt.get("test") or {}).get(k) for k in ("trades", "win_rate", "avg_pct", "total_pct")}
    write(out, "daytrade", {**dt_res, "for_today": for_today, "market": mstatus, "regime": regime,
                            "tuning": tuning, "dt_backtest": dt_bt, "source": source, "no_foreign": no_foreign,
                            "demo": demo, "fee_total_pct": fee, "updated": stamp,
                            "error": "; ".join(e for e in errors if "BEI" in e) or None})

    if heavy:
        try:
            trades = []
            for t, df in data.items():
                trades += st.backtest_ticker(t, df, cfg)
            trades.sort(key=lambda x: x.entry_date)
            write(out, "backtest", {"summary": st.summarize(trades), "tickers": len(data),
                                    "radar": st.radar_validation(data, s), "period": cfg["backtest"]["period"],
                                    "trades": [asdict(x) for x in trades[-200:]], "generated_at": stamp})
        except Exception as e:
            errors.append(f"Backtest swing gagal: {type(e).__name__}")
    # hasil backtest swing (dihitung sekali sehari) disimpan di cache agar tetap tampil di build berikutnya
    saved = cache.parent / "backtest.json"
    if heavy and (out / "data" / "backtest.json").exists():
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(out / "data" / "backtest.json", saved)
    elif not heavy:
        if saved.exists():
            shutil.copy(saved, out / "data" / "backtest.json")
        elif not (out / "data" / "backtest.json").exists():
            write(out, "backtest", {"pending": True})

    meta["build_seconds"] = round(time.time() - t0, 1)
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
    (out / ".nojekyll").write_text("")
    meta = build_all(out, heavy=a.heavy, demo=os.getenv("DEMO") == "1")
    print(json.dumps(meta, ensure_ascii=False, indent=1)[:1500])


if __name__ == "__main__":
    main()
