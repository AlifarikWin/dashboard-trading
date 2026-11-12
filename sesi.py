"""
Rekap Sesi 1 dan Sesi 2.

- Sesi 1: perubahan sejak penutupan kemarin sampai akhir sesi 1 (12.00; Jumat 11.30). Harga akhir sesi 1
  disimpan sekali per hari (snapshot) saat bursa istirahat.
- Sesi 2: perubahan dari akhir sesi 1 sampai harga terakhir/penutupan. Dipakai juga sebagai syarat BSJP
  bila uji historis membuktikan manfaatnya (lihat bsjp.s2_test).
Harga dari screener Yahoo (tertunda ±10–15 menit).
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import numpy as np


def s1_end_minute(d: date, cfg_session: dict) -> int:
    span = cfg_session["fri"] if d.weekday() == 4 else cfg_session["mon_thu"]
    h, m = map(int, span[0][1].split(":"))
    return h * 60 + m


def snap_path(store: Path, d: date) -> Path:
    return store / "sesi" / f"{d}.json"


def save_s1(store: Path, d: date, now: datetime, quotes: dict[str, dict], ihsg: dict | None, final: bool) -> dict:
    """Simpan harga akhir sesi 1. Snapshot final tidak ditimpa; snapshot sementara diperbarui."""
    f = snap_path(store, d)
    if f.exists():
        old = json.loads(f.read_text())
        if old.get("final"):
            return old
    snap = {"date": str(d), "time": now.strftime("%H:%M"), "final": final, "ihsg": ihsg,
            "q": {c: [q["p"], q.get("vol"), q.get("prev")] for c, q in quotes.items() if q.get("p")}}
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(snap, separators=(",", ":")))
    for old in sorted(f.parent.glob("*.json"))[:-15]:  # simpan 15 hari bursa terakhir
        old.unlink()
    return snap


def load_s1(store: Path, d: date) -> dict | None:
    f = snap_path(store, d)
    return json.loads(f.read_text()) if f.exists() else None


def _sectors(items: list[dict], key: str, min_n: int = 5) -> list[dict]:
    by: dict[str, list] = {}
    for x in items:
        if x.get("s") and (x.get("v20") or 0) >= 1:
            by.setdefault(x["s"], []).append(x[key])
    out = [{"s": s, "med": round(float(np.median(v)), 2), "n": len(v),
            "up": sum(1 for a in v if a > 0), "down": sum(1 for a in v if a < 0)} for s, v in by.items() if len(v) >= min_n]
    return sorted(out, key=lambda r: -r["med"])


def _breadth(vals: list[float]) -> dict:
    return {"up": sum(1 for v in vals if v > 0), "down": sum(1 for v in vals if v < 0), "flat": sum(1 for v in vals if v == 0)}


def recap_s1(items: list[dict], ihsg: dict | None, picks: list[dict], status: str, time: str) -> dict:
    """items = hasil universe.movers (ch = perubahan sejak penutupan kemarin)."""
    pk = {}
    for p in picks:
        s = (p.get("live") or {}).get("status") or "belum ada data"
        pk[s] = pk.get(s, 0) + 1
    keep = ("c", "n", "s", "p", "ch", "tr", "v20", "blk", "hold", "warn")
    return {"status": status, "time": time,
            "ihsg": round((ihsg["p"] / ihsg["prev"] - 1) * 100, 2) if ihsg and ihsg.get("prev") else None,
            "breadth": _breadth([x["ch"] for x in items]), "sectors": _sectors(items, "ch"),
            "picks": pk, "items": [{k: x.get(k) for k in keep} for x in items]}


def recap_s2(items: list[dict], s1: dict, ihsg_now: dict | None, status: str, time: str) -> dict:
    """Perubahan sesi 2 = harga terakhir ÷ harga akhir sesi 1."""
    q1 = s1.get("q", {})
    out = []
    for x in items:
        a = q1.get(x["c"])
        if not a or not a[0]:
            continue
        s2 = (x["p"] / a[0] - 1) * 100
        out.append({"c": x["c"], "n": x["n"], "s": x.get("s"), "p": x["p"], "p1": a[0], "s2": round(s2, 2),
                    "s1": round((a[0] / a[2] - 1) * 100, 2) if len(a) > 2 and a[2] else None, "ch": x["ch"],
                    "tr": x.get("tr"), "v20": x.get("v20"), "blk": x.get("blk"), "warn": x.get("warn", [])})
    ih = None
    if ihsg_now and (s1.get("ihsg") or {}).get("p"):
        ih = round((ihsg_now["p"] / s1["ihsg"]["p"] - 1) * 100, 2)
    return {"status": status, "time": time, "s1_time": s1.get("time"), "s1_final": bool(s1.get("final")),
            "ihsg": ih, "breadth": _breadth([x["s2"] for x in out]), "sectors": _sectors(out, "s2"), "items": out}


def items_at_s1(items: list[dict], snap: dict) -> list[dict]:
    """Ubah daftar movers menjadi perubahan sampai akhir sesi 1 (harga snapshot vs penutupan kemarin)."""
    q = snap.get("q", {})
    out = []
    for x in items:
        a = q.get(x["c"])
        if not a or not a[0]:
            continue
        prev = a[2] if len(a) > 2 and a[2] else x["p"] / (1 + x["ch"] / 100)
        out.append({**x, "p": a[0], "ch": round((a[0] / prev - 1) * 100, 2)})
    return out


def top(items: list[dict], key: str, n: int = 3, up: bool = True) -> list[dict]:
    """Teratas untuk notifikasi: likuid (≥ Rp10 M/hari), tren bagus untuk daftar naik, tanpa penanda risiko."""
    L = [x for x in items if (x.get("v20") or 0) >= 10 and not x.get("blk") and (x["tr"] == "up" if up else True)]
    L = [x for x in L if (x[key] > 0 if up else x[key] < 0)]
    return sorted(L, key=lambda x: -x[key] if up else x[key])[:n]
