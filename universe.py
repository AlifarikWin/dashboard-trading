"""
Semua emiten BEI (sumber: Yahoo Finance, karena situs BEI memblokir akses otomatis).

- Daftar emiten + data ringkas fundamental: screener Yahoo (bursa JKT), sekali jalan ±838 saham.
- Harga harian 1 tahun: unduhan massal Yahoo (±30 detik untuk seluruh bursa).
- Hasil per saham ditulis sebagai file kecil `analysis/<KODE>.json`; daftar untuk pencarian & screener
  ditulis sebagai satu file `universe.json`.

Dihitung sekali setelah bursa tutup (run "heavy") lalu disimpan di folder cache (actions/cache),
sehingga run tiap 15 menit cukup menyalin hasilnya.
"""
from __future__ import annotations

import json
import math
import pickle
import time
import traceback
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

import analysis as an
import risk as rk
import strategy as st

SECTORS = ["Basic Materials", "Communication Services", "Consumer Cyclical", "Consumer Defensive", "Energy",
           "Financial Services", "Healthcare", "Industrials", "Real Estate", "Technology", "Utilities"]
SECTOR_ID = {"Basic Materials": "Bahan Baku", "Communication Services": "Komunikasi", "Consumer Cyclical": "Konsumer Siklikal",
             "Consumer Defensive": "Konsumer Primer", "Energy": "Energi", "Financial Services": "Keuangan",
             "Healthcare": "Kesehatan", "Industrials": "Industri", "Real Estate": "Properti",
             "Technology": "Teknologi", "Utilities": "Utilitas"}


# ----------------------------------------------------------------------------
# Daftar emiten (screener Yahoo)
# ----------------------------------------------------------------------------
def _screen(query, max_rows: int = 2000) -> list[dict]:
    import yfinance as yf
    rows, off = [], 0
    while off < max_rows:
        r = yf.screen(query, offset=off, size=250, sortField="intradaymarketcap", sortAsc=False)
        q = r.get("quotes", []) if isinstance(r, dict) else []
        rows += q
        if len(q) < 250:
            break
        off += 250
        time.sleep(0.5)
    return rows


def fetch_list() -> dict[str, dict]:
    """{kode: ringkasan} untuk semua saham biasa di bursa JKT, lengkap dengan sektor."""
    from yfinance import EquityQuery
    base = EquityQuery("eq", ["exchange", "JKT"])
    out = {}
    for q in _screen(base):
        sym = q.get("symbol") or ""
        if q.get("quoteType") != "EQUITY" or not sym.endswith(".JK"):
            continue
        code = sym[:-3]
        out[code] = {
            "name": q.get("longName") or q.get("shortName") or code,
            "market_cap": q.get("marketCap"),
            "pe": q.get("trailingPE"), "pbv": q.get("priceToBook"),
            "eps": q.get("epsTrailingTwelveMonths"), "bvps": q.get("bookValue"),
            "dyf": q.get("trailingAnnualDividendYield"),  # rasio, mis. 0,035 = 3,5%
            "sector": None,
        }
    for sec in SECTORS:  # sektor tidak ikut di hasil screener umum: ambil per sektor
        try:
            for q in _screen(EquityQuery("and", [base, EquityQuery("eq", ["sector", sec])])):
                code = (q.get("symbol") or "")[:-3]
                if code in out:
                    out[code]["sector"] = sec
        except Exception:
            traceback.print_exc()
        time.sleep(0.5)
    return out


def basic_fund(info: dict) -> dict:
    """Ubah ringkasan screener menjadi format input analysis.fundamental()."""
    eps, bv = info.get("eps"), info.get("bvps")
    roe = eps / bv if eps is not None and bv and bv > 0 else None
    return {"longName": info.get("name"), "sector": info.get("sector"), "marketCap": info.get("market_cap"),
            "trailingPE": info.get("pe"), "priceToBook": info.get("pbv"), "trailingEps": eps, "bookValue": bv,
            "returnOnEquity": roe, "trailingAnnualDividendYield": info.get("dyf")}


def merge_fund(basic: dict | None, detail: dict | None) -> dict | None:
    """Data rinci (yf.Ticker.info, diperbarui bergiliran) diutamakan; sisanya dari screener."""
    if not basic and not detail:
        return None
    m = dict(basic or {})
    for k, v in (detail or {}).items():
        if v is not None:
            m[k] = v
    return m


# ----------------------------------------------------------------------------
# Penanda risiko dari data harga (pengganti notasi/UMA BEI yang tidak bisa diakses)
# ----------------------------------------------------------------------------
def price_flags(df: pd.DataFrame, today: date) -> list[dict]:
    fl = []
    c = df["Close"]
    if len(c) < 12:
        return fl
    last = df.index[-1].date()
    gap = np.busday_count(last, today)
    if gap > 3:
        fl.append({"kind": "suspensi", "text": f"Tidak ada transaksi sejak {last} (kemungkinan suspensi)", "block": True})
    r = c.pct_change().iloc[-10:]
    def ara_frac(p: float) -> float:
        a = rk.ar_pct(p, today)[0]
        return a / 100 if a is not None else 1 / max(p, 1)  # harga ≤ Rp10: batas naik Rp1
    lim = np.array([ara_frac(float(p)) if p == p else 1.0 for p in c.shift(1).iloc[-10:]])
    near_ara = int((r.values >= lim * 0.9).sum())
    move10 = float(c.iloc[-1] / c.iloc[-11] - 1) * 100
    if near_ara >= 2 or move10 >= 50 or move10 <= -40:
        fl.append({"kind": "tidak wajar",
                   "text": f"Pergerakan ekstrem 10 hari ({move10:+.0f}%, {near_ara}× mendekati ARA) — rawan UMA/suspensi (perkiraan dari harga)".replace(".", ","),
                   "block": True})
    val20 = float((df["Close"] * df["Volume"]).iloc[-20:].mean())
    if val20 < 1e9:
        fl.append({"kind": "likuiditas", "text": f"Nilai transaksi rata-rata Rp{val20 / 1e6:,.0f} juta/hari — kurang likuid".replace(",", "."),
                   "block": False})
    if float(c.iloc[-1]) <= 50:
        fl.append({"kind": "gocap", "text": "Harga Rp50 atau kurang", "block": False})
    return fl


# ----------------------------------------------------------------------------
# Ringkasan per saham untuk daftar/screener
# ----------------------------------------------------------------------------
def index_row(code: str, name: str, sector: str | None, df: pd.DataFrame, a: dict, wk: tuple, rs: dict | None,
              flags: list[dict]) -> dict:
    c = df["Close"]
    L, F = a["levels"], a.get("fundamental") or {}
    px = float(c.iloc[-1])
    trend = a["technical"]["items"][0]["value"] if a["technical"]["items"] else ""
    bo = L["breakout"]["level"]
    bz = L.get("buy_zone")
    vol20 = float(df["Volume"].iloc[-21:-1].mean()) if len(df) > 21 else float(df["Volume"].mean())
    graham = next((i for i in F.get("items", []) if i["label"] == "Nilai wajar Graham"), None)
    gd = None
    if graham:
        try:
            gv = float(graham["value"].replace(".", ""))
            gd = round((px / gv - 1) * 100, 1) if gv > 0 else None
        except ValueError:
            gd = None
    fv = lambda lbl: next((i["value"] for i in F.get("items", []) if i["label"] == lbl), None)
    return {
        "c": code, "n": name, "s": SECTOR_ID.get(sector or "", sector or ""),
        "p": px, "ch": round(float(c.iloc[-1] / c.iloc[-2] - 1) * 100, 2) if len(c) > 1 else 0.0,
        "v": round(float((df["Close"] * df["Volume"]).iloc[-20:].mean()) / 1e9, 2),  # miliar Rp/hari
        "vr": round(float(df["Volume"].iloc[-1]) / vol20, 2) if vol20 > 0 else None,
        "t": "up" if trend == "Naik" else "down" if trend == "Turun" else "side",
        "w": "up" if wk[0] else "down" if wk[1] else "side",
        "rs": (rs or {}).get("rs20"),
        "bo": round((bo / px - 1) * 100, 1) if bo and px else None,
        "ib": bool(bz and bz[0] <= px <= bz[1] * 1.01),
        "f": F.get("verdict"), "gd": gd,
        "pe": fv("PER"), "pbv": fv("PBV"), "roe": fv("ROE"), "dy": fv("Dividend yield"),
        "fl": [f["kind"] for f in flags], "blk": any(f["block"] for f in flags),
        "d": a["date"],
    }


def compact_ohlc(a: dict) -> None:
    """Simpan OHLC sebagai larik per kolom (lebih kecil ±60%)."""
    o = a.get("ohlc")
    if not isinstance(o, list):
        return
    num = lambda x: int(x) if float(x).is_integer() else round(float(x), 2)
    a["ohlc"] = {"t": [d["time"] for d in o], "o": [num(d["open"]) for d in o], "h": [num(d["high"]) for d in o],
                 "l": [num(d["low"]) for d in o], "c": [num(d["close"]) for d in o], "v": [int(d["volume"]) for d in o]}


# ----------------------------------------------------------------------------
# Hitung seluruh bursa
# ----------------------------------------------------------------------------
def download(codes: list[str], chunk: int = 200) -> dict[str, pd.DataFrame]:
    import data as datasrc
    out = {}
    for i in range(0, len(codes), chunk):
        part = codes[i:i + chunk]
        for attempt in range(2):
            try:
                d, _ = datasrc.fetch(part, "1y", with_intraday=False)
                out.update(d)
                break
            except Exception:
                traceback.print_exc()
                time.sleep(5)
    return out


def build(cache: Path, listing: dict[str, dict], frames: dict[str, pd.DataFrame], ihsg: pd.DataFrame | None,
          cfg: dict, regime: dict, today: date, fund_detail: dict, div_stats: dict, ar_ref_date: date) -> dict:
    """Tulis cache/analysis/*.json, cache/universe.json, cache/panel.pkl. Kembalikan ringkasan."""
    s, risk = cfg["strategy"], cfg["risk"]
    adir = cache / "analysis"
    adir.mkdir(parents=True, exist_ok=True)
    for f in adir.glob("*.json"):
        f.unlink()
    rets = {c: float(df["Close"].iloc[-1] / df["Close"].iloc[-21] - 1) * 100 for c, df in frames.items() if len(df) > 21}
    sector_rs = {}
    for sec in {(listing.get(c) or {}).get("sector") for c in frames} - {None}:
        mem = [rets[c] for c in frames if (listing.get(c) or {}).get("sector") == sec and c in rets]
        if mem:
            sector_rs[sec] = {"name": SECTOR_ID.get(sec, sec), "n": len(mem), "avg_ret20": round(float(np.median(mem)), 2)}
    rows, errors = [], 0
    for code, df in frames.items():
        try:
            info = listing.get(code) or {}
            fund = merge_fund(basic_fund(info) if info else None, fund_detail.get(code))
            a = an.analyze(code, df, fund)
            d = st.add_indicators(df, s)
            wk = (bool(d["wk_up"].iloc[-1]), bool(d["wk_down"].iloc[-1]))
            rs = an.rs_info(df, ihsg)
            an.enrich_technical(a, wk[0], wk[1], rs, sector_rs.get(info.get("sector")))
            fl = price_flags(df, today)
            ref = float(df["Close"].iloc[-1])
            a["ar"] = {**rk.ar_limits(ref, ar_ref_date), "for": str(ar_ref_date), "ref": ref}
            a["flags"] = fl
            a["events"], a["news"] = [], []
            a["dividend"] = div_stats.get(code)
            a["name"] = info.get("name") or (a.get("fundamental") or {}).get("name")
            a["check"] = st.check_stock(code, df, s, risk, regime, 1.0)
            a["check"]["flags"] = fl
            a["check"]["zones"] = an.card_zones(a)
            a["scope"] = "harian"
            compact_ohlc(a)
            (adir / f"{code}.json").write_text(json.dumps(_clean(a), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            rows.append(index_row(code, a["name"] or code, info.get("sector"), df, a, wk, rs, fl))
        except Exception:
            errors += 1
            traceback.print_exc()
    rows.sort(key=lambda r: r["c"])
    (cache / "universe.json").write_text(json.dumps(_clean({"rows": rows, "date": max((r["d"] for r in rows), default=None)}),
                                                    ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    with open(cache / "panel.pkl", "wb") as f:
        pickle.dump(frames_to_panel(frames, cfg["daytrade"]["history_days"]), f)
    flags = {r["c"]: [] for r in rows if r["fl"]}
    for code in flags:
        try:
            flags[code] = json.loads((adir / f"{code}.json").read_text(encoding="utf-8")).get("flags", [])
        except (OSError, ValueError):
            pass
    (cache / "flags.json").write_text(json.dumps(flags, ensure_ascii=False), encoding="utf-8")
    (cache / "sector_rs.json").write_text(json.dumps(sector_rs, ensure_ascii=False), encoding="utf-8")
    state = {"date": max((r["d"] for r in rows), default=None), "built_at": datetime.now().isoformat(timespec="minutes"),
             "stocks": len(rows), "listed": len(listing), "errors": errors}
    (cache / "state.json").write_text(json.dumps(state))
    return state


def frames_to_panel(frames: dict[str, pd.DataFrame], days: int) -> pd.DataFrame:
    import idx as idxsrc
    rows = []
    for code, df in frames.items():
        d = df.iloc[-(days + 1):].copy()
        d["prev"] = d["Close"].shift(1)
        d = d.dropna()
        if d.empty:
            continue
        rows.append(pd.DataFrame({"code": code, "date": d.index, "open": d["Open"].values, "high": d["High"].values,
                                  "low": d["Low"].values, "close": d["Close"].values, "prev": d["prev"].values,
                                  "volume": d["Volume"].values, "value": (d["Close"] * d["Volume"]).values,
                                  "freq": 0.0, "fbuy": 0.0, "fsell": 0.0, "remarks": ""}))
    return idxsrc.to_panel(pd.concat(rows, ignore_index=True)) if rows else pd.DataFrame()


def load_panel(cache: Path) -> pd.DataFrame:
    f = cache / "panel.pkl"
    if not f.exists():
        return pd.DataFrame()
    with open(f, "rb") as fh:
        return pickle.load(fh)


# ----------------------------------------------------------------------------
# Pola dividen: kenaikan menjelang cum date & penurunan saat ex date
# ----------------------------------------------------------------------------
def dividend_stats(close: pd.Series, open_: pd.Series, divs: pd.Series, pre: int = 10) -> dict | None:
    """Yahoo mencatat tanggal dividen = ex date. Cum date = hari bursa sebelumnya.
    run-up = close cum date ÷ close `pre` hari bursa sebelumnya; drop = open ex date ÷ close cum date."""
    divs = divs[divs > 0]
    if divs.empty:
        return None
    idx = close.index
    ev = []
    for exd in divs.index:
        exd = pd.Timestamp(exd)
        exd = (exd.tz_localize(None) if exd.tzinfo else exd).normalize()
        pos = idx.searchsorted(exd)
        if pos >= len(idx) or pos - 1 - pre < 0 or idx[pos] != exd:
            continue
        cum = pos - 1
        run = float(close.iloc[cum] / close.iloc[cum - pre] - 1) * 100
        drop = float(open_.iloc[pos] / close.iloc[cum] - 1) * 100
        ev.append({"ex": str(exd.date()), "run": round(run, 2), "drop": round(drop, 2)})
    if not ev:
        return None
    runs = np.array([e["run"] for e in ev])
    drops = np.array([e["drop"] for e in ev])
    return {"n": len(ev), "up": int((runs > 0).sum()), "avg_run": round(float(runs.mean()), 2),
            "avg_drop": round(float(drops.mean()), 2), "pre_days": pre, "events": ev[-6:]}


def fetch_dividend_stats(codes: list[str], chunk: int = 150) -> dict[str, dict]:
    import yfinance as yf
    out = {}
    for i in range(0, len(codes), chunk):
        part = codes[i:i + chunk]
        syms = [f"{c}.JK" for c in part]
        try:
            d = yf.download(syms, period="5y", interval="1d", group_by="ticker", auto_adjust=False,
                            actions=True, threads=True, progress=False)
        except Exception:
            traceback.print_exc()
            continue
        for c, sym in zip(part, syms):
            try:
                x = d[sym] if isinstance(d.columns, pd.MultiIndex) else d
                if "Dividends" not in x:
                    continue
                x = x.dropna(subset=["Close"])
                x = x[x["Volume"] > 0]
                x.index = pd.to_datetime(x.index).tz_localize(None).normalize()
                r = dividend_stats(x["Close"], x["Open"], x["Dividends"].fillna(0))
                if r:
                    out[c] = r
            except Exception:
                continue
        time.sleep(1)
    return out


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if (math.isnan(o) or math.isinf(o)) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, (pd.Timestamp, datetime, date)):
        return o.isoformat()
    return o


# ----------------------------------------------------------------------------
# Data demo (tanpa internet)
# ----------------------------------------------------------------------------
def demo_list(codes: list[str]) -> dict[str, dict]:
    rng = np.random.default_rng(3)
    out = {}
    for i, c in enumerate(codes):
        out[c] = {"name": f"PT {c.title()} Tbk (demo)", "market_cap": float(rng.uniform(1e11, 5e14)),
                  "pe": float(rng.uniform(4, 30)), "pbv": float(rng.uniform(0.5, 4)), "eps": float(rng.uniform(-20, 400)),
                  "bvps": float(rng.uniform(100, 3000)), "dyf": float(rng.uniform(0, 0.08)), "sector": SECTORS[i % len(SECTORS)]}
    return out
