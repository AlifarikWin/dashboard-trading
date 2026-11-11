import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import news  # noqa: E402
import risk as rk  # noqa: E402
import track  # noqa: E402
from data import WIB  # noqa: E402
from strategy import tick_size  # noqa: E402


def frame(n=120, base=1000.0, seed=0, end="2026-11-10"):
    rng = np.random.default_rng(seed)
    c = base * np.cumprod(1 + rng.normal(0, 0.01, n))
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": rng.uniform(1e6, 2e6, n)},
                        index=pd.bdate_range(end=end, periods=n))


# ---------------- ARA / ARB ----------------
@pytest.mark.parametrize("prev,d,ara,arb", [
    (100, date(2026, 11, 11), 35.0, 7.0), (1000, date(2026, 11, 11), 25.0, 7.0), (8000, date(2026, 11, 11), 20.0, 7.0),
    (12, date(2026, 11, 11), 35.0, None), (8, date(2026, 11, 11), None, None),
    (100, date(2027, 1, 4), 35.0, 35.0), (1000, date(2027, 1, 4), 25.0, 25.0), (8000, date(2027, 1, 4), 20.0, 20.0),
])
def test_ar_rules(prev, d, ara, arb):
    a, b, _ = rk.ar_pct(prev, d)
    assert (a, b) == (ara, arb)


def test_ar_limits_on_valid_ticks():
    for prev in (150, 199, 480, 1995, 4990, 9025):
        lim = rk.ar_limits(prev, date(2026, 11, 11))
        assert lim["ara"] % tick_size(lim["ara"]) == 0 and lim["arb"] % tick_size(lim["arb"]) == 0
        assert lim["arb"] < prev < lim["ara"]
        assert lim["ara"] <= prev * 1.35 + 1e-9 and lim["arb"] >= prev * 0.93 - tick_size(prev)


# ---------------- validasi data ----------------
def test_validate_rejects_jump_and_stale_and_fixes_ohlc():
    df = frame()
    df.iloc[-5, df.columns.get_loc("Close")] *= 1.6
    _, fatal, _ = rk.validate_frame("X", df, date(2026, 11, 10), False)
    assert any("lonjakan" in f for f in fatal)
    stale = frame(end="2026-10-20")
    clean, fatal, _ = rk.validate_frame("X", stale, date(2026, 11, 10), False)
    assert clean is None and any("suspensi" in f for f in fatal)
    bad = frame()
    bad.iloc[-3, bad.columns.get_loc("High")] = bad["Low"].iloc[-3] * 0.9
    clean, fatal, warn = rk.validate_frame("X", bad, date(2026, 11, 10), False)
    assert not fatal and warn and (clean["High"] >= clean["Low"]).all()


def test_idx_flags():
    today = date(2026, 11, 11)
    scr = [{"stockCode": "AAAA", "notation": "X"}, {"stockCode": "BBBB", "notation": "", "umaDate": "2026-11-05T00:00:00"},
           {"stockCode": "CCCC", "notation": None, "corpAction": "Dividen Tunai", "corpActionDate": "2026-11-20"},
           {"stockCode": "DDDD", "notation": "", "umaDate": "2026-01-01"}]
    sus = [{"Kode": "EEEE", "Judul": "Penghentian Sementara Perdagangan Saham", "Date": "2026-11-10T00:00:00"},
           {"Kode": "FFFF", "Judul": "Pembukaan Kembali Suspensi", "Date": "2026-11-10T00:00:00"}]
    f = rk.idx_flags(scr, sus, today)
    assert f["AAAA"][0]["block"] and f["BBBB"][0]["kind"] == "uma" and not f["CCCC"][0]["block"]
    assert "DDDD" not in f and f["EEEE"][0]["block"] and not f["FFFF"][0]["block"]


# ---------------- berita ----------------
@pytest.mark.parametrize("title,kind,tone", [
    ("BBRI bagikan dividen jumbo Rp30 triliun", "Dividen", "positif"),
    ("Laba bersih TLKM kuartal III turun 12%", "Laporan keuangan", "negatif"),
    ("Saham GOTO disuspensi BEI", "Suspensi/UMA", "negatif"),
    ("WIKA ajukan PKPU", "PKPU/Pailit", "negatif"),
    ("ASII teken kontrak baru proyek tol", "Kontrak/Ekspansi", "positif"),
    ("Jadwal RUPS BMRI pekan ini", "RUPS", "netral"),
    ("BRIS Right Issue 6,8 Miliar Saham Baru, Cek Profil hingga Kinerja Terbarunya", "Aksi korporasi", "netral"),
    ("Indo Tambangraya (ITMG) Cetak Laba Rp2,93 Triliun hingga September 2026", "Laporan keuangan", "positif"),
    ("AKRA Kuasai Penuh SPBU BP-AKR, KISI Sekuritas Proyeksikan Margin Tinggi Mulai 2027", "Akuisisi/Merger", "netral"),
    ("Saham ANTM terkoreksi, asing lepas saham", "Kepemilikan", "negatif"),
    ("Harga Saham PTBA Hari Ini, Selasa 10 November 2026", "Pergerakan harga", "netral"),
])
def test_news_labels(title, kind, tone):
    assert news.label(title) == (kind, tone)


def test_mentions_requires_code_or_name():
    assert news.mentions("BBCA cetak laba", "BBCA", None)
    assert not news.mentions("Saham bank naik", "BBCA", "PT Bank Central Asia Tbk")
    assert news.mentions("Bank Central Asia catat rekor", "BBCA", "PT Bank Central Asia Tbk")


def test_reaction_and_priced_in():
    df = frame(base=1000)
    pub = datetime.combine(df.index[-4].date(), datetime.min.time()).replace(hour=10, tzinfo=WIB).isoformat()
    df.iloc[-4:, df.columns.get_loc("Close")] = df["Close"].iloc[-5] * 1.08
    r = news.reaction(df, pub)
    assert r["priced_in"] and r["move_pct"] > 5 and r["days"] == 4
    late = datetime.combine(df.index[-1].date(), datetime.min.time()).replace(hour=19, tzinfo=WIB).isoformat()
    assert news.reaction(df, late)["days"] == 0  # setelah 16.00: baru bereaksi besok


def test_enrich_dedupes_and_archive_stats():
    df = frame(n=200)
    base = datetime.combine(df.index[-30].date(), datetime.min.time()).replace(hour=9, tzinfo=WIB)
    items = []
    for i in range(12):
        items.append({"code": "AAAA", "title": f"AAAA bagikan dividen tahap {i}", "url": "u", "source": "s", "via": "t",
                      "published": (base + timedelta(days=i)).isoformat()})
    items.append(dict(items[0]))  # duplikat
    e = news.enrich(items, {"AAAA": df})
    assert len(e) == 12
    arch = news.merge_archive({}, e, keep_days=10000)
    st = news.event_stats(arch, {"AAAA": df}, min_n=10)
    assert st and st[0]["kind"] == "Dividen" and st[0]["n"] >= 10 and st[0]["ready"]


def test_upcoming_window():
    ev = [{"code": "A", "date": "2026-11-12", "kind": "x"}, {"code": "A", "date": "2026-12-30", "kind": "x"},
          {"code": "B", "date": "2026-11-01", "kind": "x"}, {"code": "A", "date": "2026-11-12", "kind": "x"}]
    up = news.upcoming(ev, date(2026, 11, 11), days=10)
    assert [e["code"] for e in up] == ["A"]


# ---------------- track record ----------------
def test_track_snapshot_freeze_and_evaluate(tmp_path):
    plan = {"trigger": 1010, "limit": 1020, "sl": 990, "tp": 1040}
    res = {"date": "2026-11-10", "picks": [{"code": "AAAA", "score": 80, "plan": plan}], "params": {}}
    reg = {"status": "SEHAT", "level": "ok"}
    t = "2026-11-11"
    pre = datetime(2026, 11, 10, 18, 0, tzinfo=WIB)
    assert track.save_snapshot(tmp_path, t, pre, "tutup", res, None, reg, [])
    during = datetime(2026, 11, 11, 10, 0, tzinfo=WIB)
    res2 = {**res, "picks": []}
    assert not track.save_snapshot(tmp_path, t, during, "buka", res2, None, reg, [])  # beku saat sesi berjalan
    bars = {"AAAA": pd.DataFrame({"Open": [1000.0], "High": [1045.0], "Low": [1001.0], "Close": [1030.0], "Volume": [1e6]},
                                 index=[pd.Timestamp(t)])}
    out = track.evaluate_all(tmp_path, bars, date(2026, 11, 11), True, 0.4)
    r = out["days"][0]["picks"][0]["result"]
    assert r["status"] == "TP" and abs(r["net_pct"] - ((1040 / 1010 - 1) * 100 - 0.4)) < 0.01
    assert out["summary"]["all"]["trades"] == 1 and out["summary"]["followed"]["trades"] == 1


@pytest.mark.parametrize("o,h,l,c,status", [
    (1000, 1005, 995, 1000, "tidak terpicu"), (1030, 1050, 1025, 1040, "gap di atas limit"),
    (1000, 1045, 985, 1030, "SL"), (1000, 1020, 1001, 1015, "tutup sore"),
])
def test_evaluate_pick_cases(o, h, l, c, status):
    plan = {"trigger": 1010, "limit": 1020, "sl": 990, "tp": 1040}
    bar = pd.Series({"Open": o, "High": h, "Low": l, "Close": c})
    assert track.evaluate_pick(plan, bar, 0.4)["status"] == status


# ---------------- penyimpanan git ----------------
def test_store_pull_push_roundtrip(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=work, check=True)
    monkeypatch.chdir(work)
    import store
    s1 = tmp_path / "s1"
    store.pull(s1)
    (s1 / "track").mkdir()
    (s1 / "track" / "a.json").write_text("1")
    assert store.push(s1, "first")
    # seperti actions/checkout: kredensial disimpan sebagai extraheader dan harus ikut saat clone
    subprocess.run(["git", "config", "http.https://github.com/.extraheader", "AUTHORIZATION: basic dGVzdA=="], cwd=work, check=True)
    s2 = tmp_path / "s2"
    store.pull(s2)
    assert (s2 / "track" / "a.json").read_text() == "1"
    (s2 / "b.json").write_text("2")
    assert store.push(s2, "second")
    (s1 / "c.json").write_text("3")  # s1 tertinggal satu commit -> harus rebase lalu push
    assert store.push(s1, "third")
    s3 = tmp_path / "s3"
    store.pull(s3)
    assert {p.name for p in s3.iterdir()} >= {"b.json", "c.json", "track"}
    hdr = subprocess.run(["git", "config", "--get", "http.https://github.com/.extraheader"], cwd=s3, capture_output=True, text=True).stdout
    assert hdr.strip() == "AUTHORIZATION: basic dGVzdA=="


def test_weekly_report_text():
    from datetime import date
    import track
    days = [{"target": "2026-11-04", "would_trade": True, "picks": [{"code": "AAA", "result": {"status": "TP", "net_pct": 2.6}}]},
            {"target": "2026-11-05", "would_trade": False, "picks": [{"code": "BBB", "result": {"status": "SL", "net_pct": -2.4}}]},
            {"target": "2026-10-20", "would_trade": True, "picks": [{"code": "OLD", "result": {"status": "TP", "net_pct": 9.0}}]}]
    ev = [{"code": "BBCA", "kind": "Ex-date dividen", "date": "2026-11-12"}, {"code": "X", "kind": "RUPS", "date": "2026-11-30"}]
    title, body = track.weekly_report(days, date(2026, 11, 9), "tidak_layak", "TURUN", ev)
    assert title == "Laporan mingguan 09/11"
    assert "2 hari, 2/2 saham terpicu" in body and "+0,10%" in body and "OLD" not in body
    assert "TIDAK LAYAK" in body and "BBCA Ex-date dividen 12/11" in body and "RUPS" not in body
