import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import strategy as st  # noqa: E402
from data import WIB, demo  # noqa: E402

CFG = json.loads((ROOT / "config.json").read_text())
S, R = CFG["strategy"], CFG["risk"]


def synth(n=300, seed=1, base=1000, drift=0.0015):
    rng = np.random.default_rng(seed)
    close = base * np.cumprod(1 + rng.normal(drift, 0.015, n))
    high = close * (1 + rng.uniform(0, 0.02, n))
    low = close * (1 - rng.uniform(0, 0.02, n))
    vol = rng.uniform(8e6, 12e6, n)
    vol[rng.random(n) < 0.12] *= 2.5  # lonjakan volume sesekali
    idx = pd.bdate_range(end="2026-11-10", periods=n)
    return pd.DataFrame({"Open": (high + low) / 2, "High": high, "Low": low, "Close": close, "Volume": vol}, index=idx)


def test_tick_rules():
    assert [st.tick_size(p) for p in (199, 200, 499, 500, 1995, 2000, 4990, 5000)] == [1, 2, 2, 5, 5, 10, 10, 25]
    assert st.round_tick(1003, "up") == 1005 and st.round_tick(1003, "down") == 1000
    assert st.round_tick(498.5, "up") == 500
    assert st.add_ticks(1995, 1) == 2000 and st.add_ticks(2000, -1) == 1995 and st.add_ticks(500, -1) == 498


def test_plan_valid_and_sizing():
    p = st.make_plan(1990, 30, S, R)
    for v in (p.entry, p.limit, p.sl, p.tp, p.tp2):
        assert v % st.tick_size(v) == 0
    assert p.sl < p.entry < p.limit and p.entry < p.tp < p.tp2
    assert p.risk_idr <= R["capital_idr"] * R["risk_per_trade_pct"] / 100 + 1
    assert st.make_plan(1000, 200, S, R) is None  # SL terlalu lebar


def test_buy_signal():
    df = synth()
    top = df["High"].iloc[-21:-1].max()
    df.iloc[-1, df.columns.get_loc("Close")] = top * 1.01
    df.iloc[-1, df.columns.get_loc("High")] = top * 1.015
    df.iloc[-1, df.columns.get_loc("Volume")] = 3e7
    ev = st.evaluate("X", st.add_indicators(df, S), S, R, 1.0)
    assert ev["signal"] == "BUY" and ev["plan"]["lots"] >= 1


def test_no_chasing_extended_breakout():
    df = synth()
    top = df["High"].iloc[-21:-1].max()
    df.iloc[-1, df.columns.get_loc("Close")] = top * 1.08
    df.iloc[-1, df.columns.get_loc("High")] = top * 1.09
    df.iloc[-1, df.columns.get_loc("Volume")] = 3e7
    ev = st.evaluate("X", st.add_indicators(df, S), S, R, 1.0)
    assert ev["signal"] == "WATCH" and ev["plan"] is None


def test_sell_signal_breakdown():
    df = synth()
    bot = df["Low"].iloc[-11:-1].min()
    df.iloc[-1, df.columns.get_loc("Close")] = bot * 0.97
    df.iloc[-1, df.columns.get_loc("Low")] = bot * 0.96
    df.iloc[-1, df.columns.get_loc("Volume")] = 2e7
    ev = st.evaluate("X", st.add_indicators(df, S), S, R, 1.0)
    assert ev["signal"] == "SELL"


def test_intraday_volume_projection():
    df = synth()
    top = df["High"].iloc[-21:-1].max()
    df.iloc[-1, df.columns.get_loc("Close")] = top * 1.01
    df.iloc[-1, df.columns.get_loc("High")] = top * 1.01
    df.iloc[-1, df.columns.get_loc("Volume")] = 6e6  # baru 0,6x rata-rata, tapi sesi baru 30%
    d = st.add_indicators(df, S)
    assert st.evaluate("X", d, S, R, 0.3)["signal"] == "BUY"
    assert st.evaluate("X", d, S, R, 1.0)["signal"] != "BUY"


def test_market_state():
    ses = CFG["session"]
    assert st.market_state(datetime(2026, 11, 10, 10, 30, tzinfo=WIB), ses) == ("buka", 90 / 320)
    assert st.market_state(datetime(2026, 11, 10, 12, 30, tzinfo=WIB), ses)[0] == "istirahat"
    assert st.market_state(datetime(2026, 11, 10, 8, 30, tzinfo=WIB), ses)[0] == "pra-buka"
    assert st.market_state(datetime(2026, 11, 10, 16, 30, tzinfo=WIB), ses)[0] == "tutup"
    assert st.market_state(datetime(2026, 11, 13, 14, 30, tzinfo=WIB), ses) == ("buka", 180 / 260)
    assert st.market_state(datetime(2026, 11, 14, 10, 0, tzinfo=WIB), ses)[0] == "libur akhir pekan"


def test_backtest_consistency():
    trades = []
    for i in range(8):
        trades += st.backtest_ticker(f"T{i}", synth(n=500, seed=i, drift=0.002), CFG)
    assert trades
    for t in trades:
        assert t.entry > t.sl and t.days >= 1
        if t.reason == "SL":
            assert t.exit == t.sl
    m = st.summarize(trades)
    assert m["trades"] == len(trades) and 0 <= m["win_rate"] <= 100


def test_demo_data_produces_all_signal_types():
    data, _ = demo(CFG["watchlist"], datetime(2026, 11, 10, 11, 0, tzinfo=WIB))
    sig = {st.evaluate(t, st.add_indicators(df, S), S, R, 0.5)["signal"] for t, df in data.items()}
    assert {"BUY", "SELL"} <= sig


def test_radar_detects_accumulation_and_not_breakout():
    df = synth(drift=0.002)
    n = len(df)
    lvl = df["High"].iloc[-80:-25].max()
    c = lvl * 0.97 + np.zeros(25)
    c += np.sin(np.linspace(0, 6, 25)) * lvl * 0.004
    df.iloc[-25:, df.columns.get_loc("Close")] = c
    df.iloc[-25:, df.columns.get_loc("High")] = c * 1.004
    df.iloc[-25:, df.columns.get_loc("Low")] = c * 0.996
    df.iloc[-25:, df.columns.get_loc("Volume")] = 5e6
    r = st.radar("X", df, S, R)
    assert r["score"] >= st.RADAR_MIN
    for chk in r["checks"]:
        assert set(chk) >= {"label", "ok"}
    f = st.radar_frame(df, S)
    assert {"score", "kandidat", "res"} <= set(f.columns)


def test_radar_has_no_lookahead():
    df = synth(n=400, seed=3)
    full = st.radar_frame(df, S)["score"]
    part = st.radar_frame(df.iloc[:300], S)["score"]
    assert (full.iloc[:300] == part).all()


def test_market_regime_levels():
    up = synth(drift=0.003)
    down = up.copy()
    down[["Open", "High", "Low", "Close"]] = up[["Open", "High", "Low", "Close"]].iloc[::-1].values
    assert st.market_regime(up)["level"] == "ok"
    assert st.market_regime(down)["level"] == "bad"
    assert st.market_regime(None)["level"] == "unknown"


def test_check_stock_position_math():
    df = synth(drift=-0.003, seed=5)
    reg = {"status": "TURUN", "level": "bad"}
    price = float(df["Close"].iloc[-1])
    out = st.check_stock("X", df, S, R, reg, 1.0, avg=price * 1.3, lots=4)
    p = out["position"]
    assert out["entry"]["verdict"] == "JANGAN MASUK DULU"
    assert p["pl_pct"] < 0 and p["need_to_breakeven_pct"] > 29
    assert p["cut_loss"] < price and p["cut_loss"] % st.tick_size(p["cut_loss"]) == 0
    assert p["status"] in ("TREN RUSAK", "MELEMAH", "MASIH SEHAT")
