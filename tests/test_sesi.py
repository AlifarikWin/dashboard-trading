from datetime import date, datetime

import numpy as np
import pandas as pd

import bsjp
import sesi
from data import WIB


def test_s1_end_minute():
    cfg = {"mon_thu": [["09:00", "12:00"], ["13:30", "15:50"]], "fri": [["09:00", "11:30"], ["14:00", "15:50"]]}
    assert sesi.s1_end_minute(date(2026, 11, 12), cfg) == 720       # Kamis
    assert sesi.s1_end_minute(date(2026, 11, 13), cfg) == 690       # Jumat


def test_snapshot_final_not_overwritten(tmp_path):
    q = {"AAA": {"p": 1050, "vol": 10, "prev": 1000}}
    sesi.save_s1(tmp_path, date(2026, 11, 12), datetime(2026, 11, 12, 12, 5, tzinfo=WIB), q, None, final=False)
    sesi.save_s1(tmp_path, date(2026, 11, 12), datetime(2026, 11, 12, 12, 20, tzinfo=WIB), {"AAA": {"p": 1060, "prev": 1000}}, None, final=True)
    sesi.save_s1(tmp_path, date(2026, 11, 12), datetime(2026, 11, 12, 13, 0, tzinfo=WIB), {"AAA": {"p": 1, "prev": 1000}}, None, final=True)
    s = sesi.load_s1(tmp_path, date(2026, 11, 12))
    assert s["final"] and s["time"] == "12:20" and s["q"]["AAA"] == [1060, None, 1000]


def test_recaps():
    items = [{"c": "AAA", "n": "A", "s": "Energi", "p": 1100.0, "ch": 10.0, "tr": "up", "v20": 20, "blk": False},
             {"c": "BBB", "n": "B", "s": "Energi", "p": 900.0, "ch": -10.0, "tr": "down", "v20": 20, "blk": False}]
    snap = {"time": "12:15", "final": True, "ihsg": {"p": 7100, "prev": 7000},
            "q": {"AAA": [1050, 1, 1000], "BBB": [950, 1, 1000]}}
    s1 = sesi.items_at_s1(items, snap)
    assert [x["ch"] for x in s1] == [5.0, -5.0]
    r1 = sesi.recap_s1(s1, snap["ihsg"], [{"live": {"status": "kena TP"}}, {"live": None}], "selesai", "12:15")
    assert r1["ihsg"] == 1.43 and r1["breadth"] == {"up": 1, "down": 1, "flat": 0}
    assert r1["picks"] == {"kena TP": 1, "belum ada data": 1}
    r2 = sesi.recap_s2(items, snap, {"p": 7171, "prev": 7000}, "selesai", "16:15")
    a = {x["c"]: x for x in r2["items"]}
    assert round(a["AAA"]["s2"], 2) == 4.76 and a["AAA"]["s1"] == 5.0 and a["BBB"]["s2"] == -5.26
    assert r2["ihsg"] == 1.0
    assert [x["c"] for x in sesi.top(r2["items"], "s2")] == ["AAA"]              # naik: likuid + tren naik
    assert [x["c"] for x in sesi.top(r2["items"], "s2", up=False)] == ["BBB"]    # turun: tanpa syarat tren
    items[0]["blk"] = True
    assert sesi.top(sesi.recap_s2(items, snap, None, "selesai", "")["items"], "s2") == []   # berisiko disembunyikan


def test_bsjp_s2_filter_and_test():
    idx = pd.bdate_range(end="2026-11-10", periods=80)
    c = np.linspace(1000, 1400, 80); o, h, l = c * .995, c * 1.01, c * .99; v = np.full(80, 2e7)
    c[-1] = c[-2] * 1.05; h[-1] = c[-1]; l[-1] = c[-2] * .995; o[-1] = c[-2]; v[-1] = 6e7
    df = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v}, index=idx)
    close = float(c[-1])
    keep = bsjp.candidates_daily({"AAA": df}, {}, set(), date(2026, 11, 10), s1={"AAA": [close * 0.98, 1, 1]}, s2_filter=True)
    drop = bsjp.candidates_daily({"AAA": df}, {}, set(), date(2026, 11, 10), s1={"AAA": [close * 1.02, 1, 1]}, s2_filter=True)
    nos1 = bsjp.candidates_daily({"AAA": df}, {}, set(), date(2026, 11, 10), s1=None, s2_filter=True)
    assert [x["c"] for x in keep["up"]] == ["AAA"] and keep["up"][0]["s2"] > 0 and keep["s2_filter"]
    assert drop["up"] == [] and nos1["up"] and nos1["s2_missing"] and not nos1["s2_filter"]
    # s2_test: kandidat yang naik di sesi 2 semalamnya +2%, yang turun −2%
    rows = []
    s1c = {}
    for i, d in enumerate(pd.bdate_range("2026-01-01", periods=80)):
        up = i % 2 == 0
        rows.append({"date": d, "code": f"C{i}", "score": 90, "on": 2.0 if up else -2.0, "close": 100.0})
        s1c[f"C{i}"] = pd.Series({d: 99.0 if up else 101.0})
    t = bsjp.s2_test(pd.DataFrame(rows), s1c, fee=0.4)
    assert t["matched"] == 80 and t["pos"]["n"] == 40 and t["pos"]["avg_net"] == 1.6 and t["neg"]["avg_net"] == -2.4
    assert t["adopt"] is True
