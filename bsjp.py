"""
BSJP (Beli Sore Jual Pagi).

Beli di sesi pra-penutupan (15.50–16.00) saham yang ditutup kuat, jual besok pagi (09.00–09.15).
Seleksi dan bukti memakai data harian seluruh emiten:

- Kandidat naik : ditutup dekat harga tertinggi hari itu, volume melonjak, naik 2% s.d. (batas ARA − 3%),
                  tren naik (close > MA20 > MA50), likuid. Menembus high 20 hari menambah skor.
- Kandidat turun: ditutup dekat harga terendah, volume melonjak, turun ≥ 2%, di bawah MA20 (peringatan).
- Uji historis   : beli di close hari H, jual di open hari H+1, dikurangi fee. Hanya 5 skor teratas per hari,
                  sama seperti daftar yang ditampilkan. Dibandingkan dengan rata-rata semua saham likuid.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

import risk as rk
from strategy import round_tick, tick_size

P = {"cpos_up": 0.8, "cpos_dn": 0.2, "vr": 1.5, "chg_min": 2.0, "ara_gap": 3.0, "min_value": 5e9,
     "min_price": 100, "top_n": 5, "min_trades": 30, "recent_days": 60, "risk_move_pct": 3.0}


def _ara_pct(prev: np.ndarray, d: date) -> np.ndarray:
    a = np.where(prev <= 200, 35.0, np.where(prev <= 5000, 25.0, 20.0))
    return a


def features(df: pd.DataFrame) -> pd.DataFrame:
    d = pd.DataFrame(index=df.index)
    C, H, L, V, O = df["Close"], df["High"], df["Low"], df["Volume"], df["Open"]
    prev = C.shift(1)
    rng = (H - L).replace(0, np.nan)
    d["close"], d["prev"] = C, prev
    d["chg"] = (C / prev - 1) * 100
    d["cpos"] = ((C - L) / rng).fillna(0.5)
    d["av20"] = V.shift(1).rolling(20).mean()
    d["vr"] = V / d["av20"]
    d["ma20"], d["ma50"] = C.rolling(20).mean(), C.rolling(50).mean()
    d["hi20"] = H.shift(1).rolling(20).max()
    d["lo20"] = L.shift(1).rolling(20).min()
    d["val20"] = (C * V).shift(1).rolling(20).mean()
    d["on"] = (O.shift(-1) / C - 1) * 100            # hasil semalam: open besok vs close hari ini
    d["hi_next"] = (H.shift(-1) / C - 1) * 100       # batas atas (tidak realistis, hanya informasi)
    return d


def _score(cpos, vr, chg, brk):
    return (40 * np.clip(cpos, 0, 1) + 20 * np.clip(vr / 3, 0, 1) + 20 * brk + 20 * np.clip(chg / 8, 0, 1)).round(0)


def conditions(d: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    ara = pd.Series(_ara_pct(d["prev"].values, date.today()), index=d.index)
    liquid = (d["val20"] >= P["min_value"]) & (d["close"] >= P["min_price"])
    up = (liquid & (d["cpos"] >= P["cpos_up"]) & (d["vr"] >= P["vr"]) & (d["chg"] >= P["chg_min"])
          & (d["chg"] <= ara - P["ara_gap"]) & (d["close"] > d["ma20"]) & (d["ma20"] > d["ma50"]))
    dn = (liquid & (d["cpos"] <= P["cpos_dn"]) & (d["vr"] >= P["vr"]) & (d["chg"] <= -P["chg_min"])
          & (d["close"] < d["ma20"]))
    return up.fillna(False), dn.fillna(False)


def _stats(x: pd.Series, fee: float) -> dict:
    x = x.dropna()
    if not len(x):
        return {"n": 0}
    net = x - fee
    return {"n": int(len(x)), "gap_up_pct": round(float((x > 0).mean() * 100), 1),
            "win_pct": round(float((net > 0).mean() * 100), 1), "avg_on": round(float(x.mean()), 2),
            "avg_net": round(float(net.mean()), 2), "median_net": round(float(net.median()), 2),
            "worst": round(float(net.min()), 2)}


def evaluate_history(frames: dict[str, pd.DataFrame], fee: float, blocked: set[str] = frozenset()) -> tuple[dict, dict]:
    """Kembalikan (statistik uji, data dasar untuk daftar sementara besok)."""
    ups, dns, base_rows, base = [], [], [], {}
    for code, df in frames.items():
        if len(df) < 60:
            continue
        d = features(df)
        up, dn = conditions(d)
        brk = (d["close"] > d["hi20"]).astype(float)
        sc = _score(d["cpos"], d["vr"].fillna(0), d["chg"], brk)
        if up.any():
            ups.append(pd.DataFrame({"date": d.index[up], "code": code, "score": sc[up].values, "on": d["on"][up].values}))
        if dn.any():
            dns.append(pd.DataFrame({"date": d.index[dn], "code": code, "on": d["on"][dn].values}))
        liq = d["val20"] >= P["min_value"]
        base_rows.append(d.loc[liq, "on"])
        last = d.iloc[-1]
        base[code] = {k: (None if pd.isna(last[k]) else round(float(last[k]), 2))
                      for k in ("ma20", "ma50", "val20")} | {
            "hi20": None if pd.isna(last["hi20"]) else float(max(last["hi20"], df["High"].iloc[-1])),
            "av20": None if pd.isna(last["av20"]) else float(df["Volume"].iloc[-20:].mean()),
            "close": float(last["close"])}
    all_dates = sorted({x for f in frames.values() for x in f.index})
    recent_from = all_dates[-P["recent_days"]] if len(all_dates) > P["recent_days"] else (all_dates[0] if all_dates else None)
    out = {"fee_pct": fee, "params": P}
    if ups:
        u = pd.concat(ups)
        u = u.sort_values(["date", "score"], ascending=[True, False]).groupby("date").head(P["top_n"])
        out["up"] = _stats(u["on"], fee)
        out["up_recent"] = _stats(u.loc[u["date"] >= recent_from, "on"], fee) if recent_from is not None else {"n": 0}
        out["days_with_picks"] = int(u["date"].nunique())
    else:
        out["up"] = out["up_recent"] = {"n": 0}
    if dns:
        dd = pd.concat(dns)
        s = dd["on"].dropna()
        out["down"] = {"n": int(len(s)), "gap_down_pct": round(float((s < 0).mean() * 100), 1),
                       "avg_on": round(float(s.mean()), 2)} if len(s) else {"n": 0}
    else:
        out["down"] = {"n": 0}
    b = pd.concat(base_rows).dropna() if base_rows else pd.Series(dtype=float)
    out["baseline"] = {"n": int(len(b)), "avg_on": round(float(b.mean()), 2) if len(b) else None,
                       "gap_up_pct": round(float((b > 0).mean() * 100), 1) if len(b) else None}
    up_all, up_rec = out["up"], out["up_recent"]
    if up_all.get("n", 0) < P["min_trades"]:
        out["status"] = "data_kurang"
    elif up_all["avg_net"] > 0 and up_rec.get("n", 0) >= 10 and up_rec["avg_net"] > 0:
        out["status"] = "layak"
    else:
        out["status"] = "tidak_layak"
    return out, base


def _plan(code: str, price: float, prev: float, d: date) -> dict:
    lim = rk.ar_limits(prev, d)
    limit = min(round_tick(price + 2 * tick_size(price), "down"), lim["ara"])
    return {"buy_limit": limit, "risk_move_pct": P["risk_move_pct"]}


def candidates_daily(frames: dict[str, pd.DataFrame], names: dict[str, str], blocked: set[str], d_for: date) -> dict:
    """Daftar final dari bar harian terakhir (setelah bursa tutup)."""
    up_l, dn_l = [], []
    for code, df in frames.items():
        if len(df) < 60 or code in blocked:
            continue
        f = features(df).iloc[[-1]]
        up, dn = conditions(f)
        r = f.iloc[0]
        brk = bool(r["close"] > r["hi20"]) if not pd.isna(r["hi20"]) else False
        row = {"c": code, "n": names.get(code, code), "price": float(r["close"]), "prev": float(r["prev"]),
               "chg": round(float(r["chg"]), 2), "cpos": round(float(r["cpos"]), 2), "vr": round(float(r["vr"]), 2),
               "brk": brk}
        if bool(up.iloc[0]):
            row["score"] = float(_score(r["cpos"], r["vr"], r["chg"], float(brk)))
            row["plan"] = _plan(code, row["price"], row["prev"], d_for)
            up_l.append(row)
        elif bool(dn.iloc[0]):
            dn_l.append(row)
    up_l.sort(key=lambda x: (-x["score"], -x["vr"]))
    dn_l.sort(key=lambda x: x["chg"])
    date_ = max((df.index[-1] for df in frames.values()), default=None)
    return {"date": str(date_.date()) if date_ is not None else None, "up": up_l[:P["top_n"]], "down": dn_l[:10],
            "up_more": max(0, len(up_l) - P["top_n"])}


def candidates_live(quotes: dict[str, dict], base: dict[str, dict], names: dict[str, str], blocked: set[str],
                    progress: float, d: date) -> dict:
    """Daftar sementara dari harga intraday (tertunda) menjelang penutupan."""
    up_l = []
    f = max(progress, 0.1)
    for code, q in quotes.items():
        b = base.get(code)
        p, prev, h, l, vol = (q.get(k) for k in ("p", "prev", "h", "l", "vol"))
        if not b or not p or not prev or not h or not l or not vol or code in blocked:
            continue
        if None in (b.get("ma20"), b.get("ma50"), b.get("av20"), b.get("val20")) or not b["av20"]:
            continue
        chg = (p / prev - 1) * 100
        cpos = (p - l) / (h - l) if h > l else 0.5
        vr = vol / (b["av20"] * f)
        ara = _ara_pct(np.array([prev]), d)[0]
        ok = (b["val20"] >= P["min_value"] and p >= P["min_price"] and cpos >= P["cpos_up"] and vr >= P["vr"]
              and P["chg_min"] <= chg <= ara - P["ara_gap"] and p > b["ma20"] and b["ma20"] > b["ma50"])
        if not ok:
            continue
        brk = bool(b.get("hi20") and p > b["hi20"])
        up_l.append({"c": code, "n": names.get(code, code), "price": float(p), "prev": float(prev), "chg": round(chg, 2),
                     "cpos": round(cpos, 2), "vr": round(vr, 2), "brk": brk,
                     "score": float(_score(cpos, vr, chg, float(brk))), "plan": _plan(code, float(p), float(prev), d)})
    up_l.sort(key=lambda x: (-x["score"], -x["vr"]))
    return {"up": up_l[:P["top_n"]], "up_more": max(0, len(up_l) - P["top_n"])}


def monitor(picks: list[dict], quotes: dict[str, dict]) -> list[dict]:
    """Status pagi kandidat semalam: dibandingkan dengan harga beli (close kemarin)."""
    out = []
    for p in picks:
        q = quotes.get(p["c"]) or {}
        o, last = q.get("o"), q.get("p")
        if not o or not last:
            out.append({**p, "mon": None})
            continue
        base = p["price"]
        gap, now = (o / base - 1) * 100, (last / base - 1) * 100
        st = ("gap naik, bertahan" if gap > 0 and last >= o else "gap naik, tergerus" if gap > 0
              else "dibuka datar" if gap == 0 else "dibuka turun")
        out.append({**p, "mon": {"open": float(o), "last": float(last), "gap": round(gap, 2), "now": round(now, 2), "status": st}})
    return out


# ----------------------------------------------------------------------------
# Track record BSJP (daftar final disimpan, dinilai dengan open hari bursa berikutnya)
# ----------------------------------------------------------------------------
def save_snapshot(store: Path, final: dict, status: str | None) -> None:
    if not final.get("date"):
        return
    f = store / "track" / "bsjp" / f"{final['date']}.json"
    if f.exists():
        return  # daftar final tidak diubah setelah tersimpan
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"date": final["date"], "status": status, "saved_at": datetime.now().isoformat(timespec="minutes"),
                             "picks": [{"c": p["c"], "price": p["price"], "score": p["score"]} for p in final["up"]]}))


def evaluate_snapshots(store: Path, frames: dict[str, pd.DataFrame], fee: float) -> None:
    d = store / "track" / "bsjp"
    for f in sorted(d.glob("*.json")) if d.exists() else []:
        snap = json.loads(f.read_text())
        changed = False
        for p in snap["picks"]:
            if p.get("result") is not None:
                continue
            df = frames.get(p["c"])
            if df is None:
                continue
            after = df[df.index > pd.Timestamp(snap["date"])]
            if after.empty:
                continue
            o = float(after["Open"].iloc[0])
            p["result"] = {"date": str(after.index[0].date()), "open": o,
                           "net_pct": round((o / p["price"] - 1) * 100 - fee, 2)}
            changed = True
        if changed:
            f.write_text(json.dumps(snap))


def track_summary(store: Path) -> dict:
    d = store / "track" / "bsjp"
    days = [json.loads(f.read_text()) for f in sorted(d.glob("*.json"))] if d.exists() else []

    def agg(sel):
        r = [p["result"]["net_pct"] for s in sel for p in s["picks"] if p.get("result")]
        if not r:
            return {"days": len(sel), "trades": 0}
        a = np.array(r)
        return {"days": len(sel), "trades": len(r), "win_pct": round(float((a > 0).mean() * 100), 1),
                "avg_net": round(float(a.mean()), 2), "total": round(float(a.sum()), 2)}
    return {"all": agg(days), "followed": agg([s for s in days if s.get("status") == "layak"]),
            "first_date": days[0]["date"] if days else None, "days": days[-20:][::-1]}
