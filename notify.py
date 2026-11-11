"""
Notifikasi ke HP lewat ntfy (https://ntfy.sh) — gratis, tanpa akun.

Atur secret NTFY_TOPIC di GitHub (nama topik acak dan panjang, misalnya trading-ali-8f3k2q9x).
Di HP: pasang aplikasi ntfy, lalu Subscribe ke topik yang sama.
Setiap pesan hanya dikirim sekali (status disimpan di penyimpanan permanen).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


def send(title: str, body: str, tags: str = "chart_with_upwards_trend", priority: str = "default",
         click: str | None = None) -> bool:
    topic = os.getenv("NTFY_TOPIC", "").strip()
    if not topic:
        return False
    from curl_cffi import requests as creq
    # publikasi format JSON agar judul/teks UTF-8 aman (https://docs.ntfy.sh/publish/#publish-as-json)
    payload = {"topic": topic, "title": title, "message": body, "tags": [t for t in tags.split(",") if t],
               "priority": {"min": 1, "low": 2, "default": 3, "high": 4, "max": 5, "urgent": 5}.get(priority, 3)}
    if click:
        payload["click"] = click
    try:
        r = creq.post("https://ntfy.sh/", json=payload, timeout=15)
        return r.status_code == 200
    except Exception:
        return False


class Once:
    """Pencatat agar notifikasi yang sama tidak terkirim dua kali."""

    def __init__(self, store: Path):
        self.f = store / "notify_state.json"
        try:
            self.sent = json.loads(self.f.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self.sent = {}

    def __call__(self, key: str, title: str, body: str, **kw) -> bool:
        if key in self.sent:
            return False
        ok = send(title, body, **kw)
        if ok:
            self.sent[key] = datetime.now().isoformat(timespec="minutes")
        return ok

    def save(self) -> None:
        # simpan 500 kunci terakhir saja
        keep = dict(sorted(self.sent.items(), key=lambda kv: kv[1])[-500:])
        self.f.parent.mkdir(parents=True, exist_ok=True)
        self.f.write_text(json.dumps(keep))
