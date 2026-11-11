import json
from datetime import date, datetime

import numpy as np
import pandas as pd

import analysis as an
import data
import strategy as st
import universe as uni
from data import WIB


def frame(close, vol=1e6, end="2026-11-10"):
    idx = pd.bdate_range(end=end, periods=len(close))
    c = np.asarray(close, float)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": vol}, index=idx)


def test_dividend_yield_units():
    f = an.fundamental({"longName": "X", "trailingAnnualDividendYield": 0.035, "dividendYield": 9.9}, 1000)
    assert {i["label"]: i["value"] for i in f["items"]}["Dividend yield"] == "3,5%"
    f = an.fundamental({"longName": "X", "dividendYield": 2.5}, 1000)  # Yahoo kini memberi persen
    assert {i["label"]: i["value"] for i in f["items"]}["Dividend yield"] == "2,5%"


def test_basic_fund_roe_and_merge():
    b = uni.basic_fund({"name": "PT A", "sector": "Energy", "pe": 8, "pbv": 1.2, "eps": 150, "bvps": 1000, "dyf": 0.05})
    assert abs(b["returnOnEquity"] - 0.15) < 1e-9
    assert uni.basic_fund({"eps": 10, "bvps": -5})["returnOnEquity"] is None  # ekuitas negatif: ROE tidak dihitung
    m = uni.merge_fund(b, {"returnOnEquity": 0.2, "debtToEquity": None})
    assert m["returnOnEquity"] == 0.2 and m["trailingPE"] == 8
    f = an.fundamental(m, 1500)
    assert {i["label"]: i["value"] for i in f["items"]}["ROE"] == "20,0%"


def test_price_flags():
    today = date(2026, 11, 11)
    calm = frame(np.full(40, 1000.0), vol=5e6)
    assert uni.price_flags(calm, today) == []
    # dua hari mendekati ARA (25%) dalam 10 hari -> rawan UMA, memblokir
    c = np.r_[np.full(30, 1000.0), 1000, 1240, 1540, 1550, 1560, 1570, 1580, 1590, 1600, 1610]
    fl = uni.price_flags(frame(c, vol=5e6), today)
    assert any(f["kind"] == "tidak wajar" and f["block"] for f in fl)
    # tidak ada transaksi > 3 hari bursa -> kemungkinan suspensi
    fl = uni.price_flags(frame(np.full(40, 1000.0), vol=5e6, end="2026-11-02"), today)
    assert any(f["kind"] == "suspensi" and f["block"] for f in fl)
    # tidak likuid & gocap: peringatan, tidak memblokir
    fl = uni.price_flags(frame(np.full(40, 50.0), vol=1000), today)
    kinds = {f["kind"]: f["block"] for f in fl}
    assert kinds == {"likuiditas": False, "gocap": False}
    # harga ≤ Rp10 tidak boleh error (ARA berupa Rp1)
    uni.price_flags(frame(np.full(40, 8.0), vol=1e8), today)


def test_dividend_stats_cum_and_ex():
    idx = pd.bdate_range("2025-01-01", periods=60)
    close = pd.Series(np.r_[np.full(20, 1000.0), np.linspace(1000, 1100, 21), np.full(19, 1050.0)], index=idx)
    open_ = close.copy()
    ex = idx[41]
    open_[ex] = 1045.0
    divs = pd.Series(0.0, index=idx)
    divs[ex] = 50.0
    r = uni.dividend_stats(close, open_, divs, pre=10)
    assert r["n"] == 1 and r["up"] == 1
    assert abs(r["avg_run"] - (1100 / close.iloc[30] - 1) * 100) < 0.01  # cum = hari sebelum ex date
    assert abs(r["avg_drop"] - (1045 / 1100 - 1) * 100) < 0.01
    assert uni.dividend_stats(close, open_, divs * 0) is None


def test_compact_ohlc_roundtrip():
    a = {"ohlc": [{"time": "2026-11-10", "open": 100.0, "high": 110.0, "low": 95.5, "close": 105.0, "volume": 1234.0}]}
    uni.compact_ohlc(a)
    assert a["ohlc"] == {"t": ["2026-11-10"], "o": [100], "h": [110], "l": [95.5], "c": [105], "v": [1234]}
    uni.compact_ohlc(a)  # idempoten
    assert a["ohlc"]["o"] == [100]


def test_universe_build_demo(tmp_path):
    cfg = json.load(open("config.json"))
    codes = [f"DM{i:02d}" for i in range(12)]
    fr, _ = data.demo(codes + ["IHSG"], datetime(2026, 11, 11, 18, tzinfo=WIB))
    ihsg = fr.pop("IHSG")
    listing = uni.demo_list(codes)
    state = uni.build(tmp_path, listing, fr, ihsg, cfg, st.market_regime(ihsg), date(2026, 11, 11), {}, {}, date(2026, 11, 12))
    assert state["stocks"] == 12 and state["errors"] == 0
    u = json.loads((tmp_path / "universe.json").read_text())
    r = u["rows"][0]
    assert {"c", "n", "s", "p", "ch", "v", "t", "w", "rs", "bo", "ib", "f", "fl", "blk"} <= set(r)
    a = json.loads((tmp_path / "analysis" / "DM00.json").read_text())
    assert a["scope"] == "harian" and isinstance(a["ohlc"], dict) and a["check"]["entry"]["verdict"]
    assert a["ar"]["for"] == "2026-11-12"
    p = uni.load_panel(tmp_path)
    assert p["code"].nunique() == 12 and len(p) <= 12 * (cfg["daytrade"]["history_days"])


def test_fetch_fundamentals_rotation_limit(tmp_path):
    f = tmp_path / "f.json"
    f.write_text(json.dumps({"A": {"_ts": 5}, "B": {"_ts": 1}}))
    # limit=0: tidak mengunduh apa pun, cache dikembalikan apa adanya (tanpa internet)
    assert an.fetch_fundamentals(["A", "B", "C"], f, limit=0) == {"A": {"_ts": 5}, "B": {"_ts": 1}}
