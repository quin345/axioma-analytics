"""FastAPI application: static dashboard + JSON analytics API."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import analytics, service
from .assets import CLASS_LABELS
from .config import get_settings
from .db import DataSourceError

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="Axioma Analytics",
    description="Microstructure analytics over the production gold book snapshots.",
    version="2.0.0",
)


@app.get("/api/health")
def health(refresh: bool = Query(False, description="Re-probe the gold endpoint")) -> dict:
    """Connection status, instrument coverage and hints.

    Storage internals (endpoint host, database, table) are deliberately not
    exposed: the client only needs to know whether data is available.
    """
    s = get_settings()
    st = service.status(refresh=refresh)

    traded = classified = unclassified = 0
    classes = 0
    note = None
    if st.connected:
        try:
            # class_summary now excludes instruments the dimension cannot
            # classify, because the selectors do. Deriving `traded` from it
            # would under-report the snapshot and quietly report zero
            # unclassified while the coverage note still lists them, so the
            # totals come from the catalogue itself and stay independent of
            # what the pickers choose to show.
            summary = service.class_summary(only_traded=True)
            classes = len(summary)
            traded = service.traded_count()
            unclassified = service.unclassified_count()
            classified = traded - unclassified
            note = service.coverage_note()
        except DataSourceError:
            pass

    hints = list(st.hints)
    if note:
        hints.append(note)

    return {
        "app": s.app_name,
        "connected": st.connected,
        "server_time": st.server_time,
        "latest_snapshot": st.latest,
        "row_count": st.row_count,
        "error": st.error,
        "hints": hints,
        "has_data": bool(traded),
        "symbol_count": traded,
        "classified_count": classified,
        "unclassified_count": unclassified,
        "asset_class_count": classes,
        "timeframes": list(analytics.TIMEFRAMES),
    }


@app.get("/api/asset-classes")
def asset_classes() -> dict:
    """The asset-class taxonomy plus a per-class instrument rollup."""
    return {
        "classes": service.taxonomy(),
        "summary": service.class_summary(only_traded=True) if service.status().connected else [],
    }


@app.get("/api/symbols")
def symbols(
    asset_class: str | None = Query(None, description="Filter to one asset class"),
    family: str | None = Query(None, description="Filter to one broad family"),
    include_idle: bool = Query(False, description="Include instruments with no snapshots"),
) -> dict:
    """Instruments with their asset class, grouped for the selector."""
    groups = service.grouped_symbols(only_traded=not include_idle)
    if family:
        groups = [g for g in groups if g["family"] == family]
    if asset_class:
        groups = [g for g in groups if g["key"] == asset_class]
        if not groups:
            raise HTTPException(status_code=400, detail=f"Unknown asset class '{asset_class}'.")

    summary = [{k: g[k] for k in ("key", "label", "family", "family_label", "count", "ticks")}
               for g in groups]
    flat = [s for g in groups for s in g["symbols"]]
    return {
        "groups": groups,
        "summary": summary,
        "symbols": flat,
        "total": len(flat),
    }


@app.get("/api/analytics")
def analytics_report(
    symbol: str | None = Query(None, description="Symbol id; omit to analyse all"),
    timeframe: str = Query("1m"),
    window: int = Query(50, ge=2, le=5000),
    bins: int = Query(60, ge=10, le=300),
    limit: int = Query(50_000, ge=100, le=500_000),
    lookback_days: int = Query(3, ge=1, le=90),
) -> dict:
    """Full analytics bundle for one symbol."""
    try:
        frame = service.load_ticks_cached(symbol, limit, lookback_days)
    except DataSourceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    report = analytics.build_report(frame, timeframe=timeframe, window=window, bins=bins)

    inst = service.lookup(symbol) if symbol else None
    meta = {
        "symbol": symbol or report["summary"].get("symbol"),
        "symbol_name": inst.display if inst else None,
        "asset_class": inst.asset_class if inst else None,
        "asset_class_label": CLASS_LABELS.get(inst.asset_class, "Unclassified") if inst else None,
        "family": inst.family if inst else None,
        "rows_analysed": int(len(frame)),
        "generated_at": pd.Timestamp.utcnow().isoformat(),
    }
    report["meta"] = meta
    return report


@app.get("/")
def index() -> Any:
    return FileResponse(STATIC_DIR / "index.html")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(DataSourceError)
def _warehouse_handler(_request, exc: DataSourceError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})