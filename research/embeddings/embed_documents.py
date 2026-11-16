"""Embed stored news documents that have no vector yet, using the configured embedding model.

Run: venv/bin/python research/embeddings/embed_documents.py
Reads the endpoint and key from OpenClaw's memory.search config (never printed).
Idempotent: only documents missing a vector for this model are processed.
Vectors are float32 little-endian, stored with model name, version and dimension.
"""
import array
import datetime as dt
import json
import pathlib
import sqlite3
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB = ROOT / "research" / "data" / "embeddings.db"
SCHEMA = ROOT / "research" / "embeddings" / "schema.sql"
OPENCLAW_CONFIG = pathlib.Path.home() / ".openclaw" / "openclaw.json"
MODEL = "text-embedding-3-large"
MODEL_VERSION = ""
DIMENSION = 3072
BATCH = 16
MAX_CHARS = 6000  # well under the model's token limit for headline + summary


def endpoint_and_key():
    cfg = json.loads(OPENCLAW_CONFIG.read_text())
    remote = cfg["memory"]["search"]["remote"]
    base = remote["baseUrl"].rstrip("/")
    key = remote["apiKey"]
    if not isinstance(key, str) or not key:
        sys.exit("embedding apiKey is not a plain string in openclaw config")
    model = cfg["memory"]["search"].get("model")
    if model != MODEL:
        sys.exit(f"configured embedding model is {model!r}, expected {MODEL!r}")
    return base + "/embeddings", key


def embed(url, key, texts):
    body = json.dumps({"model": MODEL, "input": texts}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + key,
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"embedding request failed: HTTP {e.code}") from None
    rows = sorted(data["data"], key=lambda d: d["index"])
    vectors = []
    for row in rows:
        vec = row["embedding"]
        if len(vec) != DIMENSION:
            raise RuntimeError(f"unexpected dimension {len(vec)}")
        vectors.append(vec)
    return vectors


def main():
    url, key = endpoint_and_key()
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text())

    pending = con.execute(
        """SELECT d.document_id, d.title, COALESCE(d.body, '')
           FROM documents d
           WHERE NOT EXISTS (
             SELECT 1 FROM embeddings e
             WHERE e.document_id = d.document_id AND e.model_name = ? AND e.model_version = ?)
           ORDER BY d.document_id""",
        (MODEL, MODEL_VERSION),
    ).fetchall()

    done = 0
    for i in range(0, len(pending), BATCH):
        chunk = pending[i:i + BATCH]
        texts = [(title + "\n" + body)[:MAX_CHARS] for _, title, body in chunk]
        vectors = embed(url, key, texts)
        for (doc_id, _, _), vec in zip(chunk, vectors):
            blob = array.array("f", vec).tobytes()
            con.execute(
                """INSERT OR REPLACE INTO embeddings
                   (document_id, model_name, model_version, dimension, vector, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (doc_id, MODEL, MODEL_VERSION, DIMENSION, blob,
                 dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")),
            )
            done += 1
        con.commit()

    total = con.execute(
        "SELECT COUNT(*) FROM embeddings WHERE model_name = ? AND model_version = ?",
        (MODEL, MODEL_VERSION)).fetchone()[0]
    con.close()
    print(json.dumps({"model": MODEL, "dimension": DIMENSION, "embedded_now": done,
                      "vectors_total": total}))


if __name__ == "__main__":
    main()
