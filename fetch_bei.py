#!/usr/bin/env python3
"""
Dijalankan di LAPTOP (GitHub self-hosted runner) karena situs BEI menolak server cloud.

Mengambil data resmi BEI lalu menyimpannya ke penyimpanan permanen (branch `records`):
  idx/summary/YYYYMMDD.json  ringkasan harian seluruh saham (+ foreign buy/sell)
  idx/screener.json          notasi khusus, status, UMA, aksi korporasi, sektor
  idx/announcements.json     keterbukaan informasi (10 hari terakhir, digabung)
  idx/calendar.json          kalender BEI (bulan ini & bulan depan)
  idx/suspend.json           daftar suspensi terbaru
Setelah selesai, memicu workflow dashboard agar halaman langsung diperbarui.

  python fetch_bei.py --store store
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import idx
from data import WIB


def save(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"fetched_at": datetime.now(WIB).isoformat(timespec="minutes"), "results": obj},
                            ensure_ascii=False))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="store")
    ap.add_argument("--days", type=int, default=120)
    a = ap.parse_args()
    store = Path(a.store)
    client = idx.IdxClient(store / "idx" / "summary")
    report, ok_summary = [], False
    today = datetime.now(WIB).date()

    panel, errors = client.history(a.days)
    if not panel.empty:
        ok_summary = True
        report.append(f"ringkasan harian: {panel['date'].nunique()} hari, {panel['code'].nunique()} saham, "
                      f"terakhir {panel['date'].max().date()}")
    else:
        report.append(f"ringkasan harian GAGAL: {client.last_error or errors[:3]}")

    jobs = {
        "screener": lambda: idx.screener(client),
        "suspend": lambda: idx.suspensions(client),
        "calendar": lambda: idx.calendar(client, today) + idx.calendar(client, (today.replace(day=1) + timedelta(days=32)).replace(day=1)),
    }
    for name, fn in jobs.items():
        try:
            rows = fn()
            save(store / "idx" / f"{name}.json", rows)
            report.append(f"{name}: {len(rows)} baris")
        except Exception as e:
            report.append(f"{name} GAGAL: {type(e).__name__} {str(e)[:80]}")

    try:
        new = idx.announcements(client, today - timedelta(days=10), today)
        f = store / "idx" / "announcements.json"
        old = json.loads(f.read_text())["results"] if f.exists() else []
        merged = {}
        for r in old + new:
            p = r.get("pengumuman") or {}
            key = p.get("Id2") or p.get("NoPengumuman") or json.dumps(p, sort_keys=True)[:80]
            merged[key] = r
        cutoff = str(today - timedelta(days=60))
        keep = [r for r in merged.values() if str((r.get("pengumuman") or {}).get("TglPengumuman", ""))[:10] >= cutoff]
        save(f, keep)
        report.append(f"pengumuman: {len(new)} baru, {len(keep)} tersimpan")
    except Exception as e:
        report.append(f"pengumuman GAGAL: {type(e).__name__} {str(e)[:80]}")

    print("\n".join(report))
    (store / "idx").mkdir(parents=True, exist_ok=True)
    (store / "idx" / "last_run.json").write_text(json.dumps(
        {"at": datetime.now(WIB).isoformat(timespec="minutes"), "report": report, "ok": ok_summary}, ensure_ascii=False))
    return 0 if ok_summary else 2


def dispatch_dashboard() -> None:
    """Picu workflow dashboard (izin: GITHUB_TOKEN dengan actions: write)."""
    tok, repo = os.getenv("GITHUB_TOKEN"), os.getenv("GITHUB_REPOSITORY")
    if not tok or not repo:
        return
    from curl_cffi import requests as creq
    r = creq.post(f"https://api.github.com/repos/{repo}/actions/workflows/dashboard.yml/dispatches",
                  headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
                  json={"ref": "main", "inputs": {"heavy": "true"}}, timeout=20)
    print("picu dashboard:", r.status_code)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--dispatch":
        dispatch_dashboard()
        sys.exit(0)
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
