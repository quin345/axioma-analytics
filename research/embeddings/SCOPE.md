# Embeddings news scope

<!-- project: path:/home/azureuser/axioma-analytics -->

Status: design. Ingestion is blocked on a news-source decision (see below).

## Symbols

- First symbol: XAUUSD (gold). The schema is multi-symbol: adding a symbol is an insert into `symbols`.

## What counts as relevant for XAUUSD

Gold news is not only gold news. The collected set must include stories that move gold through:

- **Geopolitics:** conflicts, sanctions, elections, central-bank independence, and other shocks that drive safe-haven demand.
- **Bond markets:** Treasury yields, the yield curve, real yields, and inflation expectations.
- **US interest rates:** Fed decisions, FOMC minutes and speeches, rate expectations, and the macro data that drives them.

Gold-specific stories (mining, central-bank gold buying, ETF flows) are included as well.

## Open decision

News source. Finnhub's free tier has almost no gold coverage (0 gold items in 100 general news items). Options:

1. Alpha Vantage paid plan (`NEWS_SENTIMENT`, with topic and ticker filters). Recommended.
2. Finnhub paid plan (coverage not yet verified).
3. Gold-specific RSS sources (terms must be checked per source).

Whatever the source, geopolitics has no reliable single topic tag. Expect to filter by keyword as well.

## Embedding model

`text-embedding-3-large` on the existing Azure endpoint, 3072 dimensions. Stored with each vector as `model_name` and `dimension`.
