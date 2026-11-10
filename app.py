"""
Server lokal opsional (komputer sendiri / VPS / Render). Versi utama berjalan di GitHub Pages.

Menjalankan build.py secara berkala ke folder `static/` lalu menyajikannya.

  uvicorn app:app --port 8000
Env opsional:
  DASHBOARD_PASSWORD  -> aktifkan login (username bebas, password ini)
  DEMO=1              -> data sintetis
"""
from __future__ import annotations

import os
import secrets
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

import build
import strategy as st
from data import WIB

BASE = Path(__file__).resolve().parent
OUT = BASE / "static"
PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
DEMO = os.getenv("DEMO") == "1"

app = FastAPI(title="Dashboard Trading BEI", docs_url=None, redoc_url=None)
security = HTTPBasic(auto_error=False)


def auth(cred: HTTPBasicCredentials | None = Depends(security)) -> None:
    if not PASSWORD:
        return
    if cred is None or not secrets.compare_digest(cred.password.encode(), PASSWORD.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Login diperlukan", headers={"WWW-Authenticate": "Basic"})


def loop() -> None:
    last_heavy = None
    while True:
        cfg = build.load_cfg()
        now = datetime.now(WIB)
        heavy = last_heavy != now.date()
        try:
            build.build_all(OUT, heavy=heavy, demo=DEMO, cfg=cfg)
            if heavy:
                last_heavy = now.date()
        except Exception:
            traceback.print_exc()
        mstatus, _ = st.market_state(datetime.now(WIB), cfg["session"])
        time.sleep(cfg["server"]["refresh_seconds_open"] if mstatus == "buka" else cfg["server"]["refresh_seconds_closed"])


@app.on_event("startup")
def _start() -> None:
    threading.Thread(target=loop, daemon=True).start()


@app.get("/", dependencies=[Depends(auth)])
def index():
    return FileResponse(OUT / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/data/{name}", dependencies=[Depends(auth)])
def data_file(name: str):
    p = OUT / "data" / name
    if not name.endswith(".json") or "/" in name or not p.exists():
        raise HTTPException(404, "Data belum tersedia")
    return FileResponse(p, headers={"Cache-Control": "no-cache"})


@app.get("/health")
def health():
    return {"ok": True}
