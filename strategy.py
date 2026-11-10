"""
Logika sinyal saham BEI — Breakout + Volume.

Sinyal per saham:
  BUY    : harga menembus high 20 hari, volume terproyeksi >= 1,5x rata-rata, tren naik (> MA50)
  SELL   : harga jebol low 10 hari dengan volume, atau menembus ke bawah MA20 / MA50
  WATCH  : tren naik dan harga <= 3% di bawah resistance (kandidat breakout, siapkan auto order)
  NETRAL : tidak memenuhi kondisi di atas
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, time

import numpy as np
import pandas as pd

LOT = 100


# ----------------------------------------------------------------------------
# Aturan harga BEI
# ----------------------------------------------------------------------------
def tick_size(price: float) -> int:
    """Fraksi harga pasar reguler BEI (verifikasi berkala ke idx.co.id)."""
    if price < 200:
        return 1
    if price < 500:
        return 2
    if price < 2000:
        return 5
    if price < 5000:
        return 10
    return 25


def round_tick(price: float, mode: str = "nearest") -> int:
    t = tick_size(price)
    f = {"up": math.ceil, "down": math.floor}.get(mode, round)
    p = int(f(price / t) * t)
    t2 = tick_size(p)  # fraksi bisa berubah saat melewati batas kelompok harga
    if t2 != t:
        p = int(f(p / t2) * t2)
    return max(p, 1)


def add_ticks(price: int, n: int) -> int:
    p = int(price)
    for _ in range(abs(n)):
        p = p + tick_size(p) if n > 0 else p - tick_size(p - 1)
    return p


# ----------------------------------------------------------------------------
# Indikator
# ----------------------------------------------------------------------------
def add_indicators(df: pd.DataFrame, s: dict) -> pd.DataFrame:
    d = df.copy()
    d["res"] = d["High"].shift(1).rolling(s["breakout_lookback"]).max()   # resistance sebelum hari ini
    d["sup"] = d["Low"].shift(1).rolling(s["support_lookback"]).min()     # support sebelum hari ini
    d["vol_ma"] = d["Volume"].shift(1).rolling(s["volume_ma"]).mean()
    d["ma_fast"] = d["Close"].rolling(s["fast_ma"]).mean()
    d["ma_trend"] = d["Close"].rolling(s["trend_ma"]).mean()
    prev_c = d["Close"].shift(1)
    tr = pd.concat([d["High"] - d["Low"], (d["High"] - prev_c).abs(),
                    (d["Low"] - prev_c).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1 / s["atr_period"], adjust=False).mean()
    d["value"] = d["Close"] * d["Volume"]
    return d


# ----------------------------------------------------------------------------
# Rencana order & ukuran posisi
# ----------------------------------------------------------------------------
@dataclass
class Plan:
    entry: int
    limit: int
    sl: int
    tp: int
    tp2: int
    sl_pct: float
    lots: int
    risk_idr: float


def position_lots(entry: float, sl: float, risk: dict) -> tuple[int, float]:
    capital = risk["capital_idr"]
    budget = capital * risk["risk_per_trade_pct"] / 100
    per_share = (entry - sl) + entry * (risk["fee_buy_pct"] + risk["fee_sell_pct"]) / 100
    if per_share <= 0:
        return 0, 0.0
    lots = min(math.floor(budget / per_share / LOT),
               math.floor(capital * risk["max_position_pct"] / 100 / (entry * LOT)))
    lots = max(lots, 0)
    return lots, lots * LOT * per_share


def make_plan(entry: int, atr: float, s: dict, risk: dict) -> Plan | None:
    limit = add_ticks(entry, s["limit_slippage_ticks"])
    sl = round_tick(entry - s["sl_atr_mult"] * atr, "down")
    sl_pct = (entry - sl) / entry * 100
    if sl >= entry or sl_pct > s["max_sl_pct"]:
        return None
    r = entry - sl
    lots, risk_idr = position_lots(limit, sl, risk)
    return Plan(entry, limit, sl, round_tick(entry + s["tp_r"] * r, "down"),
                round_tick(entry + s["tp2_r"] * r, "down"), round(sl_pct, 2), lots, round(risk_idr))


# ----------------------------------------------------------------------------
# Sesi bursa
# ----------------------------------------------------------------------------
def _spans(now: datetime, cfg_session: dict):
    sessions = cfg_session["fri"] if now.weekday() == 4 else cfg_session["mon_thu"]
    m = lambda t: t.hour * 60 + t.minute
    return [(m(time.fromisoformat(a)), m(time.fromisoformat(b))) for a, b in sessions]


def market_state(now: datetime, cfg_session: dict) -> tuple[str, float]:
    """Kembalikan (status, progres sesi 0–1). Status: pra-buka / buka / istirahat / tutup / libur-akhir-pekan."""
    if now.weekday() > 4:
        return "libur akhir pekan", 1.0
    spans = _spans(now, cfg_session)
    total = sum(b - a for a, b in spans)
    cur = now.hour * 60 + now.minute
    done = sum(max(0, min(cur, b) - a) for a, b in spans)
    if cur < spans[0][0]:
        return "pra-buka", 0.0
    if any(a <= cur < b for a, b in spans):
        return "buka", done / total
    if cur < spans[-1][1]:
        return "istirahat", done / total
    return "tutup", 1.0


# ----------------------------------------------------------------------------
# Evaluasi sinyal
# ----------------------------------------------------------------------------
def evaluate(ticker: str, d: pd.DataFrame, s: dict, risk: dict, progress: float = 1.0) -> dict:
    """Evaluasi baris terakhir `d` (hasil add_indicators). progress = porsi sesi hari ini yang sudah berjalan."""
    row, prev = d.iloc[-1], d.iloc[-2]
    price = float(row["Close"])
    f = max(progress, 0.1)  # hindari proyeksi volume liar di menit awal
    rvol = float(row["Volume"] / (row["vol_ma"] * f)) if row["vol_ma"] else 0.0
    out = {
        "ticker": ticker, "price": price, "prev_close": float(prev["Close"]),
        "change_pct": round((price / prev["Close"] - 1) * 100, 2),
        "res": round_tick(row["res"]) if not pd.isna(row["res"]) else None,
        "sup": round_tick(row["sup"]) if not pd.isna(row["sup"]) else None,
        "ma_fast": round(float(row["ma_fast"]), 1), "ma_trend": round(float(row["ma_trend"]), 1),
        "rvol": round(rvol, 2), "value": float(row["value"]),
        "signal": "NETRAL", "reasons": [], "plan": None, "plan_type": None,
    }
    if any(pd.isna(row[c]) for c in ["res", "sup", "vol_ma", "ma_trend", "ma_fast"]):
        out["reasons"].append("data historis belum cukup")
        return out

    liquid = price >= s["min_price"] and row["value"] >= s["min_value_idr"] * f
    trend_up = price > row["ma_trend"]
    res, sup = row["res"], row["sup"]

    # --- BUY: breakout + volume
    if liquid and trend_up and price > res and rvol >= s["breakout_rvol_min"]:
        if price > res * (1 + s["max_extension_pct"] / 100):
            out["reasons"].append(f"breakout sudah >{s['max_extension_pct']:g}% di atas resistance — jangan dikejar")
            out["signal"] = "WATCH"
            return out
        plan = make_plan(round_tick(price, "up"), row["atr"], s, risk)
        if plan:
            out.update(signal="BUY", plan=asdict(plan), plan_type="beli sekarang")
            out["reasons"] += [f"tembus high {s['breakout_lookback']} hari ({round_tick(res)})",
                               f"volume {rvol:.1f}x rata-rata", f"di atas MA{s['trend_ma']}"]
            return out
        out["reasons"].append("breakout, tetapi jarak stop loss terlalu lebar")

    # --- SELL: breakdown / tembus MA ke bawah
    sell = []
    if price < sup and rvol >= s["breakdown_rvol_min"]:
        sell.append(f"jebol low {s['support_lookback']} hari ({round_tick(sup)}) dengan volume {rvol:.1f}x")
    if prev["Close"] >= prev["ma_fast"] and price < row["ma_fast"]:
        sell.append(f"tembus ke bawah MA{s['fast_ma']}")
    if prev["Close"] >= prev["ma_trend"] and price < row["ma_trend"]:
        sell.append(f"tembus ke bawah MA{s['trend_ma']} (tren rusak)")
    if sell:
        out.update(signal="SELL", reasons=out["reasons"] + sell)
        return out

    # --- WATCH: kandidat breakout, siapkan buy-stop
    if liquid and trend_up and price >= res * (1 - s["near_resistance_pct"] / 100) and price <= res:
        plan = make_plan(add_ticks(round_tick(res, "up"), 1), row["atr"], s, risk)
        if plan:
            out.update(signal="WATCH", plan=asdict(plan), plan_type="buy-stop (auto order)")
            out["reasons"].append(f"{(res / price - 1) * 100:.1f}% di bawah resistance {round_tick(res)}")
            return out

    if not liquid:
        out["reasons"].append("likuiditas/harga di bawah filter")
    elif not trend_up:
        out["reasons"].append(f"di bawah MA{s['trend_ma']} (tren turun)")
    return out


# ----------------------------------------------------------------------------
# Backtest (aproksimasi harian dari sinyal dashboard)
# ----------------------------------------------------------------------------
@dataclass
class Trade:
    ticker: str
    entry_date: str
    exit_date: str
    entry: float
    exit: float
    sl: float
    days: int
    reason: str
    net_pct: float
    r: float


def backtest_ticker(t: str, df: pd.DataFrame, cfg: dict) -> list[Trade]:
    """BUY di harga close hari sinyal. Keluar saat SL/TP tersentuh, sinyal SELL (di close),
    atau batas waktu. Bila SL & TP tersentuh di hari yang sama, dianggap kena SL (konservatif)."""
    s, risk, bt = cfg["strategy"], cfg["risk"], cfg["backtest"]
    fee = (risk["fee_buy_pct"] + risk["fee_sell_pct"]) / 100
    d = add_indicators(df, s)
    trades, i, n = [], s["trend_ma"] + 1, len(d)
    while i < n - 1:
        ev = evaluate(t, d.iloc[: i + 1], s, risk)
        if ev["signal"] != "BUY":
            i += 1
            continue
        p = ev["plan"]
        entry, j, exit_px, reason = float(d["Close"].iloc[i]), i, None, ""
        while exit_px is None:
            j += 1
            if j >= n:
                j, exit_px, reason = n - 1, float(d["Close"].iloc[-1]), "akhir data"
                break
            r = d.iloc[j]
            if r["Open"] <= p["sl"]:
                exit_px, reason = r["Open"], "SL (gap)"
            elif r["Open"] >= p["tp"]:
                exit_px, reason = r["Open"], "TP (gap)"
            elif r["Low"] <= p["sl"]:
                exit_px, reason = p["sl"], "SL"
            elif r["High"] >= p["tp"]:
                exit_px, reason = p["tp"], "TP"
            elif evaluate(t, d.iloc[: j + 1], s, risk)["signal"] == "SELL":
                exit_px, reason = r["Close"], "sinyal SELL"
            elif j - i >= bt["max_hold_days"]:
                exit_px, reason = r["Close"], "batas waktu"
        net = (exit_px / entry - 1 - fee) * 100
        trades.append(Trade(t, str(d.index[i].date()), str(d.index[j].date()), entry, float(exit_px),
                            p["sl"], j - i, reason, round(net, 2),
                            round(net / 100 * entry / (entry - p["sl"]), 2)))
        i = j + 1
    return trades


def summarize(trades: list[Trade]) -> dict:
    if not trades:
        return {"trades": 0}
    r = np.array([x.r for x in trades])
    pct = np.array([x.net_pct for x in trades])
    wins, losses = r[r > 0], r[r <= 0]
    streak = cur = 0
    for v in r:
        cur = cur + 1 if v <= 0 else 0
        streak = max(streak, cur)
    return {
        "trades": int(len(r)),
        "win_rate": round(len(wins) / len(r) * 100, 1),
        "avg_r": round(float(r.mean()), 2),
        "total_r": round(float(r.sum()), 1),
        "avg_pct": round(float(pct.mean()), 2),
        "profit_factor": round(float(wins.sum() / abs(losses.sum())), 2) if losses.sum() else None,
        "max_loss_streak": int(streak),
        "avg_days": round(float(np.mean([x.days for x in trades])), 1),
    }


# ----------------------------------------------------------------------------
# Radar akumulasi (sinyal awal SEBELUM breakout — peluang, bukan kepastian)
# ----------------------------------------------------------------------------
RADAR_MIN = 60


def radar_frame(df: pd.DataFrame, s: dict) -> pd.DataFrame:
    """Skor 0–100 per hari. Semua kolom kausal (hanya memakai data hingga hari itu)."""
    d = add_indicators(df, s)
    c, v = d["Close"], d["Volume"]
    prev_c = c.shift(1)
    tr = pd.concat([d["High"] - d["Low"], (d["High"] - prev_c).abs(), (d["Low"] - prev_c).abs()], axis=1).max(axis=1)
    obv = (np.sign(c.diff()).fillna(0) * v).cumsum()
    up_v = v.where(c > prev_c, 0).rolling(20).sum()
    dn_v = v.where(c < prev_c, 0).rolling(20).sum()

    f = pd.DataFrame(index=d.index)
    f["tren"] = (c > d["ma_trend"]) & (d["ma_trend"] > d["ma_trend"].shift(10))
    f["dekat_puncak"] = c >= d["High"].rolling(60).max() * 0.92
    f["menyempit"] = ((tr.ewm(alpha=0.1, adjust=False).mean() / tr.rolling(50).mean()) < 0.8) | \
                     (((d["High"].rolling(10).max() - d["Low"].rolling(10).min()) / c) < 0.10)
    f["volume_kering"] = v.rolling(10).mean() < 0.85 * v.rolling(50).mean()
    f["obv_naik"] = obv.diff(20) > 0
    f["obv_diam"] = f["obv_naik"] & ((c / c.shift(20) - 1).abs() < 0.06)
    f["beli_dominan"] = (up_v / dn_v.replace(0, np.nan)) > 1.2
    f["belum_tembus"] = c <= d["res"]
    f["likuid"] = (c >= s["min_price"]) & (d["value"].rolling(20).mean() >= s["min_value_idr"])
    f["score"] = (20 * f["tren"] + 15 * f["dekat_puncak"] + 20 * f["menyempit"] + 15 * f["volume_kering"]
                  + np.where(f["obv_diam"], 20, np.where(f["obv_naik"], 10, 0)) + 10 * f["beli_dominan"])
    f["kandidat"] = (f["score"] >= RADAR_MIN) & f["tren"] & f["belum_tembus"] & f["likuid"]
    f["res"], f["atr"] = d["res"], d["atr"]
    return f


RADAR_LABEL = {
    "tren": "tren naik (di atas MA50 yang menanjak)",
    "dekat_puncak": "dekat harga tertinggi 60 hari",
    "menyempit": "pergerakan menyempit (volatilitas turun)",
    "volume_kering": "volume mengering — penjual mulai habis",
    "obv_diam": "OBV naik saat harga datar — indikasi akumulasi diam-diam",
    "obv_naik": "OBV naik",
    "beli_dominan": "volume hari naik > volume hari turun",
}


def radar(ticker: str, df: pd.DataFrame, s: dict, risk: dict) -> dict:
    f = radar_frame(df, s)
    r = f.iloc[-1]
    checks = []
    for k in ["tren", "dekat_puncak", "menyempit", "volume_kering", "beli_dominan"]:
        checks.append({"label": RADAR_LABEL[k], "ok": bool(r[k])})
    checks.insert(4, {"label": RADAR_LABEL["obv_diam"] if r["obv_diam"] else RADAR_LABEL["obv_naik"],
                      "ok": bool(r["obv_naik"]), "partial": bool(r["obv_naik"] and not r["obv_diam"])})
    out = {"ticker": ticker, "score": int(r["score"]), "kandidat": bool(r["kandidat"]),
           "checks": checks, "plan": None}
    if r["kandidat"] and not pd.isna(r["res"]):
        p = make_plan(add_ticks(round_tick(r["res"], "up"), 1), r["atr"], s, risk)
        out["plan"] = asdict(p) if p else None
    return out


def radar_validation(data: dict[str, pd.DataFrame], s: dict, horizon: int = 10) -> dict:
    """Seberapa sering kandidat radar benar-benar breakout / naik dalam `horizon` hari, dibanding semua hari."""
    rows = []
    for df in data.values():
        f = radar_frame(df, s)
        hi_fwd = df["High"][::-1].rolling(horizon, min_periods=horizon).max()[::-1].shift(-1)
        ret = df["Close"].shift(-horizon) / df["Close"] - 1
        g = pd.DataFrame({"kandidat": f["kandidat"], "likuid": f["likuid"] & f["tren"].notna(),
                          "breakout": hi_fwd > f["res"], "ret": ret}).iloc[60:].dropna()
        rows.append(g[g["likuid"]])
    if not rows:
        return {}
    g = pd.concat(rows)

    def stat(x):
        return {"n": int(len(x)), "breakout_pct": round(float(x["breakout"].mean() * 100), 1) if len(x) else None,
                "naik_pct": round(float((x["ret"] > 0).mean() * 100), 1) if len(x) else None,
                "ret_avg": round(float(x["ret"].mean() * 100), 2) if len(x) else None}
    return {"horizon": horizon, "radar": stat(g[g["kandidat"]]), "semua": stat(g)}


# ----------------------------------------------------------------------------
# Kondisi pasar (IHSG)
# ----------------------------------------------------------------------------
def market_regime(ihsg: pd.DataFrame | None) -> dict:
    if ihsg is None or len(ihsg) < 60:
        return {"status": "tidak diketahui", "level": "unknown", "note": "data IHSG tidak tersedia"}
    c = ihsg["Close"]
    ma20, ma50 = c.rolling(20).mean().iloc[-1], c.rolling(50).mean().iloc[-1]
    last, prev = float(c.iloc[-1]), float(c.iloc[-2])
    base = {"close": round(last, 2), "change_pct": round((last / prev - 1) * 100, 2),
            "ma20": round(float(ma20), 2), "ma50": round(float(ma50), 2)}
    if last > ma20 and last > ma50 and ma20 > ma50:
        return {**base, "status": "SEHAT", "level": "ok",
                "note": "IHSG di atas MA20 & MA50. Kondisi mendukung entry sesuai aturan."}
    if last < ma20 and last < ma50:
        return {**base, "status": "TURUN", "level": "bad",
                "note": "IHSG di bawah MA20 & MA50. Mayoritas saham ikut tertekan — tunda entry baru atau perkecil lot."}
    return {**base, "status": "WASPADA", "level": "warn",
            "note": "IHSG bergerak campuran. Entry hanya pada sinyal terkuat dengan lot lebih kecil."}


# ----------------------------------------------------------------------------
# Cek saham: layak masuk? / kondisi posisi yang sedang dipegang
# ----------------------------------------------------------------------------
def check_stock(ticker: str, df: pd.DataFrame, s: dict, risk: dict, regime: dict,
                progress: float = 1.0, avg: float | None = None, lots: int | None = None) -> dict:
    d = add_indicators(df, s)
    ev = evaluate(ticker, d, s, risk, progress)
    rd = radar(ticker, df.iloc[:-1] if progress < 1 else df, s, risk)
    row = d.iloc[-1]
    price = float(row["Close"])
    ma_fast, ma_trend = float(row["ma_fast"]), float(row["ma_trend"])
    sup20 = float(d["Low"].shift(1).rolling(20).min().iloc[-1])
    ma_up = bool(d["ma_trend"].iloc[-1] > d["ma_trend"].iloc[-11])

    entry_plan = make_plan(round_tick(price, "up"), row["atr"], s, risk)
    checks = [
        {"label": f"Kondisi pasar (IHSG): {regime['status']}", "ok": regime.get("level") == "ok",
         "warn": regime.get("level") == "warn"},
        {"label": "Harga di atas MA50 dan MA50 menanjak", "ok": price > ma_trend and ma_up},
        {"label": "Tidak sedang sinyal SELL", "ok": ev["signal"] != "SELL"},
        {"label": f"Tidak mengejar: jarak dari MA20 {(price / ma_fast - 1) * 100:.1f}% (maks 8%)",
         "ok": price <= ma_fast * 1.08},
        {"label": "Stop loss logis ≤ 8%" + (f" ({entry_plan.sl_pct:.1f}%)" if entry_plan else " — terlalu lebar"),
         "ok": entry_plan is not None},
        {"label": f"Likuid (nilai transaksi rata-rata ≥ Rp{s['min_value_idr'] / 1e9:.0f} M)",
         "ok": bool(d["value"].rolling(20).mean().iloc[-1] >= s["min_value_idr"])},
    ]
    hard_fail = [c for c in checks[1:] if not c["ok"]]
    if hard_fail or regime.get("level") == "bad":
        verdict = "JANGAN MASUK DULU"
    elif regime.get("level") == "warn" or (ev["signal"] != "BUY" and not rd["kandidat"]):
        verdict = "TUNGGU KONFIRMASI"
    else:
        verdict = "LAYAK SESUAI ATURAN"
    if verdict == "LAYAK SESUAI ATURAN" and ev["signal"] != "BUY" and rd["kandidat"]:
        verdict = "TUNGGU BREAKOUT"  # pasang auto buy di trigger radar

    out = {"ticker": ticker, "price": price, "change_pct": ev["change_pct"], "signal": ev, "radar": rd,
           "levels": {"ma20": round(ma_fast), "ma50": round(ma_trend), "sup10": ev["sup"],
                      "sup20": round_tick(sup20) if not pd.isna(sup20) else None, "res20": ev["res"],
                      "atr": round(float(row["atr"]), 2)},
           "entry": {"verdict": verdict, "checks": checks,
                     "plan": asdict(entry_plan) if entry_plan else None},
           "position": None}

    if avg:
        lots = lots or 1
        fee = (risk["fee_buy_pct"] + risk["fee_sell_pct"]) / 100
        pl_pct = (price / avg - 1 - fee) * 100
        # batas cut loss: support terdekat di bawah harga, jika tidak ada -> harga - 1,5 ATR
        cands = [x for x in (sup20, float(ev["sup"] or 0), ma_trend) if x and x < price]
        cl = round_tick(max(cands) - tick_size(max(cands)), "down") if cands else round_tick(price - 1.5 * row["atr"], "down")
        loss_at_cl = (cl / avg - 1 - fee) * 100
        broken = price < ma_trend and price < sup20
        weak = price < ma_trend or ev["signal"] == "SELL"
        if broken:
            status, note = "TREN RUSAK", ("Harga di bawah MA50 dan sudah jebol low 20 hari. Menurut aturan dashboard, "
                                          "kondisi ini adalah kondisi keluar, bukan average down.")
        elif weak:
            status, note = "MELEMAH", (f"Tren melemah. Tetapkan batas cut loss di {cl:,} sekarang dan patuhi; "
                                       "jangan menambah lot sampai tren pulih.").replace(",", ".")
        else:
            status, note = "MASIH SEHAT", (f"Struktur tren masih utuh. Batas cut loss logis di {cl:,} "
                                           "(di bawah support terdekat).").replace(",", ".")
        out["position"] = {
            "avg": avg, "lots": lots, "status": status, "note": note,
            "pl_pct": round(pl_pct, 2), "pl_idr": round((price * (1 - fee) - avg) * lots * LOT),
            "cut_loss": cl, "loss_at_cl_pct": round(loss_at_cl, 2),
            "loss_at_cl_idr": round((cl * (1 - fee) - avg) * lots * LOT),
            "need_to_breakeven_pct": round((avg / price / (1 - fee) - 1) * 100, 2) if price < avg else 0.0,
        }
    return out
