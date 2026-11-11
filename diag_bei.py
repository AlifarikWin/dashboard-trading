"""Diagnosis akses situs BEI dari laptop: status, header, dan cuplikan respons (tanpa mencoba menembus proteksi)."""
from curl_cffi import requests as creq

URLS = ["https://www.idx.co.id/id",
        "https://www.idx.co.id/primary/TradingSummary/GetStockSummary?date=20261110"]
s = creq.Session(impersonate="chrome")
for u in URLS:
    try:
        r = s.get(u, timeout=30, headers={"Accept": "application/json, text/plain, */*", "Referer": "https://www.idx.co.id/id"})
        h = {k: v for k, v in r.headers.items() if k.lower() in ("server", "cf-mitigated", "cf-ray", "content-type")}
        body = r.text[:300].replace("\n", " ")
        print(f"{r.status_code} {u}\n  {h}\n  {body}\n")
    except Exception as e:
        print(f"ERR {u} {type(e).__name__} {e}")
