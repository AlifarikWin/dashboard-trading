import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import daytrade as dt  # noqa: E402
import idx  # noqa: E402
from strategy import tick_size  # noqa: E402

CFG = json.loads((ROOT / "config.json").read_text())["daytrade"]


def test_plan_prices_follow_ticks_and_rules():
    row = pd.Series({"high": 1987.0})
    p = dt.plan(row, CFG, 10_000_000, 1, 0.4)
    for k in ("trigger", "limit", "sl", "tp"):
        assert p[k] % tick_size(p[k]) == 0
    assert p["trigger"] > 1987 and p["sl"] < p["trigger"] < p["limit"] and p["tp"] > p["trigger"]
    assert p["lots"] * 100 * p["limit"] <= 10_000_000 * 0.5
    risk = p["lots"] * 100 * ((p["limit"] - p["sl"]) + p["limit"] * 0.004)
    assert risk <= 100_000 + 1


def test_foreign_streak_and_features():
    rows = []
    for i, fn in enumerate([-1, 1, 1, 1, -1, 1, 1]):
        rows.append({"code": "AAAA", "date": pd.Timestamp("2026-10-01") + pd.Timedelta(days=i), "open": 100, "high": 105,
                     "low": 95, "close": 100 + i, "prev": 99 + i, "volume": 1e6, "value": 1e8, "freq": 10,
                     "fbuy": 100 + fn * 50, "fsell": 100 - fn * 50, "remarks": ""})
    f = dt.features(idx.to_panel(pd.DataFrame(rows)))
    assert list(f["fstreak"]) == [0, 1, 2, 3, 0, 1, 2]


def test_morning_list_and_backtest_on_demo():
    panel = idx.demo_panel(90, 100, end=date(2026, 11, 9))
    m = dt.morning_list(panel, CFG, 10_000_000, 1, 0.4)
    assert m["date"] == "2026-11-09" and m["scanned"] == 100
    assert len(m["picks"]) <= CFG["top_n"]
    for p in m["picks"]:
        assert p["score"] >= CFG["min_score"] and len(p["checks"]) == len(dt.LABELS)
    b = dt.backtest(panel, CFG, 0.4)
    assert b["trades"] > 0
    for t in b["recent"]:
        assert t["why"] in ("SL", "TP", "tutup sore")


def test_no_lookahead_in_scores():
    panel = idx.demo_panel(90, 40, end=date(2026, 11, 9))
    cut = panel[panel["date"] <= panel["date"].unique()[60]]
    full = dt.score_rows(dt.features(panel), CFG)
    part = dt.score_rows(dt.features(cut), CFG)
    a = full[full["date"].isin(cut["date"].unique())].set_index(["code", "date"])["score"].sort_index()
    b = part.set_index(["code", "date"])["score"].sort_index()
    assert (a == b).all()


def test_idx_client_parses_and_caches(tmp_path):
    payload = {"data": [{"StockCode": "BBCA", "OpenPrice": 9000, "High": 9100, "Low": 8950, "Close": 9050,
                         "Previous": 9000, "Volume": 5e7, "Value": 4.5e11, "Frequency": 20000,
                         "ForeignBuy": 2e7, "ForeignSell": 1.5e7, "Remarks": "--U-3100-"},
                        {"StockCode": "XXXX", "OpenPrice": 0, "High": 0, "Low": 0, "Close": 50, "Previous": 50,
                         "Volume": 0, "Value": 0, "Frequency": 0, "ForeignBuy": 0, "ForeignSell": 0, "Remarks": ""}]}

    class Resp:
        status_code = 200
        def json(self): return payload

    class Sess:
        calls = 0
        def get(self, *a, **k):
            Sess.calls += 1
            return Resp()

    c = idx.IdxClient(tmp_path)
    c._session = Sess()
    rows = c.day(date(2026, 11, 6))
    assert rows[0]["code"] == "BBCA" and rows[0]["fbuy"] == 2e7
    c.day(date(2026, 11, 6))
    assert Sess.calls == 1  # hari kedua diambil dari cache
    panel = idx.to_panel(pd.DataFrame([{**r, "date": pd.Timestamp("2026-11-06")} for r in rows]))
    assert list(panel["code"]) == ["BBCA"]  # saham tanpa transaksi dibuang


def test_optimize_applies_only_when_out_of_sample_positive(monkeypatch):
    panel = idx.demo_panel(120, 60, end=date(2026, 11, 9))

    def fake(sc, c, fee, day_from=30, day_to=None):
        good = c["min_score"] == 70 and c["tp_pct"] == 3.0 and c["sl_pct"] == 1.5
        val = 1.0 if good else -0.5
        return [{"date": "2026-01-01", "code": "X", "entry": 1, "exit": 1, "why": "TP", "net_pct": val}] * 20

    monkeypatch.setattr(dt, "_simulate", fake)
    o = dt.optimize(panel, CFG, 0.4)
    assert o["applied"] and o["status"] == "layak"
    assert o["params"] == {"min_score": 70, "tp_pct": 3.0, "sl_pct": 1.5}

    monkeypatch.setattr(dt, "_simulate", lambda sc, c, fee, day_from=30, day_to=None:
                        [{"date": "d", "code": "X", "entry": 1, "exit": 1, "why": "SL",
                          "net_pct": (0.5 if day_to is not None else -0.3)}] * 20)
    o = dt.optimize(panel, CFG, 0.4)
    assert not o["applied"] and o["status"] == "tidak_layak"  # bagus di latih, rugi di uji -> ditolak
