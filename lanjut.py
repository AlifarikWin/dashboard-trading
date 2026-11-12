"""
Kandidat lanjut di sesi 2.

Saat sesi 1 ditutup, saham dipilih dari kondisi sesi 1:
- Lanjut naik  : naik ≥ 2% sejak penutupan kemarin, ditutup di ≥ 80% rentang sesi 1, volume sesi 1 ≥ 0,6× rata-rata
                 volume harian 20 hari, tren harian naik (kemarin: close > MA20 > MA50), likuid, belum mendekati ARA.
- Lanjut turun : turun ≥ 2%, ditutup di ≤ 20% rentang sesi 1, volume sama, tren harian turun (close < MA20).
Uji historis (bar 60 menit, 1 tahun): beli di harga akhir sesi 1, jual di penutupan hari itu, dikurangi fee.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

P = {"s1_min": 2.0, "pos_up": 0.8, "pos_dn": 0.2, "vr": 0.6, "min_value": 5e9, "min_price": 100, "ara_gap": 3.0,
     "top_n": 5, "min_trades": 30, "recent_days": 60}


def _ara(prev):
    prev = np.asarray(prev, float)
    return np.where(prev <= 200, 35.0, np.where(prev <= 5000, 25.0, 20.0))


def score(pos, vr, ret):
    return float(round(40 * min(max(pos, 0), 1) + 30 * min(vr / 1.5, 1) + 30 * min(abs(ret) / 6, 1)))


def classify(ret, pos, vr, prev, trend_up, trend_dn, value20, price) -> str | None:
    """'up', 'down', atau None — satu fungsi untuk uji historis dan daftar harian agar syaratnya identik."""
    if value20 is None or value20 < P["min_value"] or price < P["min_price"] or vr is None or vr < P["vr"]:
        return None
    if ret >= P["s1_min"] and pos >= P["pos_up"] and trend_up and ret <= float(_ara([prev])[0]) - P["ara_gap"]:
        return "up"
    if ret <= -P["s1_min"] and pos <= P["pos_dn"] and trend_dn:
        return "down"
    return None


# ----------------------------------------------------------------------------
# Data sesi 1 historis dari bar 60 menit
# ----------------------------------------------------------------------------
def hourly_s1(codes: list[str], period: str = "1y", chunk: int = 80) -> dict[str, pd.DataFrame]:
    """{kode: DataFrame per tanggal: s1_high, s1_low, s1_close, s1_vol} dari bar yang dimulai sebelum 12.00 WIB."""
    import time
    import traceback
    import yfinance as yf
    out = {}
    for i in range(0, len(codes), chunk):
        part = codes[i:i + chunk]
        syms = [f"{c}.JK" for c in part]
        try:
            d = yf.download(syms, period=period, interval="60m", group_by="ticker", auto_adjust=False,
                            threads=True, progress=False)
        except Exception:
            traceback.print_exc()
            continue
        for c, sym in zip(part, syms):
            try:
                x = d[sym] if isinstance(d.columns, pd.MultiIndex) else d
                x = x.dropna(subset=["Close"])
                if x.empty:
                    continue
                idx = x.index.tz_convert("Asia/Jakarta") if x.index.tz is not None else x.index.tz_localize("Asia/Jakarta")
                x = x.set_axis(idx)
                x = x[x.index.hour < 12]
                g = x.groupby(x.index.tz_localize(None).normalize())
                out[c] = pd.DataFrame({"s1_high": g["High"].max(), "s1_low": g["Low"].min(),
                                       "s1_close": g["Close"].last(), "s1_vol": g["Volume"].sum()})
            except Exception:
                continue
        time.sleep(1)
    return out


def _stats(x: pd.Series, fee: float) -> dict:
    x = x.dropna()
    if not len(x):
        return {"n": 0}
    net = x - fee
    return {"n": int(len(x)), "up_pct": round(float((x > 0).mean() * 100), 1), "avg_s2": round(float(x.mean()), 2),
            "win_pct": round(float((net > 0).mean() * 100), 1), "avg_net": round(float(net.mean()), 2),
            "median_net": round(float(net.median()), 2), "worst": round(float(net.min()), 2)}


def test(frames: dict[str, pd.DataFrame], s1: dict[str, pd.DataFrame], fee: float) -> dict:
    up_rows, dn_rows, base = [], [], []
    for code, h in s1.items():
        df = frames.get(code)
        if df is None or len(df) < 60 or h.empty:
            continue
        C, V = df["Close"], df["Volume"]
        f = pd.DataFrame({"prev": C.shift(1), "close": C, "ma20p": C.rolling(20).mean().shift(1),
                          "ma50p": C.rolling(50).mean().shift(1), "av20": V.shift(1).rolling(20).mean(),
                          "val20": (C * V).shift(1).rolling(20).mean()})
        j = f.join(h, how="inner").dropna()
        if j.empty:
            continue
        rng = (j["s1_high"] - j["s1_low"]).replace(0, np.nan)
        j = j.assign(ret=(j["s1_close"] / j["prev"] - 1) * 100, pos=((j["s1_close"] - j["s1_low"]) / rng).fillna(0.5),
                     vr=j["s1_vol"] / j["av20"], s2=(j["close"] / j["s1_close"] - 1) * 100)
        up = (j["prev"] > j["ma20p"]) & (j["ma20p"] > j["ma50p"])
        dn = j["prev"] < j["ma20p"]
        cls = [classify(r.ret, r.pos, r.vr, r.prev, u, d_, r.val20, r.s1_close)
               for r, u, d_ in zip(j.itertuples(), up, dn)]
        j["cls"] = cls
        liq = j["val20"] >= P["min_value"]
        base.append(j.loc[liq, "s2"])
        for k, bucket in (("up", up_rows), ("down", dn_rows)):
            sel = j[j["cls"] == k]
            if len(sel):
                bucket.append(pd.DataFrame({"date": sel.index, "code": code, "s2": sel["s2"].values,
                                            "score": [score(a, b, c) for a, b, c in zip(sel["pos"] if k == "up" else 1 - sel["pos"], sel["vr"], sel["ret"])]}))
    out = {"fee_pct": fee, "params": P, "stocks": len(s1)}
    all_dates = sorted({d for h in s1.values() for d in h.index})
    recent_from = all_dates[-P["recent_days"]] if len(all_dates) > P["recent_days"] else (all_dates[0] if all_dates else None)
    if up_rows:
        u = pd.concat(up_rows).sort_values(["date", "score"], ascending=[True, False]).groupby("date").head(P["top_n"])
        out["up"] = _stats(u["s2"], fee)
        out["up_recent"] = _stats(u.loc[u["date"] >= recent_from, "s2"], fee)
    else:
        out["up"] = out["up_recent"] = {"n": 0}
    if dn_rows:
        dd = pd.concat(dn_rows).sort_values(["date", "score"], ascending=[True, False]).groupby("date").head(P["top_n"])
        s = dd["s2"]
        out["down"] = {"n": int(len(s)), "down_pct": round(float((s < 0).mean() * 100), 1), "avg_s2": round(float(s.mean()), 2)}
    else:
        out["down"] = {"n": 0}
    b = pd.concat(base) if base else pd.Series(dtype=float)
    out["baseline"] = {"n": int(len(b)), "avg_s2": round(float(b.mean()), 2) if len(b) else None,
                       "up_pct": round(float((b > 0).mean() * 100), 1) if len(b) else None}
    U, R = out["up"], out["up_recent"]
    out["status"] = ("data_kurang" if U.get("n", 0) < P["min_trades"] else
                     "layak" if U["avg_net"] > 0 and R.get("n", 0) >= 10 and R["avg_net"] > 0 else "tidak_layak")
    return out


# ----------------------------------------------------------------------------
# Daftar harian dari snapshot akhir sesi 1
# ----------------------------------------------------------------------------
def candidates(snap: dict, base: dict[str, dict], names: dict[str, str], blocked: set[str]) -> dict:
    """snap['q'][kode] = [harga, volume, prev, high, low] saat akhir sesi 1."""
    up, dn = [], []
    for code, a in (snap.get("q") or {}).items():
        if code in blocked or len(a) < 5 or not all(a[i] for i in (0, 2, 3, 4)):
            continue
        b = base.get(code) or {}
        if None in (b.get("ma20"), b.get("ma50"), b.get("av20"), b.get("val20"), b.get("close")) or not b["av20"]:
            continue
        p, vol, prev, h, l = a[:5]
        ret = (p / prev - 1) * 100
        pos = (p - l) / (h - l) if h > l else 0.5
        vr = (vol or 0) / b["av20"]
        k = classify(ret, pos, vr, prev, b["close"] > b["ma20"] > b["ma50"], b["close"] < b["ma20"], b["val20"], p)
        if not k:
            continue
        row = {"c": code, "n": names.get(code, code), "p1": float(p), "ret": round(ret, 2), "pos": round(pos, 2),
               "vr": round(vr, 2), "score": score(pos if k == "up" else 1 - pos, vr, ret)}
        (up if k == "up" else dn).append(row)
    up.sort(key=lambda x: -x["score"])
    dn.sort(key=lambda x: -x["score"])
    return {"up": up[:P["top_n"]], "down": dn[:P["top_n"]], "up_more": max(0, len(up) - P["top_n"]),
            "down_more": max(0, len(dn) - P["top_n"])}


def monitor(c: dict, prices: dict[str, float]) -> dict:
    """Tambahkan perubahan sesi 2 terkini (harga sekarang ÷ harga akhir sesi 1)."""
    def add(rows):
        return [{**r, "s2": round((prices[r["c"]] / r["p1"] - 1) * 100, 2) if prices.get(r["c"]) else None} for r in rows]
    return {**c, "up": add(c.get("up", [])), "down": add(c.get("down", []))}


# ----------------------------------------------------------------------------
# Track record: daftar disimpan saat sesi 1 final, dinilai dengan harga penutupan
# ----------------------------------------------------------------------------
def save_track(store: Path, d: date, c: dict, status: str | None) -> None:
    f = store / "track" / "lanjut" / f"{d}.json"
    if f.exists():
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"date": str(d), "status": status, "saved_at": datetime.now().isoformat(timespec="minutes"),
                             "up": [{"c": r["c"], "p1": r["p1"]} for r in c.get("up", [])],
                             "down": [{"c": r["c"], "p1": r["p1"]} for r in c.get("down", [])]}))


def close_track(store: Path, d: date, closes: dict[str, float], fee: float) -> None:
    f = store / "track" / "lanjut" / f"{d}.json"
    if not f.exists():
        return
    t = json.loads(f.read_text())
    if t.get("closed"):
        return
    for k in ("up", "down"):
        for r in t[k]:
            px = closes.get(r["c"])
            if px:
                s2 = (px / r["p1"] - 1) * 100
                r["s2"] = round(s2, 2)
                if k == "up":
                    r["net"] = round(s2 - fee, 2)
    t["closed"] = True
    f.write_text(json.dumps(t))


def track_summary(store: Path) -> dict:
    d = store / "track" / "lanjut"
    days = [json.loads(f.read_text()) for f in sorted(d.glob("*.json"))] if d.exists() else []
    nets = [r["net"] for t in days if t.get("closed") for r in t["up"] if r.get("net") is not None]
    dn = [r["s2"] for t in days if t.get("closed") for r in t["down"] if r.get("s2") is not None]
    a = np.array(nets)
    return {"days": len(days), "first_date": days[0]["date"] if days else None,
            "up": {"trades": len(nets), **({"win_pct": round(float((a > 0).mean() * 100), 1), "avg_net": round(float(a.mean()), 2)} if len(a) else {})},
            "down": {"n": len(dn), **({"down_pct": round(float((np.array(dn) < 0).mean() * 100), 1)} if dn else {})}}
