-- Word-embedding store for news. Separate from webapp/data/axioma.db on purpose.
-- Timestamps are ISO-8601 UTC with milliseconds, e.g. 2026-11-16T07:20:00.000Z.

PRAGMA foreign_keys = ON;

-- One row per instrument. Adding a symbol = inserting a row here.
CREATE TABLE IF NOT EXISTS symbols (
    symbol_id    INTEGER PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,        -- e.g. XAUUSD
    name         TEXT NOT NULL,               -- e.g. Gold / US dollar
    asset_class  TEXT NOT NULL,               -- e.g. commodity, fx, equity
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- One row per article. Keep the raw timestamp exactly as the source sent it.
CREATE TABLE IF NOT EXISTS documents (
    document_id      INTEGER PRIMARY KEY,
    source           TEXT NOT NULL,           -- e.g. alphavantage
    source_item_id   TEXT,                    -- the provider's own id or URL hash
    url              TEXT,
    title            TEXT NOT NULL,
    body             TEXT,
    published_at     TEXT NOT NULL,           -- normalised UTC, ms precision
    published_raw    TEXT NOT NULL,           -- the timestamp string as received
    ingested_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (source, source_item_id)
);
CREATE INDEX IF NOT EXISTS documents_published_idx ON documents (published_at);

-- Many-to-many: an article can concern several symbols, and a symbol many articles.
CREATE TABLE IF NOT EXISTS document_symbols (
    document_id  INTEGER NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    symbol_id    INTEGER NOT NULL REFERENCES symbols(symbol_id),
    relevance    REAL,                        -- provider's ticker relevance score, if given
    PRIMARY KEY (document_id, symbol_id)
);
CREATE INDEX IF NOT EXISTS document_symbols_symbol_idx ON document_symbols (symbol_id);

-- One vector per (document, model). Storing the model with each vector stops
-- two embedding models from mixing in the same search.
CREATE TABLE IF NOT EXISTS embeddings (
    document_id    INTEGER NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    model_name     TEXT NOT NULL,             -- e.g. text-embedding-3-large
    model_version  TEXT NOT NULL DEFAULT '',
    dimension      INTEGER NOT NULL,
    vector         BLOB NOT NULL,             -- float32 little-endian, length = dimension * 4
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (document_id, model_name, model_version)
);

-- Seed: XAUUSD is the first symbol.
INSERT OR IGNORE INTO symbols (code, name, asset_class) VALUES ('XAUUSD', 'Gold / US dollar', 'commodity');
