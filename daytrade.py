"""
Daftar Pagi Day Trade.

Dihitung dari ringkasan harian BEI hari bursa terakhir (seluruh saham), untuk dipakai keesokan harinya:
  skor 0–100 = asing (35) + kekuatan penutupan (25) + lonjakan volume (15) + tren (15) + akumulasi OBV (10)
Rencana: beli HANYA jika harga menembus high kemarin (Auto Buy), TP & SL tetap, tutup sebelum 15.30.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from strategy import add_ticks, round_tick, tick_size

DEFAULTS = {
    "min_price": 150,
    "min_value_idr": 10_000_000_000,     # likuiditas harian minimal untuk day trade
    "max_change_pct": 15.0,               # hindari saham yang sudah melonjak terlalu tinggi
    "min_score": 60,
    "top_n": 5,
    "tp_pct": 3.0,
    "sl_pct": 2.0,
    "limit_ticks": 2,
    "exclude_remarks": [],                # isi kode notasi khusus BEI yang ingin dikecualikan
}


def features(panel: pd.DataFrame) -> pd.DataFrame:
    """Fitur per saham per hari. Semua kausal (hanya memakai data sampai hari itu)."""
    p = panel.sort_values(["code", "date"]).copy()
    g = p.groupby("code", group_keys=False)
    p["fnet"] = p["fbuy"] - p["fsell"]
    p["fnet_val"] = p["fnet"] * p["close"]
    p["fpos"] = (p["fnet"] > 0).astype(int)
    # jumlah hari berturut-turut asing net buy
    p["fstreak"] = g["fpos"].transform(lambda s: s.groupby((s != s.shift()).cumsum()).cumcount().add(1).where(s == 1, 0))
    p["fnet5"] = g["fnet_val"].transform(lambda s: s.rolling(5, min_periods=3).sum())
    p["val5"] = g["value"].transform(lambda s: s.rolling(5, min_periods=3).sum())
    p["vol_ma20"] = g["volume"].transform(lambda s: s.shift(1).rolling(20, min_periods=10).mean())
    p["val_ma20"] = g["value"].transform(lambda s: s.rolling(20, min_periods=10).mean())
    p["ma20"] = g["close"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    p["ma50"] = g["close"].transform(lambda s: s.rolling(50, min_periods=30).mean())
    sign = np.sign(p["close"] - p["prev"])
    p["obv"] = (sign * p["volume"]).groupby(p["code"]).cumsum()
    p["obv_up"] = p.groupby("code")["obv"].transform(lambda s: s.diff(10)) > 0
    rng = (p["high"] - p["low"]).replace(0, np.nan)
    p["close_pos"] = ((p["close"] - p["low"]) / rng).fillna(0.5)
    p["chg_pct"] = (p["close"] / p["prev"].replace(0, np.nan) - 1) * 100
    p["rvol"] = p["volume"] / p["vol_ma20"]
    return p


def score_rows(f: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    c = {**DEFAULTS, **(cfg or {})}
    s = pd.DataFrame(index=f.index)
    s["asing_hari_ini"] = f["fnet"] > 0
    s["asing_beruntun"] = f["fstreak"] >= 3
    s["asing_5hari"] = (f["fnet5"] > 0) & (f["fnet5"] >= 0.02 * f["val5"])
    s["tutup_kuat"] = f["close_pos"] >= 0.75
    s["naik_wajar"] = (f["chg_pct"] > 0) & (f["chg_pct"] <= 7)
    s["volume_naik"] = f["rvol"] >= 1.5
    s["tren"] = (f["close"] > f["ma20"]) & ((f["ma50"].isna()) | (f["ma20"] > f["ma50"]))
    s["akumulasi"] = f["obv_up"].fillna(False).astype(bool)
    f = f.copy()
    f["score"] = (10 * s["asing_hari_ini"] + 15 * s["asing_beruntun"] + 10 * s["asing_5hari"]
                  + 15 * s["tutup_kuat"] + 10 * s["naik_wajar"] + 15 * s["volume_naik"]
                  + 15 * s["tren"] + 10 * s["akumulasi"])
    if c.get("no_foreign"):  # tanpa data asing: skala ulang dari maksimum 65 poin ke 100
        f["score"] = (f["score"] / 65 * 100).round()
    excl = f["remarks"].apply(lambda r: any(x and x in r for x in c["exclude_remarks"]))
    f["eligible"] = ((f["close"] >= c["min_price"]) & (f["val_ma20"] >= c["min_value_idr"])
                     & (f["chg_pct"] < c["max_change_pct"]) & s["tren"] & ~excl)
    return pd.concat([f, s], axis=1)


LABELS = {
    "asing_hari_ini": "Asing net buy di hari terakhir",
    "asing_beruntun": "Asing net buy ≥ 3 hari berturut-turut",
    "asing_5hari": "Akumulasi asing 5 hari ≥ 2% nilai transaksi",
    "tutup_kuat": "Ditutup dekat harga tertinggi hari itu",
    "naik_wajar": "Naik, tetapi belum terlalu tinggi (≤ 7%)",
    "volume_naik": "Volume ≥ 1,5× rata-rata 20 hari",
    "tren": "Di atas MA20 (tren pendek naik)",
    "akumulasi": "OBV 10 hari naik (akumulasi)",
}


def plan(row: pd.Series, cfg: dict, capital: float, risk_pct: float, fee_total_pct: float) -> dict:
    c = {**DEFAULTS, **(cfg or {})}
    trigger = add_ticks(round_tick(row["high"], "up"), 1)
    limit = add_ticks(trigger, c["limit_ticks"])
    sl = round_tick(trigger * (1 - c["sl_pct"] / 100), "down")
    if sl >= trigger:
        sl = add_ticks(trigger, -1)
    tp = round_tick(trigger * (1 + c["tp_pct"] / 100), "up")
    if tp <= trigger:
        tp = add_ticks(trigger, 1)
    per = (limit - sl) + limit * fee_total_pct / 100
    lots = max(0, math.floor(capital * risk_pct / 100 / per / 100)) if per > 0 else 0
    lots = min(lots, math.floor(capital * 0.5 / (limit * 100)))  # maks 50% modal per saham
    return {"trigger": trigger, "limit": limit, "sl": sl, "tp": tp, "lots": int(lots),
            "sl_pct": round((trigger - sl) / trigger * 100, 2), "tp_pct": round((tp - trigger) / trigger * 100, 2),
            "tick": tick_size(trigger)}


def morning_list(panel: pd.DataFrame, cfg: dict, capital: float, risk_pct: float, fee_total_pct: float) -> dict:
    if panel.empty:
        return {"date": None, "picks": [], "scanned": 0}
    sc = score_rows(features(panel), cfg)
    last_date = sc["date"].max()
    day = sc[sc["date"] == last_date]
    c = {**DEFAULTS, **(cfg or {})}
    cand = day[day["eligible"] & (day["score"] >= c["min_score"])].sort_values(
        ["score", "fnet_val"], ascending=False).head(c["top_n"])
    picks = []
    for _, r in cand.iterrows():
        picks.append({
            "code": r["code"], "score": int(r["score"]),
            "close": float(r["close"]), "high": float(r["high"]), "low": float(r["low"]),
            "chg_pct": round(float(r["chg_pct"]), 2), "value": float(r["value"]),
            "rvol": round(float(r["rvol"]), 2) if pd.notna(r["rvol"]) else None,
            "fnet_val": float(r["fnet_val"]), "fnet5": float(r["fnet5"]) if pd.notna(r["fnet5"]) else 0.0,
            "fstreak": int(r["fstreak"]),
            "checks": [{"label": LABELS[k], "ok": bool(r[k])} for k in LABELS],
            "plan": plan(r, cfg, capital, risk_pct, fee_total_pct),
        })
    return {"date": str(last_date.date()), "picks": picks, "scanned": int(day["code"].nunique()),
            "eligible": int(day["eligible"].sum())}


def _simulate(sc: pd.DataFrame, cfg: dict, fee_total_pct: float, day_from: int = 30, day_to: int | None = None) -> list[dict]:
    """Simulasi rencana pada skor yang sudah dihitung. Sinyal hari ke-i dieksekusi di hari ke-(i+1)."""
    c = {**DEFAULTS, **(cfg or {})}
    dates = sorted(sc["date"].unique())
    by_day = {d: g.set_index("code") for d, g in sc.groupby("date")}
    trades = []
    end = min(day_to if day_to is not None else len(dates) - 1, len(dates) - 1)
    for i in range(max(day_from, 0), end):
        today, nxt = by_day[dates[i]], by_day[dates[i + 1]]
        cand = today[today["eligible"] & (today["score"] >= c["min_score"])].sort_values("score", ascending=False).head(c["top_n"])
        for code, r in cand.iterrows():
            if code not in nxt.index:
                continue
            n = nxt.loc[code]
            p = plan(r, c, 1e12, 1, fee_total_pct)
            if n["high"] < p["trigger"] or n["open"] > p["limit"]:
                continue
            entry = max(p["trigger"], n["open"])
            if n["low"] <= p["sl"]:
                exit_, why = p["sl"], "SL"
            elif n["high"] >= p["tp"]:
                exit_, why = p["tp"], "TP"
            else:
                exit_, why = n["close"], "tutup sore"
            net = (exit_ / entry - 1) * 100 - fee_total_pct
            trades.append({"date": str(pd.Timestamp(dates[i + 1]).date()), "code": code, "entry": float(entry),
                           "exit": float(exit_), "why": why, "net_pct": round(float(net), 2)})
    return trades


def _stats(trades: list[dict], days: int) -> dict:
    if not trades:
        return {"trades": 0, "days": days}
    t = pd.DataFrame(trades)
    wins, losses = t[t["net_pct"] > 0], t[t["net_pct"] <= 0]
    return {
        "days": days, "trades": int(len(t)),
        "win_rate": round(len(wins) / len(t) * 100, 1),
        "avg_pct": round(float(t["net_pct"].mean()), 2),
        "total_pct": round(float(t["net_pct"].sum()), 1),
        "avg_win": round(float(wins["net_pct"].mean()), 2) if len(wins) else 0.0,
        "avg_loss": round(float(losses["net_pct"].mean()), 2) if len(losses) else 0.0,
        "by_exit": t["why"].value_counts().to_dict(),
        "recent": t.tail(40).iloc[::-1].to_dict("records"),
    }


def backtest(panel: pd.DataFrame, cfg: dict, fee_total_pct: float, sc: pd.DataFrame | None = None) -> dict:
    """Uji rencana pada seluruh histori. Konservatif: jika SL dan TP sama-sama tersentuh di hari yang sama,
    dihitung SL. Sisa posisi ditutup di harga penutupan."""
    sc = sc if sc is not None else score_rows(features(panel), cfg)
    return _stats(_simulate(sc, cfg, fee_total_pct), sc["date"].nunique())


GRID = {"min_score": [60, 70, 80], "tp_pct": [2.0, 3.0, 4.0], "sl_pct": [1.5, 2.0, 3.0]}


def optimize(panel: pd.DataFrame, cfg: dict, fee_total_pct: float, train_frac: float = 0.6,
             min_train_trades: int = 15, min_test_trades: int = 8) -> dict:
    """Penyetelan otomatis dengan validasi walk-forward:
    1) pilih setelan terbaik (rata-rata hasil per transaksi) di periode LATIH (60% awal),
    2) uji setelan itu di periode UJI (40% akhir) yang tidak dipakai untuk memilih,
    3) setelan dipakai hanya jika hasil di periode UJI positif setelah fee."""
    sc = score_rows(features(panel), cfg)
    n = sc["date"].nunique()
    split = 30 + int((n - 31) * train_frac)
    results = []
    for ms in GRID["min_score"]:
        for tp in GRID["tp_pct"]:
            for sl in GRID["sl_pct"]:
                c = {**(cfg or {}), "min_score": ms, "tp_pct": tp, "sl_pct": sl}
                tr = _stats(_simulate(sc, c, fee_total_pct, 30, split), split - 30)
                results.append({"min_score": ms, "tp_pct": tp, "sl_pct": sl,
                                "train_trades": tr["trades"], "train_avg": tr.get("avg_pct")})
    ok = [r for r in results if r["train_trades"] >= min_train_trades]
    if not ok:
        return {"status": "data_kurang", "applied": False, "days": n, "grid": results,
                "note": "Transaksi di periode latih terlalu sedikit untuk memilih setelan."}
    best = max(ok, key=lambda r: (r["train_avg"], r["train_trades"]))
    c = {**(cfg or {}), **{k: best[k] for k in GRID}}
    test = _stats(_simulate(sc, c, fee_total_pct, split, None), n - split)
    applied = test["trades"] >= min_test_trades and test.get("avg_pct", -1) > 0
    status = "layak" if applied else ("data_kurang" if test["trades"] < min_test_trades else "tidak_layak")
    return {"status": status, "applied": applied, "days": n,
            "train_days": split - 30, "test_days": n - split,
            "params": {k: best[k] for k in GRID}, "train_avg": best["train_avg"], "train_trades": best["train_trades"],
            "test": test, "grid": sorted(results, key=lambda r: -(r["train_avg"] or -99))[:10]}
