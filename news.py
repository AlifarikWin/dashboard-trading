"""
Stream peristiwa: berita, keterbukaan informasi BEI, dan kalender peristiwa per saham.

Prinsip:
- Hanya judul, sumber, waktu, dan tautan yang ditampilkan (isi artikel milik media, tidak disalin).
- Label jenis peristiwa & arah (positif/negatif) memakai aturan kata kunci yang terbuka, bukan tebakan.
- Reaksi harga sejak berita dihitung dari data harga, untuk menandai berita yang "sudah dihargai pasar".
- Arsip disimpan permanen agar statistik reaksi per jenis peristiwa bisa dihitung setelah data cukup.
"""
from __future__ import annotations

import hashlib
import html
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

WIB = ZoneInfo("Asia/Jakarta")
GNEWS = "https://news.google.com/rss/search?q={q}&hl=id&gl=ID&ceid=ID:id"

# (label, kata kunci) — urutan menentukan prioritas bila beberapa cocok
EVENT_RULES = [
    ("Suspensi/UMA", r"suspen|gembok|unusual market activity|\bUMA\b|penghentian sementara"),
    ("PKPU/Pailit", r"\bPKPU\b|pailit|gagal bayar|default|restrukturisasi utang"),
    ("Aksi korporasi", r"rights? issue|\bHMETD\b|private placement|stock split|pemecahan saham|reverse stock|tender offer|go private|delisting"),
    ("Dividen", r"dividen|cum date|ex date|\bcum\b"),
    ("Buyback", r"buyback|buy back|pembelian kembali"),
    ("Akuisisi/Merger", r"akuisisi|caplok|merger|penggabungan|divestasi|ambil alih|kuasai penuh"),
    ("Laporan keuangan", r"laba|rugi bersih|kinerja|kuartal|semester|pendapatan|laporan keuangan|\bQ[1-4]\b|9M|1H|net profit|earnings"),
    ("Kontrak/Ekspansi", r"kontrak|proyek|tender|ekspansi|pabrik baru|kerja sama|kerjasama|MoU|order baru"),
    ("RUPS", r"\bRUPS\b|rapat umum pemegang saham"),
    ("Kepemilikan", r"borong|lepas saham|jual saham|tambah kepemilikan|kurangi kepemilikan|pengendali|investor asing|kepemilikan saham"),
    ("Rekomendasi analis", r"target harga|rekomendasi|analis|outperform|underperform|\bTP\b|proyeksikan"),
    ("Pergerakan harga", r"harga saham .*hari ini|dibuka (menguat|melemah)|ditutup (menguat|melemah)|zona (hijau|merah)|"
                         r"sesi i\b|top gainers|top losers|jenuh (beli|jual)|\bIHSG\b"),
]
ROUTINE = {"Pergerakan harga", "Umum"}  # disembunyikan secara bawaan di Stream
POS = (r"naik|melonjak|melesat|menguat|tumbuh|meningkat|rekor|cetak laba|untung|jumbo|kontrak baru|ekspansi|buyback|"
       r"borong|terbang|\bARA\b|outperform|upgrade|moncer|melambung|menanjak|bullish|positif|kenaikan|surplus|cuan|"
       r"kinclong|ciamik|perkuat|akselerasi")
NEG = (r"turun|anjlok|melemah|merosot|rugi|tekor|gagal bayar|PKPU|pailit|suspen|gembok|terjun|ambles|\bARB\b|"
       r"lepas saham|downgrade|underperform|penurunan|bearish|negatif|koreksi|tertekan|longsor|jeblok|rontok|defisit|"
       r"gugatan|sanksi|boncos|merah|ambruk|terkoreksi")


def label(title: str) -> tuple[str, str]:
    t = title or ""
    kind = next((k for k, rx in EVENT_RULES if re.search(rx, t, re.I)), "Umum")
    p, n = len(re.findall(POS, t, re.I)), len(re.findall(NEG, t, re.I))
    tone = "positif" if p > n else "negatif" if n > p else "netral"
    return kind, tone


def _id(*parts) -> str:
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def mentions(title: str, code: str, name: str | None) -> bool:
    """Judul harus menyebut kode saham atau nama perusahaan agar tidak salah pasang."""
    if re.search(rf"\b{re.escape(code)}\b", title, re.I):
        return True
    if name:
        core = re.sub(r"\b(PT|Tbk|\(Persero\)|Persero|Indonesia)\b\.?", "", name, flags=re.I).strip()
        words = [w for w in re.split(r"\s+", core) if len(w) >= 4][:2]
        if words and all(re.search(rf"\b{re.escape(w)}\b", title, re.I) for w in words):
            return True
    return False


# ----------------------------------------------------------------------------
# Pengambil berita
# ----------------------------------------------------------------------------
def fetch_google_news(code: str, name: str | None, session, max_items: int = 8, days: int = 10) -> list[dict]:
    q = urllib.parse.quote(f'"{code}" saham')
    r = session.get(GNEWS.format(q=q), timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Google News HTTP {r.status_code}")
    root = ET.fromstring(r.content)
    out, cutoff = [], datetime.now(timezone.utc) - timedelta(days=days)
    for it in root.iter("item"):
        title = html.unescape(it.findtext("title") or "")
        src = it.find("source")
        source = src.text if src is not None else ""
        if source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)]
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate"))
        except (TypeError, ValueError):
            continue
        if pub < cutoff or not mentions(title, code, name):
            continue
        out.append({"code": code, "title": title, "url": it.findtext("link"), "source": source or "Google News",
                    "published": pub.astimezone(WIB).isoformat(), "via": "Google News"})
        if len(out) >= max_items:
            break
    return out


def fetch_yahoo_news(code: str, name: str | None, max_items: int = 6, days: int = 10) -> list[dict]:
    import yfinance as yf
    items = yf.Ticker(f"{code}.JK").news or []
    out, cutoff = [], datetime.now(timezone.utc) - timedelta(days=days)
    for n in items:
        c = n.get("content") or n
        title = c.get("title") or ""
        url = ((c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict) else None) or c.get("link")
        prov = (c.get("provider") or {}).get("displayName") if isinstance(c.get("provider"), dict) else c.get("publisher")
        ts = c.get("pubDate") or c.get("providerPublishTime")
        try:
            pub = datetime.fromtimestamp(ts, timezone.utc) if isinstance(ts, (int, float)) else datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if not title or not url or pub < cutoff or not mentions(title, code, name):
            continue
        out.append({"code": code, "title": title, "url": url, "source": prov or "Yahoo Finance",
                    "published": pub.astimezone(WIB).isoformat(), "via": "Yahoo Finance"})
        if len(out) >= max_items:
            break
    return out


def from_idx_announcements(rows: list[dict], universe: set[str]) -> list[dict]:
    """Keterbukaan informasi resmi BEI (diambil runner laptop)."""
    out = []
    for r in rows or []:
        p = r.get("pengumuman") or r
        code = (p.get("Kode_Emiten") or p.get("code") or "").strip()
        if not code or (universe and code not in universe):
            continue
        title = (p.get("JudulPengumuman") or p.get("title") or "").strip()
        when = p.get("TglPengumuman") or p.get("date")
        try:
            pub = datetime.fromisoformat(str(when).replace("Z", "")).replace(tzinfo=WIB)
        except ValueError:
            continue
        att = r.get("attachments") or []
        url = att[0].get("FullSavePath") if att and isinstance(att[0], dict) else "https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi/"
        out.append({"code": code, "title": title, "url": url, "source": "Keterbukaan Informasi BEI",
                    "published": pub.isoformat(), "via": "BEI", "official": True})
    return out


def collect(codes: list[str], names: dict[str, str], idx_announcements: list[dict] | None = None,
            delay: float = 0.3) -> tuple[list[dict], list[str]]:
    from curl_cffi import requests as creq
    s = creq.Session(impersonate="chrome")
    items, errors = [], []
    g_fail = y_fail = 0
    for c in codes:
        if g_fail < 3:
            try:
                items += fetch_google_news(c, names.get(c), s)
            except Exception as e:
                g_fail += 1
                if g_fail == 3:
                    errors.append(f"Google News: {type(e).__name__} {str(e)[:60]}")
        if y_fail < 3:
            try:
                items += fetch_yahoo_news(c, names.get(c))
            except Exception as e:
                y_fail += 1
                if y_fail == 3:
                    errors.append(f"Yahoo news: {type(e).__name__} {str(e)[:60]}")
        time.sleep(delay)
    items += from_idx_announcements(idx_announcements or [], set(codes))
    return items, errors


# ----------------------------------------------------------------------------
# Pengolahan: label, reaksi harga, arsip, statistik
# ----------------------------------------------------------------------------
def enrich(items: list[dict], frames: dict[str, pd.DataFrame]) -> list[dict]:
    seen, out = set(), []
    for it in sorted(items, key=lambda x: (not x.get("official"), x["published"])):
        key = (it["code"], _norm(it["title"])[:80])
        if key in seen:
            continue
        seen.add(key)
        it = dict(it)
        it["id"] = _id(it["code"], _norm(it["title"])[:80])
        it["kind"], it["tone"] = label(it["title"])
        it["routine"] = it["kind"] in ROUTINE
        it["reaction"] = reaction(frames.get(it["code"]), it["published"])
        out.append(it)
    return sorted(out, key=lambda x: x["published"], reverse=True)


def reaction(df: pd.DataFrame | None, published: str) -> dict | None:
    """Perubahan harga dari penutupan sebelum berita sampai harga terakhir."""
    if df is None or df.empty:
        return None
    pub = datetime.fromisoformat(published).astimezone(WIB)
    # berita setelah 16.00 WIB baru bisa direspons hari bursa berikutnya
    ref_day = pd.Timestamp(pub.date()) if pub.hour < 16 else pd.Timestamp(pub.date()) + pd.Timedelta(days=1)
    before = df[df.index < ref_day]
    if before.empty:
        return None
    ref = float(before["Close"].iloc[-1])
    last = float(df["Close"].iloc[-1])
    after = df[df.index >= ref_day]
    vmax = float(after["Volume"].max() / df["Volume"].iloc[-21:-1].mean()) if len(after) and len(df) > 21 else None
    move = (last / ref - 1) * 100
    return {"ref": ref, "last": last, "move_pct": round(move, 2), "days": int(len(after)),
            "vol_ratio": round(vmax, 2) if vmax else None, "priced_in": abs(move) >= 5}


def merge_archive(archive: dict, items: list[dict], keep_days: int = 180) -> dict:
    for it in items:
        if it["id"] not in archive:
            archive[it["id"]] = {k: it[k] for k in ("id", "code", "title", "url", "source", "published", "kind", "tone", "via")} \
                | {"official": bool(it.get("official"))}
    cutoff = (datetime.now(WIB) - timedelta(days=keep_days)).isoformat()
    return {k: v for k, v in archive.items() if v["published"] >= cutoff}


def event_stats(archive: dict, frames: dict[str, pd.DataFrame], min_n: int = 10) -> list[dict]:
    """Rata-rata perubahan harga 1/3/5 hari bursa setelah peristiwa, per jenis peristiwa."""
    rows = []
    for a in archive.values():
        df = frames.get(a["code"])
        if df is None or len(df) < 30:
            continue
        pub = datetime.fromisoformat(a["published"]).astimezone(WIB)
        ref_day = pd.Timestamp(pub.date()) if pub.hour < 16 else pd.Timestamp(pub.date()) + pd.Timedelta(days=1)
        before, after = df[df.index < ref_day], df[df.index >= ref_day]
        if before.empty or len(after) < 5:
            continue
        ref = float(before["Close"].iloc[-1])
        r = {h: (float(after["Close"].iloc[h - 1]) / ref - 1) * 100 for h in (1, 3, 5)}
        kind, tone = label(a["title"])  # label dihitung ulang agar perbaikan aturan berlaku ke arsip lama
        if kind in ROUTINE:
            continue
        rows.append({"kind": kind, "tone": tone, **{f"r{h}": v for h, v in r.items()}})
    if not rows:
        return []
    t = pd.DataFrame(rows)
    out = []
    for (kind, tone), g in t.groupby(["kind", "tone"]):
        n = len(g)
        out.append({"kind": kind, "tone": tone, "n": int(n), "ready": n >= min_n,
                    "r1": round(float(g["r1"].mean()), 2), "r3": round(float(g["r3"].mean()), 2),
                    "r5": round(float(g["r5"].mean()), 2), "up5": round(float((g["r5"] > 0).mean() * 100), 1)})
    return sorted(out, key=lambda x: -x["n"])


# ----------------------------------------------------------------------------
# Kalender peristiwa
# ----------------------------------------------------------------------------
def yahoo_calendar(codes: list[str], delay: float = 0.3) -> tuple[list[dict], list[str]]:
    import yfinance as yf
    out, errors, fails = [], [], 0
    for c in codes:
        try:
            cal = yf.Ticker(f"{c}.JK").calendar or {}
            if isinstance(cal, pd.DataFrame):
                cal = cal.to_dict()
            for k, kind in (("Earnings Date", "Rilis laporan keuangan (perkiraan)"), ("Ex-Dividend Date", "Ex-date dividen"),
                            ("Dividend Date", "Pembayaran dividen")):
                v = cal.get(k)
                vals = v if isinstance(v, (list, tuple)) else [v]
                for d in vals:
                    if d is None or (isinstance(d, float) and np.isnan(d)):
                        continue
                    ds = str(pd.Timestamp(d).date())
                    out.append({"code": c, "date": ds, "kind": kind, "source": "Yahoo Finance"})
        except Exception as e:
            fails += 1
            if fails == 3:
                errors.append(f"Kalender Yahoo: {type(e).__name__}")
        time.sleep(delay)
    return out, errors


def idx_calendar(rows: list[dict] | None) -> list[dict]:
    out = []
    for r in rows or []:
        code = (r.get("title") or r.get("code") or "").strip()
        try:
            ds = str(pd.Timestamp(r.get("start") or r.get("date")).date())
        except (TypeError, ValueError):
            continue
        kind = (r.get("Jenis") or r.get("type") or "Agenda").strip()
        desc = (r.get("description") or "").strip()
        if code:
            out.append({"code": code, "date": ds, "kind": kind + (f": {desc[:60]}" if desc else ""), "source": "Kalender BEI"})
    return out


def upcoming(events: list[dict], today, days: int = 10) -> list[dict]:
    lo, hi = str(today), str(today + timedelta(days=days))
    seen, out = set(), []
    for e in sorted(events, key=lambda x: (x["date"], x["code"])):
        k = (e["code"], e["date"], e["kind"][:20])
        if lo <= e["date"] <= hi and k not in seen:
            seen.add(k)
            out.append(e)
    return out
