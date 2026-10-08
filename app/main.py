"""FastAPI application: static dashboard + JSON analytics API."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import analytics, refresh, service
from .assets import CLASS_LABELS
from .config import get_settings
from .errors import DataSourceError

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Run the cache refresh cycle for the life of the server.

    The fixed window is only as useful as the cycle that re-reads it: without
    one, the cache holds whatever the first request of the day happened to
    find. It starts with the app and is cancelled on shutdown; `run()` refreshes
    immediately, so a restart warms the window instead of leaving the next
    visitor to pay for it.
    """
    task = refresh.start()
    try:
        yield
    finally:
        await refresh.shutdown(task)


app = FastAPI(
    title="Axioma Analytics",
    description="Microstructure analytics over the production KQL aggregate DOM rows.",
    version="2.0.0",
    lifespan=lifespan,
)


@app.get("/api/health")
def health(refresh: bool = Query(False, description="Re-probe the data endpoints")) -> dict:
    """Connection status, cache state, instrument coverage and hints.

    Storage internals (endpoint host, database, table) are deliberately not
    exposed: the client only needs to know whether data is available. The cache
    is reported alongside because it decides whether a request costs a KQL
    query, but a cache outage is not an outage - reads fall through to KQL.
    """
    s = get_settings()
    st = service.status(refresh=refresh)
    # Read the refresh state live rather than off the probe: the probe is cached
    # between `refresh=true` calls, so a snapshot of it would show the cycle's
    # stamp frozen at whenever the probe last ran.
    last = service.last_refresh() or {}

    traded = classified = unclassified = 0
    classes = 0
    if st.connected:
        try:
            # class_summary now excludes instruments the dimension cannot
            # classify, because the selectors do. Deriving `traded` from it
            # would under-report the metrics and quietly report zero
            # unclassified while the coverage note still lists them, so the
            # totals come from the catalogue itself and stay independent of
            # what the pickers choose to show.
            summary = service.class_summary(only_traded=True)
            classes = len(summary)
            traded = service.traded_count()
            unclassified = service.unclassified_count()
            classified = traded - unclassified
        except DataSourceError:
            pass

    hints = list(st.hints)

    return {
        "app": s.app_name,
        "connected": st.connected,
        "server_time": st.server_time,
        "latest_tick": st.latest,
        "row_count": st.row_count,
        "dimension_rows": st.dimension_rows,
        "error": st.error,
        "hints": hints,
        "cache_connected": st.cache_connected,
        "cache_error": st.cache_error,
        # The fixed window the cache holds. The UI may narrow it, never widen
        # it, so it is published as the ceiling for the duration control.
        "lookback_minutes": st.lookback_minutes or s.cache_lookback_minutes,
        "cache_ttl_seconds": s.cache_ttl_seconds,
        # The cycle that keeps that window current: how often it runs, when it
        # last finished and how many symbols it replaced.
        "cache_refresh_minutes": s.cache_refresh_minutes,
        "cache_refreshed_at": last.get("finished_at"),
        "cached_symbols": last.get("symbols"),
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
    include_idle: bool = Query(False, description="Include instruments with no aggregate rows"),
) -> dict:
    """Instruments with their asset class, grouped for the selector."""
    only_traded = not include_idle
    groups = service.grouped_symbols(only_traded=only_traded)
    if family:
        groups = [g for g in groups if g["family"] == family]
    if asset_class:
        groups = [g for g in groups if g["key"] == asset_class]
        if not groups:
            raise HTTPException(status_code=400, detail=f"Unknown asset class '{asset_class}'.")

    # `groups` is narrowed by the filters above, so it cannot also describe the
    # asset-class picker: rebuilding the dropdown from it would leave a single
    # option and strand the user. `summary` is always the full rollup.
    summary = service.class_summary(only_traded=only_traded)
    if family:
        keep = {g["key"] for g in groups}
        summary = [c for c in summary if c["key"] in keep]
    flat = [s for g in groups for s in g["symbols"]]
    return {
        "groups": groups,
        "summary": summary,
        "symbols": flat,
        "total": len(flat),
        "default_symbol": service.default_symbol_id(only_traded=only_traded),
    }


@app.get("/api/analytics")
def analytics_report(
    symbol: str | None = Query(None, description="Symbol id; omit to analyse all"),
    timeframe: str = Query("1m"),
    window: int = Query(50, ge=2, le=5000),
    bins: int = Query(60, ge=10, le=300),
    limit: int = Query(50_000, ge=100, le=500_000),
    lookback_minutes: int | None = Query(
        None, ge=1,
        description="How much of the cached window to analyse; clamped to it.",
    ),
) -> dict:
    """Full analytics bundle for one symbol, from the cached window.

    The duration is clamped rather than rejected: the cache holds a fixed
    window, so asking for more returns everything there is. That keeps a
    bookmarked URL or a stale tab working instead of returning a 422.
    """
    requested = service.clamp_lookback(lookback_minutes)
    try:
        frame = service.load_ticks_cached(symbol, limit, requested)
    except DataSourceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if timeframe not in analytics.TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"Unknown timeframe '{timeframe}'.")

    report = analytics.build_report(frame, timeframe=timeframe, window=window, bins=bins)

    inst = service.lookup(symbol) if symbol else None
    # The newest tick actually returned by this query, so the UI can show when
    # the data on screen arrived rather than when the page was served.
    latest_tick = None if frame.empty else pd.Timestamp(frame["ts"].max()).isoformat()
    meta = {
        "symbol": symbol or report["summary"].get("symbol"),
        "symbol_name": inst.display if inst else None,
        "symbol_description": inst.description if inst else None,
        "asset_class": inst.asset_class if inst else None,
        "asset_class_label": CLASS_LABELS.get(inst.asset_class, "Unclassified") if inst else None,
        "family": inst.family if inst else None,
        "rows_analysed": int(len(frame)),
        "lookback_minutes": requested,
        "latest_tick": latest_tick,
        "generated_at": pd.Timestamp.utcnow().isoformat(),
    }
    report["meta"] = meta
    return report


@app.get("/unavailable")
def unavailable() -> Any:
    """The branded holding page, always reachable.

    Served at its own path so it can be previewed (and linked to) while the
    dashboard is still up; `/` only swaps to it when MAINTENANCE_MODE is on.
    """
    return FileResponse(
        STATIC_DIR / "unavailable.html",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@app.get("/welcome")
def welcome() -> Any:
    """The front-facing explainer, always reachable.

    A separate page from the dashboard: it describes what the app is and what
    it measures, then links into `app.axiomanalytics.info`. Served at its own
    path so the `www` host -- and anyone previewing it -- renders the same
    content. It is a static asset and never touches the warehouse, so it stays
    up during maintenance mode.
    """
    return FileResponse(
        STATIC_DIR / "landing.html",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@app.get("/")
def index() -> Any:
    """The dashboard, or the holding page while maintenance mode is on.

    503 rather than 200 while unavailable: it is a temporary condition, and a
    non-2xx keeps proxies and uptime checks from caching the outage as healthy.
    """
    if get_settings().maintenance:
        return FileResponse(
            STATIC_DIR / "unavailable.html",
            status_code=503,
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


if STATIC_DIR.exists():
    # The dashboard is assembled from index.html plus app.js. They reference
    # each other by element id, so a browser that pairs a fresh app.js with a
    # cached index.html renders controls the script cannot find. Revalidating
    # both on every load keeps the pair consistent; ETag/Last-Modified still
    # make the common case a cheap 304.
    class RevalidatingStatic(StaticFiles):
        """Static files that must be revalidated on every request."""

        def file_response(self, *args, **kwargs):
            resp = super().file_response(*args, **kwargs)
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
            return resp

    app.mount(
        "/static",
        RevalidatingStatic(directory=STATIC_DIR),
        name="static",
    )


@app.exception_handler(DataSourceError)
def _warehouse_handler(_request, exc: DataSourceError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})