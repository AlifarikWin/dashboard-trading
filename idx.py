"""
Ringkasan perdagangan harian resmi BEI (seluruh saham), termasuk foreign buy/sell.

Sumber: https://www.idx.co.id/primary/TradingSummary/GetStockSummary?date=YYYYMMDD
Data tersedia setelah pasar tutup. ForeignBuy/ForeignSell dalam satuan lembar saham.
Setiap hari disimpan sebagai file JSON di folder cache agar tidak diunduh ulang.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

WIB = ZoneInfo("Asia/Jakarta")
BASE_URL = "https://www.idx.co.id"
SUMMARY_URL = BASE_URL + "/primary/TradingSummary/GetStockSummary?date={d}"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    "Referer": BASE_URL + "/id",
    "X-Requested-With": "XMLHttpRequest",
}
FIELDS = {"StockCode": "code", "OpenPrice": "open", "High": "high", "Low": "low", "Close": "close",
          "Previous": "prev", "Volume": "volume", "Value": "value", "Frequency": "freq",
          "ForeignBuy": "fbuy", "ForeignSell": "fsell", "Remarks": "remarks"}


class IdxClient:
    def __init__(self, cache_dir: Path):
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self._session = None
        self.last_error = ""

    def _sess(self):
        if self._session is None:
            from curl_cffi import requests as creq  # meniru browser agar tidak ditolak
            s = creq.Session(impersonate="chrome")
            s.get(BASE_URL + "/id", timeout=30)  # ambil cookie sesi
            self._session = s
        return self._session

    def day(self, d: date, refresh: bool = False) -> list[dict] | None:
        """Data satu hari. [] = bukan hari bursa / belum tersedia. None = gagal unduh."""
        f = self.cache / f"{d:%Y%m%d}.json"
        if f.exists() and not refresh:
            return json.loads(f.read_text())
        try:
            r = self._sess().get(SUMMARY_URL.format(d=f"{d:%Y%m%d}"), headers=HEADERS, timeout=30)
            if r.status_code != 200:
                self.last_error = f"HTTP {r.status_code}"
                return None
            rows = r.json().get("data") or []
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {str(e)[:120]}"
            self._session = None
            return None
        rows = [{v: x.get(k) for k, v in FIELDS.items()} for x in rows]
        today = datetime.now(WIB).date()
        # hanya simpan hari yang sudah lengkap (berisi data, atau hari lampau yang memang kosong/libur)
        if rows or d < today - timedelta(days=1):
            f.write_text(json.dumps(rows))
        return rows

    def history(self, n_days: int, end: date | None = None, delay: float = 0.4) -> tuple[pd.DataFrame, list[str]]:
        """Kumpulkan n hari bursa terakhir. Kembalikan (panel panjang, daftar error)."""
        end = end or datetime.now(WIB).date()
        frames, errors, d, tries = [], [], end, 0
        while len(frames) < n_days and tries < int(n_days * 1.7) + 10:
            tries += 1
            if d.weekday() < 5:
                cached = (self.cache / f"{d:%Y%m%d}.json").exists()
                rows = self.day(d)
                if rows is None:
                    errors.append(f"{d:%Y-%m-%d}")
                    if len(errors) >= 3 and not frames:
                        break  # sumber tidak bisa diakses, hentikan cepat
                elif rows:
                    df = pd.DataFrame(rows)
                    df["date"] = pd.Timestamp(d)
                    frames.append(df)
                if not cached:
                    time.sleep(delay)
            d -= timedelta(days=1)
        if not frames:
            return pd.DataFrame(), errors
        return to_panel(pd.concat(frames, ignore_index=True)), errors


def to_panel(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    for c in ["open", "high", "low", "close", "prev", "volume", "value", "freq", "fbuy", "fsell"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    # saham tanpa transaksi: BEI mengisi open/high/low = 0
    df = df[df["volume"] > 0].copy()
    df["remarks"] = df["remarks"].fillna("").astype(str)
    return df.sort_values(["code", "date"]).reset_index(drop=True)


def demo_panel(n_days: int = 90, n_stocks: int = 120, end: date | None = None, seed: int = 0) -> pd.DataFrame:
    """Panel sintetis berformat sama dengan data BEI, untuk mode demo dan pengujian."""
    rng = np.random.default_rng(seed)
    end = end or datetime.now(WIB).date()
    dates = pd.bdate_range(end=pd.Timestamp(end), periods=n_days)
    rows = []
    for k in range(n_stocks):
        code = "".join(chr(65 + int(x)) for x in rng.integers(0, 26, 4))
        base = float(rng.choice([160, 250, 480, 900, 1800, 3500, 7000]))
        drift = rng.normal(0.0008, 0.002)
        c = base * np.cumprod(1 + rng.normal(drift, 0.022, n_days))
        hi = c * (1 + rng.uniform(0, 0.025, n_days))
        lo = c * (1 - rng.uniform(0, 0.025, n_days))
        op = lo + (hi - lo) * rng.uniform(0.2, 0.8, n_days)
        vol = rng.lognormal(16, 0.5, n_days) * (2000 / base)
        vol[rng.random(n_days) < 0.1] *= 2.5
        fshare = rng.uniform(0.05, 0.35)
        fnet = rng.normal(0, 0.08, n_days)
        if k % 7 == 0:  # pola asing akumulasi + penutupan kuat di hari terakhir
            fnet[-5:] = rng.uniform(0.08, 0.2, 5)
            c[-1] = hi[-1] * 0.995
            vol[-1] *= 2.2
        fb = vol * fshare * (1 + fnet)
        fs = vol * fshare * (1 - fnet)
        prev = np.r_[c[0], c[:-1]]
        for i, d in enumerate(dates):
            rows.append({"code": code, "date": d, "open": op[i], "high": max(hi[i], c[i], op[i]),
                         "low": min(lo[i], c[i], op[i]), "close": c[i], "prev": prev[i], "volume": vol[i],
                         "value": vol[i] * c[i], "freq": vol[i] / 500, "fbuy": fb[i], "fsell": fs[i], "remarks": ""})
    return to_panel(pd.DataFrame(rows))
