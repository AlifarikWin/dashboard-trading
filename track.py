"""
Track record nyata (forward test) Daftar Pagi.

Setiap hari daftar disimpan SEBELUM hari perdagangannya, lalu dinilai memakai harga harian sebenarnya
pada hari itu: terpicu? kena SL / TP / ditutup sore? Penilaian memakai aturan konservatif yang sama
dengan uji historis (SL dianggap lebih dulu jika SL dan TP tersentuh di hari yang sama).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


def next_bday(d: date) -> date:
    n = d + timedelta(days=1)
    while n.weekday() > 4:
        n += timedelta(days=1)
    return n


def target_date(list_date: str, for_today: bool, today: date) -> str:
    """Tanggal perdagangan yang dituju sebuah Daftar Pagi."""
    return str(today) if for_today else str(next_bday(datetime.fromisoformat(list_date).date() if list_date else today))


def save_snapshot(store: Path, target: str, now: datetime, mstatus: str, dt_res: dict, tuning: dict | None,
                  regime: dict, stops: list[str]) -> bool:
    """Simpan daftar untuk tanggal `target`. Dibekukan begitu sesi hari itu dimulai."""
    f = store / "track" / "picks" / f"{target}.json"
    frozen = str(now.date()) >= target and mstatus not in ("pra-buka",)
    if f.exists() and frozen:
        return False
    if str(now.date()) > target:
        return False
    f.parent.mkdir(parents=True, exist_ok=True)
    snap = {"target": target, "saved_at": now.isoformat(timespec="minutes"), "list_date": dt_res.get("date"),
            "would_trade": not stops, "stops": stops, "regime": regime.get("status"),
            "tuning": (tuning or {}).get("status"), "params": dt_res.get("params"),
            "picks": [{"code": p["code"], "score": p["score"], "plan": p["plan"], "flags": p.get("flags")} for p in dt_res["picks"]]}
    f.write_text(json.dumps(snap, ensure_ascii=False))
    return True


def evaluate_pick(plan: dict, bar: pd.Series | None, fee_total_pct: float) -> dict:
    if bar is None:
        return {"status": "data belum ada"}
    o, h, l, c = (float(bar[k]) for k in ("Open", "High", "Low", "Close"))
    if h < plan["trigger"]:
        return {"status": "tidak terpicu", "high": h}
    if o > plan["limit"]:
        return {"status": "gap di atas limit", "open": o}
    entry = max(plan["trigger"], o)
    if l <= plan["sl"]:
        exit_, why = plan["sl"], "SL"
    elif h >= plan["tp"]:
        exit_, why = plan["tp"], "TP"
    else:
        exit_, why = c, "tutup sore"
    net = (exit_ / entry - 1) * 100 - fee_total_pct
    return {"status": why, "entry": entry, "exit": exit_, "net_pct": round(net, 2)}


def evaluate_all(store: Path, bars: dict[str, pd.DataFrame], today: date, market_closed_today: bool,
                 fee_total_pct: float) -> dict:
    pdir = store / "track" / "picks"
    days = []
    for f in sorted(pdir.glob("*.json")) if pdir.exists() else []:
        snap = json.loads(f.read_text())
        t = snap["target"]
        if t > str(today) or (t == str(today) and not market_closed_today):
            days.append({**snap, "pending": True})
            continue
        res = []
        for p in snap["picks"]:
            df = bars.get(p["code"])
            bar = None
            if df is not None:
                m = df[df.index == pd.Timestamp(t)]
                bar = m.iloc[0] if len(m) else None
            res.append({**p, "result": evaluate_pick(p["plan"], bar, fee_total_pct)})
        days.append({**snap, "picks": res, "pending": False})
    return {"days": days, "summary": summarize(days)}


def summarize(days: list[dict]) -> dict:
    def agg(sel):
        trades = [p["result"] for d in sel for p in d["picks"] if p["result"].get("net_pct") is not None]
        picks = [p for d in sel for p in d["picks"] if p["result"].get("status") != "data belum ada"]
        if not picks:
            return {"days": len(sel), "picks": 0, "trades": 0}
        net = np.array([t["net_pct"] for t in trades]) if trades else np.array([])
        return {"days": len(sel), "picks": len(picks), "trades": len(trades),
                "trigger_rate": round(len(trades) / len(picks) * 100, 1),
                "win_rate": round(float((net > 0).mean() * 100), 1) if len(net) else None,
                "avg_pct": round(float(net.mean()), 2) if len(net) else None,
                "total_pct": round(float(net.sum()), 2) if len(net) else None,
                "by_exit": pd.Series([t["status"] for t in trades]).value_counts().to_dict() if trades else {}}
    done = [d for d in days if not d.get("pending")]
    return {"all": agg(done), "followed": agg([d for d in done if d.get("would_trade")]),
            "first_date": done[0]["target"] if done else None}


def codes_to_evaluate(store: Path, today: date) -> set[str]:
    pdir = store / "track" / "picks"
    out = set()
    for f in pdir.glob("*.json") if pdir.exists() else []:
        snap = json.loads(f.read_text())
        if snap["target"] <= str(today) and str(today - timedelta(days=40)) <= snap["target"]:
            out |= {p["code"] for p in snap["picks"]}
    return out
