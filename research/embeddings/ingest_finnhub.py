"""Poll Finnhub free news and store macro, geopolitical and gold items in research/data/embeddings.db.

Run: venv/bin/python research/embeddings/ingest_finnhub.py
Reads FINNHUB_API_KEY from the repo's .env. Never prints the key.
Dedupes on (source, source_item_id). Embeddings are a separate later step.
"""
import datetime as dt
import json
import pathlib
import re
import sqlite3
import sys
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
ENV = ROOT / ".env"
DB = ROOT / "research" / "data" / "embeddings.db"
SCHEMA = ROOT / "research" / "embeddings" / "schema.sql"
CATEGORIES = ["general", "forex", "crypto", "merger"]
SYMBOL = "XAUUSD"

GOLD = r"gold|xau|bullion|precious metals?"
MACRO = (r"fed|fomc|federal reserve|powell|rate cuts?|rate hikes?|interest rates?|treasur(y|ies)|"
         r"yields?|bonds?|inflation|cpi|ppi|payrolls?|nonfarm|jobs report|gdp|recession|"
         r"unemployment|dollar index|dxy|tariffs?|central banks?|ecb|boj|pboc|debt ceiling|"
         r"government shutdown|monetary policy|real rates?")
GEO = (r"sanctions?|geopolit\w*|wars?|conflicts?|missiles?|ceasefire|iran|israel|russia|ukraine|"
       r"taiwan|houthis?|strait|nato|coup|invasion")
KEEP = re.compile(rf"\b({GOLD}|{MACRO}|{GEO})\b", re.IGNORECASE)


def load_key():
    for line in ENV.read_text().splitlines():
        if line.startswith("FINNHUB_API_KEY="):
            value = line.split("=", 1)[1].strip()
            if value:
                return value
    sys.exit("FINNHUB_API_KEY missing from .env")


def fetch(category, token):
    url = "https://finnhub.io/api/v1/news?" + urllib.parse.urlencode({"category": category, "token": token})
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.load(r)
    if not isinstance(data, list):
        raise RuntimeError(f"unexpected response for {category}")
    return data


def utc_ms(epoch_seconds):
    t = dt.datetime.fromtimestamp(int(epoch_seconds), dt.timezone.utc)
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def main():
    token = load_key()
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text())  # idempotent
    symbol_id = con.execute("SELECT symbol_id FROM symbols WHERE code = ?", (SYMBOL,)).fetchone()[0]

    seen, kept, new = set(), 0, 0
    per_category = {}
    for category in CATEGORIES:
        items = fetch(category, token)
        per_category[category] = len(items)
        for it in items:
            item_id = str(it.get("id"))
            if item_id in seen:
                continue
            seen.add(item_id)
            text = f"{it.get('headline') or ''} {it.get('summary') or ''}"
            if not KEEP.search(text):
                continue
            kept += 1
            published = utc_ms(it["datetime"])
            cur = con.execute(
                """INSERT OR IGNORE INTO documents
                   (source, source_item_id, url, title, body, published_at, published_raw)
                   VALUES ('finnhub', ?, ?, ?, ?, ?, ?)""",
                (item_id, it.get("url"), it.get("headline") or "", it.get("summary"),
                 published, str(it["datetime"])),
            )
            if cur.rowcount:
                new += 1
                doc_id = cur.lastrowid
                con.execute(
                    "INSERT OR IGNORE INTO document_symbols (document_id, symbol_id) VALUES (?, ?)",
                    (doc_id, symbol_id),
                )
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM documents WHERE source='finnhub'").fetchone()[0]
    con.close()
    print(json.dumps({
        "fetched_per_category": per_category,
        "unique_items": len(seen),
        "matched": kept,
        "new_stored": new,
        "finnhub_documents_total": total,
        "checked_at": utc_ms(dt.datetime.now(dt.timezone.utc).timestamp()),
    }))


if __name__ == "__main__":
    main()
