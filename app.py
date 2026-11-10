"""
Dashboard sinyal saham BEI (Breakout + Volume).

Jalankan:  uvicorn app:app --host 0.0.0.0 --port 8000
Env opsional:
  DASHBOARD_PASSWORD  -> aktifkan login (username bebas, password ini)
  DEMO=1              -> pakai data sintetis (tanpa internet), untuk mencoba tampilan
  BOT_CONFIG          -> path config.json alternatif
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
import traceback
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

import pandas as pd

import data as datasrc
import daytrade as dtm
import idx as idxsrc
import strategy as st
from data import WIB

BASE = Path(__file__).resolve().parent
CFG = json.loads(Path(os.getenv("BOT_CONFIG", BASE / "config.json")).read_text(encoding="utf-8"))
DEMO = os.getenv("DEMO") == "1"
PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")

ORDER = {"BUY": 0, "SELL": 1, "WATCH": 2, "NETRAL": 3}

_cache: dict = {"payload": None, "ts": 0.0, "error": None}
_bt_cache: dict = {"payload": None, "ts": 0.0, "running": False}
_lock = threading.Lock()


def build_payload() -> dict:
    now = datetime.now(WIB)
    mstatus, progress = st.market_state(now, CFG["session"])
    tickers = CFG["watchlist"] + ["IHSG"]
    data, stamps = (datasrc.demo(tickers, now) if DEMO
                    else datasrc.fetch(tickers, "1y", with_intraday=mstatus != "pra-buka"))
    ihsg = data.pop("IHSG", None)
    regime = st.market_regime(ihsg)
    s, risk = CFG["strategy"], CFG["risk"]
    rows, radars = [], []
    for t, df in data.items():
        is_today = df.index[-1].date() == now.date()
        prog = progress if is_today else 1.0  # bar terakhir bukan hari ini -> bar penuh
        ev = st.evaluate(t, st.add_indicators(df, s), s, risk, prog)
        ev["as_of"] = stamps.get(t, "")
        if ev["signal"] == "BUY" and regime["level"] == "bad":
            ev["reasons"].append("⚠ IHSG sedang turun — risiko gagal breakout lebih tinggi")
        rows.append(ev)
        # radar memakai bar yang sudah lengkap saja (hindari volume parsial hari ini)
        complete = df.iloc[:-1] if is_today and mstatus not in ("tutup",) else df
        rd = st.radar(t, complete, s, risk)
        rd["price"], rd["change_pct"] = ev["price"], ev["change_pct"]
        radars.append(rd)
    rows.sort(key=lambda r: (ORDER[r["signal"]], -r["rvol"]))
    radars.sort(key=lambda r: (-r["kandidat"], -r["score"]))
    missing = sorted(set(CFG["watchlist"]) - set(data))
    _cache["frames"] = data
    _cache["regime"] = regime
    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S WIB"),
        "market": {"status": mstatus, "progress": round(progress, 3)},
        "regime": regime,
        "demo": DEMO,
        "counts": {k: sum(r["signal"] == k for r in rows) for k in ORDER},
        "signals": rows,
        "radar": radars,
        "radar_min": st.RADAR_MIN,
        "missing": missing,
        "refresh_seconds": CFG["server"]["refresh_seconds_open"] if mstatus == "buka"
        else CFG["server"]["refresh_seconds_closed"],
        "risk": CFG["risk"],
        "watchlist": CFG["watchlist"],
    }


def refresh() -> None:
    try:
        payload = build_payload()
        with _lock:
            _cache.update(payload=payload, ts=time.time(), error=None)
    except Exception as e:  # jangan matikan loop; tampilkan error di dashboard
        traceback.print_exc()
        with _lock:
            _cache["error"] = f"{type(e).__name__}: {e}"


def refresher_loop() -> None:
    while True:
        refresh()
        with _lock:
            p = _cache["payload"]
        time.sleep(p["refresh_seconds"] if p else 60)


app = FastAPI(title="Dashboard Sinyal Saham BEI", docs_url=None, redoc_url=None)
security = HTTPBasic(auto_error=False)


def auth(cred: HTTPBasicCredentials | None = Depends(security)) -> None:
    if not PASSWORD:
        return
    if cred is None or not secrets.compare_digest(cred.password.encode(), PASSWORD.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Login diperlukan",
                            headers={"WWW-Authenticate": "Basic"})


# ---------------------------------------------------------------------------
# Daftar Pagi Day Trade (data resmi BEI, seluruh saham)
# ---------------------------------------------------------------------------
IDX = idxsrc.IdxClient(Path(os.getenv("IDX_CACHE", BASE / "cache" / "idx")))
_dt: dict = {"panel": None, "ts": 0.0, "error": None, "loading": False, "live": {}, "live_ts": 0.0, "opt": None}


def dt_refresh() -> None:
    if _dt["loading"]:
        return
    _dt["loading"] = True
    try:
        if DEMO:
            now = datetime.now(WIB)
            mstatus, _ = st.market_state(now, CFG["session"])
            end = (pd.Timestamp(now.date()) - pd.offsets.BDay(1)).date() if mstatus != "tutup" else now.date()
            panel, errors = idxsrc.demo_panel(CFG["daytrade"]["history_days"], 150, end=end), []
        else:
            panel, errors = IDX.history(CFG["daytrade"]["history_days"])
        if panel.empty:
            _dt["error"] = ("Data BEI tidak dapat diunduh dari server ini "
                            f"(gagal: {', '.join(errors[:3]) or 'tidak ada data'}).")
        else:
            _dt.update(panel=panel, ts=time.time(),
                       error=f"Sebagian tanggal gagal diunduh: {', '.join(errors[:5])}" if errors else None)
            if CFG["daytrade"].get("auto_tune", True):
                fee = CFG["risk"]["fee_buy_pct"] + CFG["risk"]["fee_sell_pct"]
                _dt["opt"] = dtm.optimize(panel, CFG["daytrade"], fee)
    except Exception as e:
        traceback.print_exc()
        _dt["error"] = f"{type(e).__name__}: {e}"
    finally:
        _dt["loading"] = False


def dt_loop() -> None:
    while True:
        dt_refresh()
        time.sleep(1800)  # cek data baru tiap 30 menit (data hari ini terbit setelah pasar tutup)


@app.on_event("startup")
def _start() -> None:
    threading.Thread(target=refresher_loop, daemon=True).start()
    threading.Thread(target=dt_loop, daemon=True).start()


def _live_status(picks: list[dict], list_date: str, now: datetime) -> None:
    """Status tiap saham hari ini (terpicu/TP/SL) dari harga Yahoo (dapat tertunda)."""
    if not picks or list_date >= now.date().isoformat():
        return
    codes = [p["code"] for p in picks]
    if time.time() - _dt["live_ts"] > 60 or set(codes) - set(_dt["live"]):
        if DEMO:  # harga simulasi di sekitar harga penutupan kemarin
            import numpy as np
            live = {}
            for k, p in enumerate(picks):
                r = np.random.default_rng(int(now.timestamp() // 60) + k)
                o = p["close"] * (1 + r.normal(0.003, 0.008))
                path = o * np.cumprod(1 + r.normal(0.0008, 0.006, 60))
                live[p["code"]] = {"last": float(path[-1]), "high": float(max(path.max(), o)), "low": float(min(path.min(), o)),
                                   "open": float(o), "as_of": now.strftime("%H:%M WIB (DEMO)")}
            _dt.update(live=live, live_ts=time.time())
            codes = []
        try:
            if not codes:
                raise StopIteration
            got, stamps = datasrc.fetch(codes, "1mo", with_intraday=True)
            live = {}
            for c, df in got.items():
                if df.index[-1].date() == now.date():
                    r = df.iloc[-1]
                    live[c] = {"last": float(r["Close"]), "high": float(r["High"]), "low": float(r["Low"]),
                               "open": float(r["Open"]), "as_of": stamps.get(c, "")}
            _dt.update(live=live, live_ts=time.time())
        except StopIteration:
            pass
        except Exception:
            traceback.print_exc()
    for p in picks:
        lv = _dt["live"].get(p["code"])
        if not lv:
            p["live"] = None
            continue
        pl = p["plan"]
        if lv["high"] < pl["trigger"]:
            st_ = "belum terpicu"
        elif lv["open"] > pl["limit"]:
            st_ = "gap di atas limit — tidak terisi"
        elif lv["low"] <= pl["sl"] and lv["last"] <= pl["sl"]:
            st_ = "kena SL"
        elif lv["high"] >= pl["tp"]:
            st_ = "kena TP"
        else:
            st_ = "terpicu — berjalan"
        p["live"] = {**lv, "status": st_}


@app.get("/api/daytrade", dependencies=[Depends(auth)])
def daytrade(capital: float | None = None, risk: float | None = None):
    now = datetime.now(WIB)
    if _dt["panel"] is None:
        if not _dt["loading"]:
            threading.Thread(target=dt_refresh, daemon=True).start()
        return {"loading": True, "error": _dt["error"]}
    cap = capital or CFG["risk"]["capital_idr"]
    rk = risk or CFG["risk"]["risk_per_trade_pct"]
    fee = CFG["risk"]["fee_buy_pct"] + CFG["risk"]["fee_sell_pct"]
    opt = _dt.get("opt")
    dcfg = {**CFG["daytrade"], **(opt["params"] if opt and opt.get("applied") else {})}
    res = dtm.morning_list(_dt["panel"], dcfg, cap, rk, fee)
    mstatus, _ = st.market_state(now, CFG["session"])
    for_today = res["date"] is not None and res["date"] < now.date().isoformat() and mstatus not in ("tutup", "libur akhir pekan")
    _live_status(res["picks"], res["date"] or "", now)
    with _lock:
        regime = _cache.get("regime")
    return {**res, "for_today": for_today, "market": mstatus, "regime": regime,
            "error": _dt["error"], "demo": DEMO, "fee_total_pct": fee,
            "params": {k: dcfg[k] for k in ("tp_pct", "sl_pct", "min_score", "top_n", "min_value_idr")},
            "tuning": None if not opt else {k: opt.get(k) for k in ("status", "applied", "days", "train_days", "test_days",
                                                                     "params", "train_avg", "train_trades", "note")}
                      | {"test": {k: (opt.get("test") or {}).get(k) for k in ("trades", "win_rate", "avg_pct", "total_pct")}},
            "updated": datetime.fromtimestamp(_dt["ts"], WIB).strftime("%Y-%m-%d %H:%M WIB")}


@app.get("/api/daytrade/backtest", dependencies=[Depends(auth)])
def daytrade_backtest():
    if _dt["panel"] is None:
        return {"error": "data belum siap"}
    fee = CFG["risk"]["fee_buy_pct"] + CFG["risk"]["fee_sell_pct"]
    opt = _dt.get("opt")
    dcfg = {**CFG["daytrade"], **(opt["params"] if opt and opt.get("applied") else {})}
    return {**dtm.backtest(_dt["panel"], dcfg, fee), "tuning": opt}


@app.get("/", dependencies=[Depends(auth)])
def index():
    return FileResponse(BASE / "static" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/signals", dependencies=[Depends(auth)])
def signals():
    with _lock:
        p, err, ts = _cache["payload"], _cache["error"], _cache["ts"]
    if p is None:
        if err:
            return JSONResponse({"error": err}, status_code=503)
        refresh()  # permintaan pertama sebelum loop selesai
        with _lock:
            p, err, ts = _cache["payload"], _cache["error"], _cache["ts"]
        if p is None:
            return JSONResponse({"error": err or "data belum siap"}, status_code=503)
    return {**p, "server_error": err, "age_seconds": round(time.time() - ts)}


def _run_backtest() -> None:
    try:
        data, _ = (datasrc.demo(CFG["watchlist"]) if DEMO
                   else datasrc.fetch(CFG["watchlist"], CFG["backtest"]["period"], with_intraday=False))
        trades = []
        for t, df in data.items():
            trades += st.backtest_ticker(t, df, CFG)
        trades.sort(key=lambda x: x.entry_date)
        payload = {"summary": st.summarize(trades), "tickers": len(data),
                   "radar": st.radar_validation(data, CFG["strategy"]),
                   "period": CFG["backtest"]["period"],
                   "trades": [asdict(x) for x in trades[-200:]],
                   "generated_at": datetime.now(WIB).strftime("%Y-%m-%d %H:%M WIB")}
        _bt_cache.update(payload=payload, ts=time.time())
    except Exception as e:
        traceback.print_exc()
        _bt_cache.update(payload={"error": f"{type(e).__name__}: {e}"}, ts=time.time())
    finally:
        _bt_cache["running"] = False


_check_cache: dict = {}


@app.get("/api/check", dependencies=[Depends(auth)])
def check(ticker: str, avg: float | None = None, lots: int | None = None):
    t = ticker.strip().upper().replace(".JK", "")
    if not t.isalnum() or len(t) > 6:
        raise HTTPException(400, "Kode saham tidak valid")
    now = datetime.now(WIB)
    mstatus, progress = st.market_state(now, CFG["session"])
    with _lock:
        df = (_cache.get("frames") or {}).get(t)
        regime = _cache.get("regime") or {"status": "tidak diketahui", "level": "unknown"}
    if df is None:
        hit = _check_cache.get(t)
        if hit and time.time() - hit[0] < 60:
            df = hit[1]
        else:
            got, _ = (datasrc.demo([t], now) if DEMO else datasrc.fetch([t], "1y", with_intraday=mstatus != "pra-buka"))
            if t not in got:
                raise HTTPException(404, f"Data {t} tidak ditemukan (kode salah, suspensi, atau data kurang dari 60 hari)")
            df = got[t]
            _check_cache[t] = (time.time(), df)
    prog = progress if df.index[-1].date() == now.date() else 1.0
    return st.check_stock(t, df, CFG["strategy"], CFG["risk"], regime, prog, avg, lots)


@app.post("/api/backtest", dependencies=[Depends(auth)])
def backtest_start():
    if not _bt_cache["running"]:
        _bt_cache["running"] = True
        threading.Thread(target=_run_backtest, daemon=True).start()
    return {"running": True}


@app.get("/api/backtest", dependencies=[Depends(auth)])
def backtest_get():
    return {"running": _bt_cache["running"], "result": _bt_cache["payload"]}


@app.get("/health")
def health():
    return {"ok": True}
