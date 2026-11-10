"""Pengambilan data harga dari Yahoo Finance (gratis, dapat tertunda) dan data demo."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

WIB = ZoneInfo("Asia/Jakarta")
COLS = ["Open", "High", "Low", "Close", "Volume"]
SPECIAL = {"IHSG": "^JKSE"}  # indeks tidak memakai akhiran .JK


def _split(raw: pd.DataFrame, sym: str) -> pd.DataFrame | None:
    try:
        df = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
        return df[COLS].dropna()
    except KeyError:
        return None


def fetch(tickers: list[str], period: str = "6mo", with_intraday: bool = True
          ) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """Kembalikan (data harian per ticker, waktu harga terakhir per ticker).
    Bar hari ini diperbarui dengan data 1 menit agar harga lebih mutakhir."""
    import yfinance as yf
    syms = [SPECIAL.get(t, f"{t}.JK") for t in tickers]
    daily = yf.download(syms, period=period, interval="1d", group_by="ticker",
                        auto_adjust=False, threads=True, progress=False)
    intra = None
    if with_intraday:
        try:
            intra = yf.download(syms, period="1d", interval="1m", group_by="ticker",
                                auto_adjust=False, threads=True, progress=False, prepost=False)
        except Exception:
            intra = None

    data, stamps = {}, {}
    for t, sym in zip(tickers, syms):
        df = _split(daily, sym)
        if df is None or len(df) < 60:
            continue
        df = df.copy() if t in SPECIAL else df[df["Volume"] > 0].copy()  # volume indeks bisa 0
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        stamps[t] = f"{df.index[-1].date()} (harian)"
        m = _split(intra, sym) if intra is not None and not intra.empty else None
        if m is not None and not m.empty:
            idx = pd.to_datetime(m.index)
            idx = idx.tz_convert(WIB) if idx.tz is not None else idx.tz_localize(WIB)
            m.index = idx
            day = pd.Timestamp(idx[-1].date())
            bar = {"Open": m["Open"].iloc[0], "High": m["High"].max(), "Low": m["Low"].min(),
                   "Close": m["Close"].iloc[-1], "Volume": m["Volume"].sum()}
            if day in df.index:
                bar["Volume"] = max(bar["Volume"], df.at[day, "Volume"])
                bar["High"] = max(bar["High"], df.at[day, "High"])
                bar["Low"] = min(bar["Low"], df.at[day, "Low"])
                bar["Open"] = df.at[day, "Open"]
            df.loc[day, COLS] = [bar[c] for c in COLS]
            df = df.sort_index()
            stamps[t] = idx[-1].strftime("%Y-%m-%d %H:%M WIB")
        data[t] = df
    return data, stamps


def demo(tickers: list[str], now: datetime | None = None) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """Data sintetis untuk uji tampilan tanpa internet. Harga bergerak tiap menit."""
    now = now or datetime.now(WIB)
    minute_seed = int(now.timestamp() // 60)
    data, stamps = {}, {}
    end = pd.Timestamp(now.date())
    for k, t in enumerate(tickers):
        rng = np.random.default_rng(k + 7)
        n, base = 300, float(rng.choice([180, 450, 1200, 2600, 6500, 9000]))
        if t == "IHSG":
            base = 7000
        drift = rng.choice([0.002, 0.0015, -0.0015, 0.0005])
        close = base * np.cumprod(1 + rng.normal(drift, 0.015, n))
        high = close * (1 + rng.uniform(0, 0.02, n))
        low = close * (1 - rng.uniform(0, 0.02, n))
        vol = rng.uniform(8e6, 1.2e7, n) * (2e4 / base)
        vol[rng.random(n) < 0.1] *= 2.5
        r2 = np.random.default_rng(minute_seed + k)
        scen = -1 if t == "IHSG" else k % 6
        if scen == 3:      # pola akumulasi: harga menyempit dekat puncak, volume mengering
            lvl = high[-80:-25].max()
            close[-25:] = lvl * (0.965 + 0.02 * np.sin(np.linspace(0, 6, 25)) * np.linspace(1, 0.3, 25))
            high[-25:], low[-25:] = close[-25:] * 1.006, close[-25:] * 0.994
            vol[-25:] *= np.linspace(0.8, 0.45, 25)
            vol[-25:][np.diff(np.r_[close[-26], close[-25:]]) > 0] *= 1.8
        top, bot = high[-21:-1].max(), low[-11:-1].min()
        if scen == 0:      # breakout
            close[-1] = top * (1.01 + r2.uniform(0, 0.02)); vol[-1] *= 2.5
        elif scen == 1:    # breakdown
            close[-1] = bot * (0.98 - r2.uniform(0, 0.01)); vol[-1] *= 1.8
        elif scen == 2:    # dekat resistance
            close[-1] = top * (0.985 + r2.uniform(-0.005, 0.005))
        high[-1], low[-1] = max(high[-1], close[-1]), min(low[-1], close[-1])
        idx = pd.bdate_range(end=end, periods=n)
        data[t] = pd.DataFrame({"Open": (high + low) / 2, "High": high, "Low": low,
                                "Close": close, "Volume": vol}, index=idx)
        stamps[t] = now.strftime("%Y-%m-%d %H:%M WIB") + " (DEMO)"
    return data, stamps
