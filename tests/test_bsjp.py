import json
from datetime import date

import numpy as np
import pandas as pd

import bsjp


def trend_frame(n=80, end="2026-11-10", last_strong=True, next_open=None):
    idx = pd.bdate_range(end=end, periods=n)
    c = np.linspace(1000, 1400, n)                       # tren naik: close > MA20 > MA50
    o, h, l = c * 0.995, c * 1.01, c * 0.99
    v = np.full(n, 2e7)                                   # nilai transaksi ± Rp28 miliar/hari
    if last_strong:                                       # hari terakhir: naik 5%, tutup di high, volume 3×
        c[-1] = c[-2] * 1.05; h[-1] = c[-1]; l[-1] = c[-2] * 0.995; o[-1] = c[-2]; v[-1] = 6e7
    df = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v}, index=idx)
    if next_open is not None:                             # tambah satu hari: open besok
        nx = pd.bdate_range(start=idx[-1] + pd.offsets.BDay(1), periods=1)
        df = pd.concat([df, pd.DataFrame({"Open": [next_open], "High": [next_open * 1.01], "Low": [next_open * 0.99],
                                          "Close": [next_open], "Volume": [2e7]}, index=nx)])
    return df


def test_conditions_and_overnight_outcome():
    df = trend_frame(next_open=None)
    d = bsjp.features(df)
    up, dn = bsjp.conditions(d)
    assert bool(up.iloc[-1]) and not bool(dn.iloc[-1])
    assert not up.iloc[:-1].any()                         # hari biasa tidak lolos (volume/penutupan tidak kuat)
    close = df["Close"].iloc[-1]
    df2 = trend_frame(next_open=close * 1.02)
    d2 = bsjp.features(df2)
    assert abs(d2["on"].iloc[-2] - 2.0) < 1e-9           # open besok +2% dari close hari sinyal


def test_ara_locked_is_excluded():
    df = trend_frame()
    df.iloc[-1, df.columns.get_loc("Close")] = df["Close"].iloc[-2] * 1.24   # +24% (ARA 25%): tidak bisa dibeli wajar
    df.iloc[-1, df.columns.get_loc("High")] = df["Close"].iloc[-1]
    up, _ = bsjp.conditions(bsjp.features(df))
    assert not bool(up.iloc[-1])


def test_evaluate_history_and_status():
    frames = {f"S{i}": trend_frame(next_open=None) for i in range(3)}
    stats, base = bsjp.evaluate_history(frames, fee=0.4)
    assert stats["up"]["n"] == 0                          # hasil semalam belum ada untuk hari terakhir
    assert stats["status"] == "data_kurang"
    assert set(base["S0"]) >= {"ma20", "ma50", "hi20", "av20", "val20", "close"}
    frames = {f"S{i}": trend_frame(next_open=trend_frame()["Close"].iloc[-1] * 1.01) for i in range(3)}
    stats, _ = bsjp.evaluate_history(frames, fee=0.4)
    assert stats["up"]["n"] == 3 and stats["up"]["avg_net"] == 0.6 and stats["up"]["gap_up_pct"] == 100.0


def test_candidates_daily_and_live():
    frames = {"AAA": trend_frame(), "BBB": trend_frame(last_strong=False)}
    f = bsjp.candidates_daily(frames, {"AAA": "PT A"}, set(), date(2026, 11, 10))
    assert [x["c"] for x in f["up"]] == ["AAA"] and f["up"][0]["plan"]["buy_limit"] >= f["up"][0]["price"]
    assert bsjp.candidates_daily(frames, {}, {"AAA"}, date(2026, 11, 10))["up"] == []   # saham berisiko diblokir
    _, base = bsjp.evaluate_history({"AAA": trend_frame(last_strong=False)}, 0.4)
    b = base["AAA"]
    q = {"AAA": {"p": b["close"] * 1.04, "prev": b["close"], "h": b["close"] * 1.041, "l": b["close"], "vol": b["av20"] * 2}}
    live = bsjp.candidates_live(q, base, {}, set(), 0.95, date(2026, 11, 11))
    assert [x["c"] for x in live["up"]] == ["AAA"] and live["up"][0]["vr"] > 2


def test_monitor_status():
    picks = [{"c": "AAA", "price": 1000}, {"c": "BBB", "price": 1000}, {"c": "CCC", "price": 1000}]
    q = {"AAA": {"o": 1020, "p": 1030}, "BBB": {"o": 1020, "p": 1010}, "CCC": {"o": 990, "p": 985}}
    m = {x["c"]: x["mon"] for x in bsjp.monitor(picks, q)}
    assert m["AAA"]["status"] == "gap naik, bertahan" and m["AAA"]["gap"] == 2.0
    assert m["BBB"]["status"] == "gap naik, tergerus" and m["CCC"]["status"] == "dibuka turun"


def test_snapshot_track(tmp_path):
    final = {"date": "2026-11-10", "up": [{"c": "AAA", "price": 1000.0, "score": 90}]}
    bsjp.save_snapshot(tmp_path, final, "layak")
    bsjp.save_snapshot(tmp_path, {"date": "2026-11-10", "up": []}, "layak")      # tidak menimpa
    df = pd.DataFrame({"Open": [1000, 1015.0], "High": [1, 1], "Low": [1, 1], "Close": [1000, 1010], "Volume": [1, 1]},
                      index=pd.to_datetime(["2026-11-10", "2026-11-11"]))
    bsjp.evaluate_snapshots(tmp_path, {"AAA": df}, fee=0.4)
    s = bsjp.track_summary(tmp_path)
    assert s["all"]["trades"] == 1 and s["all"]["avg_net"] == 1.1 and s["followed"]["trades"] == 1
