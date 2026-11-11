"""
Pengaman data dan risiko khusus BEI.

- Validasi data harga (harga nol/kosong, OHLC tidak konsisten, lonjakan melebihi batas ARA, data basi)
- Batas Auto Rejection (ARA/ARB) sesuai periode berlaku
- Penanda risiko per saham: notasi khusus, UMA, suspensi, aksi korporasi (dari data BEI bila tersedia)

Sumber aturan ARA/ARB (diverifikasi November 2026):
  20 Okt – 31 Des 2026 : Rp1–10 ARA/ARB Rp1; Rp11–14 ARA 35% ARB Rp1; Rp15–200 ARA 35% ARB 7%;
                         >200–5.000 ARA 25% ARB 7%; >5.000 ARA 20% ARB 7%  (Kep-00158/BEI/10-2026)
  mulai 1 Jan 2027     : ARB kembali simetris dengan ARA (35% / 25% / 20%), Rp1–10 tetap Rp1
Aturan ini hanya untuk Papan Utama/Pengembangan/Ekonomi Baru. Papan Akselerasi & Pemantauan Khusus berbeda.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from strategy import round_tick, tick_size

# ----------------------------------------------------------------------------
# ARA / ARB
# ----------------------------------------------------------------------------
def ar_pct(prev: float, d: date) -> tuple[float | None, float | None, str]:
    """Kembalikan (ARA%, ARB%, catatan). None berarti batas berupa nominal Rp1."""
    sym = d >= date(2027, 1, 1)
    if prev <= 10:
        return None, None, "Rp1"
    ara = 35.0 if prev <= 200 else 25.0 if prev <= 5000 else 20.0
    if sym:
        return ara, ara, "simetris (mulai 1 Jan 2027)"
    if prev <= 14:
        return ara, None, "ARB Rp1 (Rp11–14)"
    return ara, 7.0, "ARB 7% (20 Okt–31 Des 2026)"


def ar_limits(prev: float, d: date) -> dict:
    """Harga ARA/ARB untuk hari bursa dengan harga acuan `prev` (dibulatkan ke fraksi ke arah aman)."""
    ara, arb, note = ar_pct(prev, d)
    up = prev + 1 if ara is None else prev * (1 + ara / 100)
    dn = prev - 1 if arb is None else prev * (1 - arb / 100)
    return {"ara": round_tick(up, "down"), "arb": max(1, round_tick(dn, "up")),
            "ara_pct": ara, "arb_pct": arb, "note": note}


# ----------------------------------------------------------------------------
# Validasi data harga
# ----------------------------------------------------------------------------
def validate_frame(t: str, df: pd.DataFrame, today: date, market_open: bool) -> tuple[pd.DataFrame | None, list[str], list[str]]:
    """Kembalikan (frame bersih atau None bila tidak layak dipakai, masalah fatal, peringatan)."""
    fatal, warn = [], []
    if df is None or df.empty:
        return None, ["data kosong"], []
    d = df.copy()
    bad = d[["Open", "High", "Low", "Close"]].le(0).any(axis=1) | d[["Open", "High", "Low", "Close"]].isna().any(axis=1)
    if bad.any():
        d = d[~bad]
        warn.append(f"{int(bad.sum())} baris harga nol/kosong dibuang")
    # perbaiki OHLC yang tidak konsisten (high < low dsb.) — tandai saja, lalu rapikan batasnya
    inc = (d["High"] < d[["Open", "Close", "Low"]].max(axis=1)) | (d["Low"] > d[["Open", "Close", "High"]].min(axis=1))
    if inc.any():
        d["High"] = d[["Open", "High", "Low", "Close"]].max(axis=1)
        d["Low"] = d[["Open", "High", "Low", "Close"]].min(axis=1)
        warn.append(f"{int(inc.sum())} bar OHLC tidak konsisten dirapikan")
    if len(d) < 60:
        return None, ["data historis kurang dari 60 hari"], warn
    # lonjakan melebihi batas ARA tertinggi (35%) + toleransi: kemungkinan stock split / data salah
    chg = d["Close"].pct_change().abs()
    jump = chg[chg > 0.40]
    recent_jump = jump[jump.index >= d.index[-1] - pd.Timedelta(days=90)]
    if len(recent_jump):
        fatal.append(f"lonjakan harga {recent_jump.iloc[-1] * 100:.0f}% pada {recent_jump.index[-1].date()} "
                     "melebihi batas ARA — kemungkinan stock split/aksi korporasi atau data salah")
    # data basi
    last = d.index[-1].date()
    age = np.busday_count(last, today)
    if age > 3:
        fatal.append(f"tidak ada transaksi sejak {last} (kemungkinan suspensi)")
    elif market_open and last < today:
        warn.append("belum ada transaksi hari ini")
    return (None if fatal else d), fatal, warn


# ----------------------------------------------------------------------------
# Penanda risiko dari data resmi BEI (screener, suspensi) — tersedia bila runner laptop aktif
# ----------------------------------------------------------------------------
def idx_flags(screener: list[dict] | None, suspends: list[dict] | None, today: date) -> dict[str, list[dict]]:
    flags: dict[str, list[dict]] = {}

    def add(code, kind, text, block):
        flags.setdefault(code, []).append({"kind": kind, "text": text, "block": block})

    for s in screener or []:
        code = (s.get("stockCode") or s.get("code") or "").strip()
        if not code:
            continue
        notation = (s.get("notation") or "").strip(" -")
        if notation:
            add(code, "notasi", f"Notasi khusus BEI: {notation}", True)
        uma = s.get("umaDate")
        if uma:
            try:
                ud = datetime.fromisoformat(str(uma)[:10]).date()
                if (today - ud).days <= 14:
                    add(code, "uma", f"UMA (aktivitas pasar tidak wajar) {ud:%d-%m-%Y}", True)
            except ValueError:
                pass
        status = (s.get("status") or "").strip()
        if status and status.lower() not in ("active", "aktif", "normal"):
            add(code, "status", f"Status BEI: {status}", True)
        ca, cad = s.get("corpAction"), s.get("corpActionDate")
        if ca:
            add(code, "aksi", f"Aksi korporasi: {ca}" + (f" ({str(cad)[:10]})" if cad else ""), False)
    for s in suspends or []:
        code = (s.get("Kode") or s.get("code") or "").strip()
        title = s.get("Judul") or s.get("title") or ""
        try:
            sd = datetime.fromisoformat(str(s.get("Date") or s.get("date"))[:10]).date()
        except ValueError:
            continue
        if code and (today - sd).days <= 7:
            is_unsuspend = "pembukaan" in title.lower() or "unsuspen" in title.lower()
            add(code, "suspensi", f"{'Pembukaan suspensi' if is_unsuspend else 'Suspensi'} {sd:%d-%m-%Y}: {title[:80]}",
                not is_unsuspend)
    return flags


def summarize_flags(*lists: list[dict] | None) -> dict:
    items = [f for lst in lists if lst for f in lst]
    return {"items": items, "block": any(f["block"] for f in items)}
