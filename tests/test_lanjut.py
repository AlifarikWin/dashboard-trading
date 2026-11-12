from datetime import date

import numpy as np
import pandas as pd

import lanjut


def test_classify_rules():
    ok = dict(prev=1000, value20=1e10, price=1050)
    assert lanjut.classify(5, 0.9, 1.0, trend_up=True, trend_dn=False, **ok) == "up"
    assert lanjut.classify(5, 0.7, 1.0, trend_up=True, trend_dn=False, **ok) is None        # tidak ditutup dekat high sesi 1
    assert lanjut.classify(5, 0.9, 0.3, trend_up=True, trend_dn=False, **ok) is None        # volume sesi 1 kecil
    assert lanjut.classify(5, 0.9, 1.0, trend_up=False, trend_dn=False, **ok) is None       # tren harian tidak naik
    assert lanjut.classify(23, 0.95, 1.0, trend_up=True, trend_dn=False, **ok) is None      # dekat ARA 25%
    assert lanjut.classify(-4, 0.1, 1.0, trend_up=False, trend_dn=True, **ok) == "down"
    assert lanjut.classify(5, 0.9, 1.0, prev=1000, trend_up=True, trend_dn=False, value20=1e8, price=1050) is None  # tidak likuid


def _frames(n=80):
    idx = pd.bdate_range(end="2026-11-10", periods=n)
    c = np.linspace(1000, 1400, n)
    df = pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 2e7}, index=idx)
    return idx, df


def test_test_outcome_is_close_vs_s1_close():
    idx, df = _frames()
    d = idx[-1]
    prev = df["Close"].iloc[-2]
    s1c = prev * 1.04
    df.loc[d, "Close"] = s1c * 1.02                          # sesi 2 naik 2%
    h = pd.DataFrame({"s1_high": [s1c * 1.001], "s1_low": [prev], "s1_close": [s1c], "s1_vol": [2e7]}, index=[d])
    t = lanjut.test({"AAA": df}, {"AAA": h}, fee=0.4)
    assert t["up"]["n"] == 1 and abs(t["up"]["avg_s2"] - 2.0) < 0.01 and abs(t["up"]["avg_net"] - 1.6) < 0.01
    assert t["status"] == "data_kurang"


def test_candidates_monitor_track(tmp_path):
    base = {"AAA": {"ma20": 950, "ma50": 900, "av20": 1e6, "val20": 2e10, "close": 1000},
            "BBB": {"ma20": 1100, "ma50": 1200, "av20": 1e6, "val20": 2e10, "close": 1000}}
    snap = {"q": {"AAA": [1050, 8e5, 1000, 1052, 995], "BBB": [960, 9e5, 1000, 1001, 958], "CCC": [100, 1, 100]}}
    c = lanjut.candidates(snap, base, {"AAA": "PT A"}, set())
    assert [x["c"] for x in c["up"]] == ["AAA"] and [x["c"] for x in c["down"]] == ["BBB"]
    assert lanjut.candidates(snap, base, {}, {"AAA"})["up"] == []
    m = lanjut.monitor(c, {"AAA": 1071, "BBB": 950})
    assert m["up"][0]["s2"] == 2.0 and m["down"][0]["s2"] == -1.04
    d = date(2026, 11, 12)
    lanjut.save_track(tmp_path, d, c, "layak")
    lanjut.close_track(tmp_path, d, {"AAA": 1071, "BBB": 950}, fee=0.4)
    lanjut.close_track(tmp_path, d, {"AAA": 1, "BBB": 1}, fee=0.4)      # tidak menimpa hasil yang sudah ditutup
    s = lanjut.track_summary(tmp_path)
    assert s["up"]["trades"] == 1 and s["up"]["avg_net"] == 1.6 and s["down"]["down_pct"] == 100.0
