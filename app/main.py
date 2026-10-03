"""FastAPI application: static dashboard + JSON analytics API."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import analytics, service
from .config import get_settings
from .db import WarehouseError, connect, query

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="Axioma Analytics",
    description="Tick-data microstructure analytics over a Microsoft Fabric SQL analytics endpoint.",
    version="1.0.0",
)


@app.get("/api/health")
def health(refresh: bool = Query(False, description="Re-probe the warehouse")) -> dict:
    """Connection status, discovered tables and actionable hints."""
    s = get_settings()
    st = service.status(refresh=refresh)
    # Warm the symbol lookup so the UI can show tickers instead of raw ids.
    service.symbol_map()
    return {
        "app": s.app_name,
        "connected": st.connected,
        "synthetic_available": s.allow_synthetic,
        "using_synthetic": bool(not st.connected and s.allow_synthetic),
        "server_time": st.server_time,
        "error": st.error,
        "hints": st.hints,
        "tables": st.tables,
        "databases": st.databases,
        "symbol_dimension": service._SYMBOL_DIM,
        "symbol_count": len(service._SYMBOL_LABELS),
        "auth": "service principal" if s.has_credentials else "az login access token",
        "endpoint": f"{s.server}:{s.port}",
        "timeframes": list(analytics.TIMEFRAMES),
    }


@app.get("/api/sources")
def sources() -> dict:
    """Tick sources available for analysis, with their symbols."""
    out = []
    for src in service.sources():
        d = src.to_dict()
        rows = [{"symbol": s} for s in service.symbols_for(src.key)]
        d["symbols"] = service.decorate(rows)
        out.append(d)
    return {"sources": out, "symbol_dimension": service._SYMBOL_DIM}


@app.get("/api/symbols")
def symbols(source: str = Query("synthetic")) -> dict:
    rows = [{"symbol": s} for s in service.symbols_for(source)]
    return {"symbols": service.decorate(rows)}


@app.get("/api/analytics")
def analytics_report(
    source: str = Query("synthetic", description="schema.table, or 'synthetic'"),
    symbol: str | None = Query(None),
    timeframe: str = Query("1m"),
    window: int = Query(50, ge=2, le=5000),
    bins: int = Query(60, ge=10, le=300),
    limit: int = Query(50_000, ge=100, le=500_000),
    lookback_days: int = Query(3, ge=1, le=90),
) -> dict:
    """Full analytics bundle for one symbol."""
    try:
        frame, is_synthetic = service.load_ticks_cached(source, symbol, limit, lookback_days)
    except WarehouseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    report = analytics.build_report(frame, timeframe=timeframe, window=window, bins=bins)
    report["meta"] = {
        "source": source,
        "symbol": symbol or report["summary"].get("symbol"),
        "symbol_name": service.symbol_map().get(str(symbol)) if symbol else None,
        "synthetic": is_synthetic,
        "rows_analysed": int(len(frame)),
        "generated_at": pd.Timestamp.utcnow().isoformat(),
    }
    return report


_SELECT_ONLY = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|truncate|merge|grant|exec|execute)\b", re.IGNORECASE)


@app.post("/api/query")
def ad_hoc_query(sql: str = Query(..., description="A single read-only SELECT statement")) -> dict:
    """Run one read-only SELECT. Useful for exploring an unfamiliar schema."""
    if not _SELECT_ONLY.match(sql or ""):
        raise HTTPException(status_code=400, detail="Only SELECT / WITH queries are allowed.")
    if _FORBIDDEN.search(sql):
        raise HTTPException(status_code=400, detail="Only read-only queries are allowed.")
    try:
        with connect() as conn:
            df = query(conn, sql)
    except WarehouseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "columns": list(df.columns),
        "rows": df.head(200).astype(object).where(pd.notna(df.head(200)), None).to_dict("records"),
        "row_count": int(len(df)),
    }


@app.get("/")
def index() -> Any:
    return FileResponse(STATIC_DIR / "index.html")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(WarehouseError)
def _warehouse_handler(_request, exc: WarehouseError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})