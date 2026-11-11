"""
Analisis per saham: level kunci (support/resistance), area beli/jual, skenario tembus/jebol,
ringkasan teknikal, statistik historis, dan ringkasan fundamental.

Semua level dihitung dari data harga (titik balik yang benar-benar terjadi), bukan angka bulat atau perkiraan.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from strategy import round_tick

PIVOT_K = 5          # titik balik = tertinggi/terendah di jendela ±5 hari
LOOKBACK = 180       # hari bursa yang dipakai untuk mencari titik balik


# ----------------------------------------------------------------------------
# Indikator dasar
# ----------------------------------------------------------------------------
def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


# ----------------------------------------------------------------------------
# Level kunci dari titik balik
# ----------------------------------------------------------------------------
def pivots(df: pd.DataFrame, k: int = PIVOT_K) -> list[dict]:
    d = df.iloc[-LOOKBACK:]
    hi, lo, vol = d["High"].values, d["Low"].values, d["Volume"].values
    out = []
    for i in range(k, len(d) - k):  # k hari terakhir belum bisa dikonfirmasi
        if hi[i] == hi[i - k:i + k + 1].max():
            out.append({"price": float(hi[i]), "type": "high", "date": str(d.index[i].date()), "vol": float(vol[i])})
        if lo[i] == lo[i - k:i + k + 1].min():
            out.append({"price": float(lo[i]), "type": "low", "date": str(d.index[i].date()), "vol": float(vol[i])})
    return out


def zones(df: pd.DataFrame) -> list[dict]:
    """Kelompokkan titik balik yang berdekatan (≤ 0,5×ATR) menjadi zona harga."""
    pv = sorted(pivots(df), key=lambda x: x["price"])
    if not pv:
        return []
    a = float(atr(df).iloc[-1])
    tol = max(0.5 * a, df["Close"].iloc[-1] * 0.004)
    groups, cur = [], [pv[0]]
    for p in pv[1:]:
        if p["price"] - cur[-1]["price"] <= tol:
            cur.append(p)
        else:
            groups.append(cur)
            cur = [p]
    groups.append(cur)
    out = []
    for g in groups:
        lo_, hi_ = min(x["price"] for x in g), max(x["price"] for x in g)
        if hi_ - lo_ < 0.3 * a:  # zona satu titik: beri ketebalan kecil agar realistis
            mid = (lo_ + hi_) / 2
            lo_, hi_ = mid - 0.15 * a, mid + 0.15 * a
        out.append({"low": round_tick(lo_, "down"), "high": round_tick(hi_, "up"), "touches": len(g),
                    "last": max(x["date"] for x in g)})
    return out


def key_levels(df: pd.DataFrame) -> dict:
    price = float(df["Close"].iloc[-1])
    a = float(atr(df).iloc[-1])
    zs = zones(df)
    sup = sorted([z for z in zs if z["high"] < price], key=lambda z: -z["high"])
    res = sorted([z for z in zs if z["low"] > price], key=lambda z: z["low"])
    inside = [z for z in zs if z["low"] <= price <= z["high"]]
    # zona yang sedang ditempati harga dihitung sebagai support jika harga di paruh atasnya, resistance jika di bawah
    for z in inside:
        (sup if price >= (z["low"] + z["high"]) / 2 else res).insert(0, z)

    def pick(lst, n):  # pilih zona terdekat, utamakan yang disentuh ≥ 2 kali
        strong = [z for z in lst if z["touches"] >= 2]
        return (strong or lst)[:n]

    s, r = pick(sup, 2), pick(res, 2)
    hi52, lo52 = float(df["High"].iloc[-250:].max()), float(df["Low"].iloc[-250:].min())
    out = {"price": price, "atr": round(a, 2), "supports": s, "resistances": r,
           "high_52w": round_tick(hi52), "low_52w": round_tick(lo52)}

    # skenario tembus ke atas
    if r:
        bo = r[0]["high"]
        tgt = r[1]["low"] if len(r) > 1 else round_tick(bo + 2 * a, "down")
        out["breakout"] = {"level": bo, "target": tgt, "touches": r[0]["touches"],
                           "target_basis": "resistance berikutnya" if len(r) > 1 else "2× ATR (tidak ada resistance lebih tinggi)"}
    else:
        bo = round_tick(hi52, "up")
        out["breakout"] = {"level": bo, "target": round_tick(bo + 2 * a, "down"), "touches": 0,
                           "target_basis": "2× ATR (harga di area tertinggi 52 minggu)"}
    # skenario jebol ke bawah
    if s:
        bd = s[0]["low"]
        tgt = s[1]["high"] if len(s) > 1 else round_tick(bd - 2 * a, "up")
        out["breakdown"] = {"level": bd, "target": tgt, "touches": s[0]["touches"],
                            "target_basis": "support berikutnya" if len(s) > 1 else "2× ATR (tidak ada support lebih rendah)"}
    else:
        bd = round_tick(lo52, "down")
        out["breakdown"] = {"level": bd, "target": round_tick(bd - 2 * a, "up"), "touches": 0,
                            "target_basis": "2× ATR (harga di area terendah 52 minggu)"}
    out["buy_zone"] = [s[0]["low"], s[0]["high"]] if s else None
    out["sell_zone"] = [r[0]["low"], r[0]["high"]] if r else None
    return out


# ----------------------------------------------------------------------------
# Statistik historis: apa yang terjadi setelah harga tembus/jebol di saham ini
# ----------------------------------------------------------------------------
def follow_through(df: pd.DataFrame, n: int = 20, fwd: int = 5) -> dict:
    """Proksi yang bisa diuji: tembus high 20 hari / jebol low 20 hari dengan volume ≥ rata-rata.
    Hasil: berapa kali harga 5 hari kemudian masih searah."""
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    hh, ll = h.shift(1).rolling(n).max(), l.shift(1).rolling(n).min()
    vma = v.shift(1).rolling(20).mean()
    ret = c.shift(-fwd) / c - 1
    up = (c > hh) & (v >= vma) & ret.notna()
    dn = (c < ll) & (v >= vma) & ret.notna()
    # hitung kejadian pertama saja dalam rentetan berturut-turut
    up &= ~up.shift(1, fill_value=False)
    dn &= ~dn.shift(1, fill_value=False)

    def stat(mask, direction):
        r = ret[mask]
        if len(r) == 0:
            return {"n": 0}
        ok = (r > 0) if direction > 0 else (r < 0)
        return {"n": int(len(r)), "pct": round(float(ok.mean() * 100), 1), "avg_ret": round(float(r.mean() * 100), 2)}

    return {"days": int(len(df)), "fwd": fwd, "breakout": stat(up, 1), "breakdown": stat(dn, -1)}


# ----------------------------------------------------------------------------
# Ringkasan teknikal
# ----------------------------------------------------------------------------
def technical(df: pd.DataFrame) -> dict:
    c = df["Close"]
    price = float(c.iloc[-1])
    ma = {n: float(c.rolling(n).mean().iloc[-1]) if len(c) >= n else None for n in (20, 50, 200)}
    r = float(rsi(c).iloc[-1])
    a = float(atr(df).iloc[-1])
    v10, v50 = float(df["Volume"].iloc[-10:].mean()), float(df["Volume"].iloc[-50:].mean())
    items = []

    def add(label, value, tone, note):
        items.append({"label": label, "value": value, "tone": tone, "note": note})

    if ma[50] and ma[200]:
        if price > ma[50] > ma[200]:
            add("Tren", "Naik", "good", "Harga > MA50 > MA200")
        elif price < ma[50] < ma[200]:
            add("Tren", "Turun", "bad", "Harga < MA50 < MA200")
        else:
            add("Tren", "Campuran", "warn", "Harga, MA50, dan MA200 belum searah")
    elif ma[50]:
        add("Tren", "Naik" if price > ma[50] else "Turun", "good" if price > ma[50] else "bad", "Dibanding MA50 (data < 200 hari)")
    if ma[20]:
        d = (price / ma[20] - 1) * 100
        add("Jarak dari MA20", f"{d:+.1f}%".replace(".", ","), "warn" if abs(d) > 8 else "neutral",
            "Terlalu jauh dari rata-rata, rawan koreksi" if d > 8 else "Jauh di bawah rata-rata" if d < -8 else "Wajar")
    add("RSI 14", f"{r:.0f}", "warn" if r >= 70 or r <= 30 else "neutral",
        "Jenuh beli (≥70)" if r >= 70 else "Jenuh jual (≤30)" if r <= 30 else "Netral (30–70)")
    vr = v10 / v50 if v50 else 1
    add("Volume 10 hari", f"{vr:.1f}× rata-rata 50 hari".replace(".", ","), "good" if vr >= 1.2 else "neutral",
        "Minat beli/jual meningkat" if vr >= 1.2 else "Normal" if vr >= 0.8 else "Sepi")
    add("Volatilitas (ATR)", f"{a / price * 100:.1f}% per hari".replace(".", ","), "neutral",
        "Rata-rata rentang gerak harian")
    return {"items": items, "ma20": ma[20], "ma50": ma[50], "ma200": ma[200], "rsi": round(r, 1)}


# ----------------------------------------------------------------------------
# Fundamental (Yahoo Finance)
# ----------------------------------------------------------------------------
FUND_FIELDS = ["longName", "sector", "industry", "marketCap", "trailingPE", "priceToBook", "returnOnEquity",
               "debtToEquity", "profitMargins", "revenueGrowth", "earningsGrowth", "dividendYield",
               "trailingEps", "bookValue", "currency"]


def fetch_fundamentals(tickers: list[str], cache_file: Path, max_age_days: int = 7, delay: float = 0.5) -> dict:
    cache = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text())
        except json.JSONDecodeError:
            cache = {}
    now = time.time()
    todo = [t for t in tickers if now - cache.get(t, {}).get("_ts", 0) > max_age_days * 86400]
    if todo:
        import yfinance as yf
        for t in todo:
            try:
                info = yf.Ticker(f"{t}.JK").info or {}
                cache[t] = {k: info.get(k) for k in FUND_FIELDS} | {"_ts": now, "_date": datetime.now().strftime("%Y-%m-%d")}
            except Exception as e:
                cache.setdefault(t, {})["_error"] = type(e).__name__
            time.sleep(delay)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache))
    return cache


def _pct(x):
    """Yahoo kadang memberi rasio (0,15) dan kadang persen (15). Seragamkan ke persen."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return float(x) * 100 if abs(float(x)) <= 3 else float(x)


def fundamental(raw: dict | None, price: float) -> dict:
    if not raw or all(raw.get(k) is None for k in FUND_FIELDS):
        return {"available": False}
    fin = (raw.get("sector") or "") == "Financial Services"
    roe, margin = _pct(raw.get("returnOnEquity")), _pct(raw.get("profitMargins"))
    rg, eg = _pct(raw.get("revenueGrowth")), _pct(raw.get("earningsGrowth"))
    dy = _pct(raw.get("dividendYield"))
    der = raw.get("debtToEquity")  # Yahoo: persen (mis. 45 = 0,45×)
    pe, pb, eps, bv = raw.get("trailingPE"), raw.get("priceToBook"), raw.get("trailingEps"), raw.get("bookValue")
    items, score, counted = [], 0, 0

    def add(label, value, tone, note):
        nonlocal score, counted
        items.append({"label": label, "value": value, "tone": tone, "note": note})
        if tone in ("good", "bad"):
            counted += 1
            score += tone == "good"

    f1 = lambda x, s="%": "—" if x is None else f"{x:.1f}{s}".replace(".", ",")
    if roe is not None:
        add("ROE", f1(roe), "good" if roe >= 15 else "bad" if roe < 8 else "neutral",
            "Kuat (≥15%)" if roe >= 15 else "Lemah (<8%)" if roe < 8 else "Sedang (8–15%)")
    if margin is not None:
        add("Margin laba bersih", f1(margin), "good" if margin >= 10 else "bad" if margin < 3 else "neutral",
            "Tinggi (≥10%)" if margin >= 10 else "Tipis (<3%)" if margin < 3 else "Sedang")
    if rg is not None:
        add("Pertumbuhan pendapatan", f1(rg), "good" if rg > 0 else "bad", "Tumbuh (yoy)" if rg > 0 else "Turun (yoy)")
    if eg is not None:
        add("Pertumbuhan laba", f1(eg), "good" if eg > 0 else "bad", "Tumbuh (yoy)" if eg > 0 else "Turun (yoy)")
    if der is not None and not fin:
        add("Utang / ekuitas (DER)", f1(der / 100, "×"), "good" if der <= 100 else "bad" if der > 200 else "neutral",
            "Aman (≤1×)" if der <= 100 else "Tinggi (>2×)" if der > 200 else "Sedang (1–2×)")
    if pe is not None:
        add("PER", f1(pe, "×"), "neutral", "Harga ÷ laba per saham")
    if pb is not None:
        add("PBV", f1(pb, "×"), "neutral", "Harga ÷ nilai buku per saham")
    if dy is not None:
        add("Dividend yield", f1(dy), "neutral", "Dividen ÷ harga")
    graham = math.sqrt(22.5 * eps * bv) if eps and bv and eps > 0 and bv > 0 else None
    if graham:
        gap = (price / graham - 1) * 100
        add("Nilai wajar Graham", f"{graham:,.0f}".replace(",", "."), "good" if gap < 0 else "bad" if gap > 30 else "neutral",
            f"Harga {abs(gap):.0f}% {'di bawah' if gap < 0 else 'di atas'} nilai wajar √(22,5×EPS×BVPS)")
    verdict = None
    if counted >= 3:
        r = score / counted
        verdict = "Kuat" if r >= 0.7 else "Lemah" if r < 0.4 else "Sedang"
    return {"available": True, "name": raw.get("longName"), "sector": raw.get("sector"), "industry": raw.get("industry"),
            "market_cap": raw.get("marketCap"), "items": items, "verdict": verdict, "score": f"{score}/{counted}",
            "is_financial": fin, "as_of": raw.get("_date")}


def demo_fundamental(t: str) -> dict:
    r = np.random.default_rng(sum(map(ord, t)))
    return {"longName": f"{t} Tbk (demo)", "sector": "Industrials", "industry": "Demo", "marketCap": float(r.uniform(1e12, 8e14)),
            "trailingPE": float(r.uniform(5, 30)), "priceToBook": float(r.uniform(0.6, 5)), "returnOnEquity": float(r.uniform(0.02, 0.25)),
            "debtToEquity": float(r.uniform(10, 250)), "profitMargins": float(r.uniform(0.01, 0.3)),
            "revenueGrowth": float(r.uniform(-0.1, 0.2)), "earningsGrowth": float(r.uniform(-0.2, 0.3)),
            "dividendYield": float(r.uniform(0.5, 6)), "trailingEps": float(r.uniform(20, 600)),
            "bookValue": float(r.uniform(200, 4000)), "_date": datetime.now().strftime("%Y-%m-%d")}


# ----------------------------------------------------------------------------
# Paket lengkap per saham
# ----------------------------------------------------------------------------
def analyze(t: str, df: pd.DataFrame, fund_raw: dict | None = None) -> dict:
    df = df.dropna()
    lv = key_levels(df)
    tech = technical(df)
    ft = follow_through(df)
    last = df.iloc[-1]
    ohlc = [{"time": str(i.date()), "open": round(float(r.Open), 2), "high": round(float(r.High), 2),
             "low": round(float(r.Low), 2), "close": round(float(r.Close), 2), "volume": float(r.Volume)}
            for i, r in df.iloc[-250:].iterrows()]
    return {"ticker": t, "price": float(last["Close"]), "prev": float(df["Close"].iloc[-2]),
            "date": str(df.index[-1].date()), "levels": lv, "technical": tech, "history": ft,
            "fundamental": fundamental(fund_raw, float(last["Close"])), "ohlc": ohlc}


def card_zones(a: dict) -> dict:
    """Ringkasan kecil untuk ditampilkan di setiap kartu saham."""
    lv = a["levels"]
    return {"buy_zone": lv.get("buy_zone"), "sell_zone": lv.get("sell_zone"),
            "breakout": lv["breakout"]["level"], "breakdown": lv["breakdown"]["level"]}
