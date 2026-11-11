import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analysis as an  # noqa: E402
from strategy import tick_size  # noqa: E402


def frame(n=300, seed=1, base=3500):
    rng = np.random.default_rng(seed)
    c = base * np.cumprod(1 + rng.normal(0.0005, 0.015, n))
    h, l = c * (1 + rng.uniform(0, .015, n)), c * (1 - rng.uniform(0, .015, n))
    return pd.DataFrame({"Open": (h + l) / 2, "High": h, "Low": l, "Close": c, "Volume": rng.uniform(1e7, 2e7, n)},
                        index=pd.bdate_range(end="2026-11-10", periods=n))


def test_levels_are_ordered_and_on_ticks():
    for seed in range(5):
        df = frame(seed=seed)
        lv = an.key_levels(df)
        assert lv["breakdown"]["level"] < lv["breakout"]["level"]
        assert lv["breakdown"]["target"] < lv["breakdown"]["level"]
        assert lv["breakout"]["target"] > lv["breakout"]["level"]
        for z in lv["supports"] + lv["resistances"]:
            assert z["low"] <= z["high"] and z["low"] % tick_size(z["low"]) == 0
        if lv["buy_zone"]:
            assert lv["buy_zone"][0] == lv["breakdown"]["level"]
        if lv["sell_zone"]:
            assert lv["sell_zone"][1] == lv["breakout"]["level"]


def test_support_found_at_repeated_bounce():
    df = frame(n=200, seed=3)
    floor = 3000.0
    for i in (40, 90, 140):  # tiga kali memantul di 3.000
        df.iloc[i, df.columns.get_loc("Low")] = floor
    df.iloc[-1, df.columns.get_loc("Close")] = max(df["Close"].iloc[-1], floor * 1.08)
    zs = an.zones(df)
    assert any(z["low"] <= floor <= z["high"] and z["touches"] >= 3 for z in zs)


def test_follow_through_counts_and_bounds():
    ft = an.follow_through(frame(n=500, seed=7))
    for k in ("breakout", "breakdown"):
        s = ft[k]
        assert s["n"] >= 0
        if s["n"]:
            assert 0 <= s["pct"] <= 100


def test_fundamental_scoring_and_percent_normalisation():
    raw = {"longName": "X", "sector": "Financial Services", "returnOnEquity": 0.2, "profitMargins": 25.0,
           "revenueGrowth": 0.05, "earningsGrowth": -0.02, "debtToEquity": 500, "trailingPE": 10,
           "priceToBook": 2, "trailingEps": 400, "bookValue": 2000, "dividendYield": 0.04}
    f = an.fundamental(raw, 3500)
    labels = {i["label"]: i for i in f["items"]}
    assert labels["ROE"]["value"] == "20,0%" and labels["Margin laba bersih"]["value"] == "25,0%"
    assert "Utang / ekuitas (DER)" not in labels  # tidak dinilai untuk sektor keuangan
    assert f["verdict"] in ("Kuat", "Sedang", "Lemah")
    assert an.fundamental({}, 100) == {"available": False}


def test_analyze_payload():
    a = an.analyze("BMRI", frame(), an.demo_fundamental("BMRI"))
    assert len(a["ohlc"]) == 250 and a["levels"]["price"] == a["price"]
    z = an.card_zones(a)
    assert set(z) == {"buy_zone", "sell_zone", "breakout", "breakdown"}
