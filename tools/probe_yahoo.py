"""Uji: daftar seluruh emiten JKT dari screener Yahoo + waktu unduh harga massal."""
import time, json
import yfinance as yf
from yfinance import EquityQuery

q = EquityQuery("eq", ["exchange", "JKT"])
rows, off = [], 0
while True:
    r = yf.screen(q, offset=off, size=250, sortField="intradaymarketcap", sortAsc=False)
    qs = r.get("quotes", [])
    rows += qs
    print("offset", off, "got", len(qs), "total", r.get("total"))
    if len(qs) < 250 or off > 2000:
        break
    off += 250
print("jumlah emiten:", len(rows))
print("contoh field:", sorted(rows[0].keys())[:60])
print(json.dumps({k: rows[0].get(k) for k in ("symbol", "shortName", "longName", "sector", "industry", "regularMarketPrice", "marketCap", "quoteType")}, ensure_ascii=False))
syms = [x["symbol"] for x in rows if x.get("quoteType") == "EQUITY"]
print("equity:", len(syms), syms[:5], syms[-5:])
t = time.time()
d = yf.download(syms, period="1y", interval="1d", group_by="ticker", auto_adjust=False, threads=True, progress=False)
print("download 1y:", round(time.time() - t, 1), "s", d.shape)
ok = sum(1 for s in syms if s in d.columns.get_level_values(0) and d[s]["Close"].notna().sum() > 60)
print("saham dengan >60 bar:", ok)
