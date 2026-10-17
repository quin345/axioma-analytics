"""FastAPI application: static dashboard + JSON analytics API."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analytics, auth, metrics, refresh, service
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


@app.middleware("http")
async def gate_dashboard(request: Request, call_next):
    """Keep the dashboard and its data behind the login page.

    One check in one place: while ``LOGIN_PASSWORD`` is set, every request
    must carry a live session cookie, except the public paths listed in
    ``auth.is_public``. Pages bounce to ``/login`` so a bookmarked dashboard
    lands on the form; the JSON API answers ``401`` instead, which the
    dashboard's own script turns into the same bounce - a redirect would hand
    a fetch call an HTML body. Left open on purpose: the login flow, the
    health probe (no rows, and what uptime checks poll), the explainer and
    outage pages, the API docs (schema only) and the static assets.
    """
    s = get_settings()
    if not s.login_enabled or auth.is_public(request.url.path, s):
        return await call_next(request)
    if auth.session_valid(request.cookies.get(auth.COOKIE_NAME)):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Not authenticated."}, status_code=401)
    return RedirectResponse("/login", status_code=307)


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
        # The point-in-time controls, published rather than hard-coded so a
        # shorter cache window or a different grid reaches the UI unchanged:
        # a `timeline_minutes` long timeline in `timeline_step_minutes` steps,
        # and the fixed aggregate-window choices (the four `metrics_5m`
        # windows, 1 h / 2 h / 3 h / 4 h) offered at each of its points.
        "timeline_minutes": st.timeline_minutes,
        "timeline_step_minutes": st.timeline_step_minutes,
        "window_choices_minutes": st.window_choices_minutes,
        "window_min_minutes": st.window_min_minutes,
        "window_max_minutes": st.window_max_minutes,
        # The aggregate window the analysis runs over, bounded by what Redis
        # holds.
        "window_minutes": st.window_minutes or s.cache_lookback_minutes,
        # The raw cache window for anyone who needs it (refresh scripts, the
        # badge tooltip); the UI never asks for this much history.
        "lookback_minutes": st.lookback_minutes or s.cache_lookback_minutes,
        "cache_ttl_seconds": s.cache_ttl_seconds,
        # The cycle that keeps that window current: how often it runs, when it
        # last finished and how many symbols it replaced.
        "cache_refresh_minutes": s.cache_refresh_minutes,
        "cache_refresh_offset_seconds": s.cache_refresh_offset_seconds,
        "cache_refreshed_at": last.get("finished_at"),
        "cached_symbols": last.get("symbols"),
        # The precomputed grid pushed to Redis alongside the ticks, and the
        # freshness of the table it came from: the headline metrics are served
        # from here, so a stalled metrics job is visible in health rather than
        # showing up as a panel of nulls.
        "metrics_5m": st.metrics_5m,
        "metrics_table": s.metrics_table if st.metrics_5m else None,
        "metrics_stops": st.metrics_rows,
        "metrics_latest_as_of": st.metrics_latest,
        "cached_metrics_symbols": last.get("metrics_symbols"),
        "cached_metrics_rows": last.get("metrics_rows"),
        "has_data": bool(traded),
        "symbol_count": traded,
        "classified_count": classified,
        "unclassified_count": unclassified,
        "asset_class_count": classes,
        "timeframes": list(analytics.TIMEFRAMES),
        # The IANA zone the dashboard renders every wall-clock time in: the
        # clock widget, the "last update"/"latest tick"/"next refresh" badges
        # and the hourly activity profile. Published so one server setting
        # drives the whole UI instead of the browser assuming UTC.
        "display_timezone": s.display_timezone,
        # Whether the dashboard is behind the login gate. Published so the UI
        # shows the Sign-out control only on a gated server - an open one
        # must not offer a button that signs you out of nothing.
        "login_enabled": s.login_enabled,
    }


class LoginRequest(BaseModel):
    """The sign-in form's payload. Both fields are plain strings; a blank
    answer is simply a wrong one, answered by `auth.verify` like any other."""

    username: str = ""
    password: str = ""


@app.get("/login")
def login_page(request: Request) -> Any:
    """The sign-in page, or the dashboard for someone already signed in.

    Served at its own path so the gate has somewhere to bounce to; an
    authenticated visitor asking for it again is sent straight back to the
    dashboard, so the form only ever shows to someone who needs it. Like the
    other standalone pages it is a static file - no data source involved.
    """
    s = get_settings()
    if s.login_enabled and auth.session_valid(request.cookies.get(auth.COOKIE_NAME)):
        return RedirectResponse("/", status_code=307)
    return FileResponse(
        STATIC_DIR / "login.html",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@app.post("/api/login")
def login(payload: LoginRequest, response: Response) -> dict:
    """Exchange the form's credentials for a session cookie.

    The one account is checked in constant time (`auth.verify`), a wrong
    pair is a plain 401 whatever was wrong, and no cookie is set on failure.
    The cookie is HttpOnly and SameSite=Lax, so the dashboard's script never
    sees the token and a cross-site form post cannot ride it.
    """
    s = get_settings()
    if not s.login_enabled:
        raise HTTPException(
            status_code=403,
            detail="Login is disabled: no LOGIN_PASSWORD is configured.",
        )
    if not auth.verify(payload.username, payload.password, s):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    response.set_cookie(
        auth.COOKIE_NAME,
        auth.start_session(s),
        max_age=max(1, s.login_session_hours) * 3600,
        httponly=True,
        samesite="lax",
        secure=s.login_cookie_secure,
        path="/",
    )
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response) -> dict:
    """End the session and clear the cookie. Idempotent and public: the
    button should always work, and signing out twice is not an error."""
    s = get_settings()
    auth.end_session(request.cookies.get(auth.COOKIE_NAME))
    response.delete_cookie(
        auth.COOKIE_NAME, path="/", httponly=True, samesite="lax",
        secure=s.login_cookie_secure,
    )
    return {"ok": True}


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


@app.get("/api/metrics")
def metrics_report(
    symbol: str | None = Query(None, description="Symbol id; omit for every instrument"),
    window_minutes: int | None = Query(
        None, ge=1,
        description=(
            "Which trailing window to report: 60, 120, 180 or 240 minutes. A "
            "value the table does not precompute is served with the widest one "
            "it does that is no wider, and the resolution is reported."
        ),
    ),
    as_of: str | None = Query(
        None,
        description=(
            "Point in time to report, ISO 8601. Omitted - or ahead of the data - "
            "means the newest stop; anything older resolves to the newest stop at "
            "or before it."
        ),
    ),
) -> dict:
    """Precomputed volume-at-price ladders for one symbol, as a Redis lookup.

    The ladder is already binned in `metrics_5m` over the four fixed trailing
    windows, so this endpoint neither scans `agg_dom` nor re-bins in pandas: it
    picks the row for the chosen point in time and the `vap_*` column for the
    chosen window out of the grid the refresh cycle pushed to Redis. All four
    windows come back together, because they are four column sets on one row -
    the UI can switch window without a request.

    The headline scalar tiles are served by `/api/analytics`, not here: they are
    views of one tick frame, and `metrics_5m` snaps `as_of` to its 5-minute stop
    while the tick path ends at the instant asked for.

    The point in time is clamped rather than rejected, for the same reason as
    `/api/analytics`: the grid is a discrete set of stops, so a bookmarked URL or
    a stale tab resolves to the nearest usable stop instead of failing.
    """
    try:
        return metrics.report(symbol, window_minutes=window_minutes, as_of=as_of)
    except DataSourceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/analytics")
def analytics_report(
    symbol: str | None = Query(None, description="Symbol id; omit to analyse all"),
    timeframe: str = Query("1m"),
    window: int = Query(50, ge=2, le=5000),
    bins: int = Query(60, ge=10, le=300),
    limit: int = Query(50_000, ge=100, le=500_000),
    window_minutes: int | None = Query(
        None, ge=1,
        description="How many minutes the aggregate window runs for, ending at the chosen point in time; clamped to the cached window.",
    ),
    lookback_minutes: int | None = Query(
        None, ge=1,
        description="Deprecated alias for `window_minutes`; kept so bookmarked URLs keep working.",
    ),
    as_of: str | None = Query(
        None,
        description=(
            "Point in time to end at, ISO 8601. Omitted - or ahead of the data - "
            "means up to the newest row; anything older re-anchors the window "
            "on that point instead."
        ),
    ),
) -> dict:
    """Full analytics bundle for one symbol, from the cached window.

    Both the point in time and the aggregate window are clamped rather than
    rejected: the cache holds a fixed window, so asking for more than it has
    returns everything there is. That keeps a bookmarked URL or a stale tab
    working instead of returning a 422.
    """
    s = get_settings()
    selection = window_minutes if window_minutes is not None else lookback_minutes
    requested = service.clamp_window(selection)
    try:
        frame = service.load_ticks_cached(symbol, limit, requested, end_time=as_of)
    except DataSourceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if timeframe not in analytics.TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"Unknown timeframe '{timeframe}'.")

    report = analytics.build_report(
        frame, timeframe=timeframe, window=window, bins=bins, tz=s.display_timezone,
    )

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
        "window_minutes": requested,
        "latest_tick": latest_tick,
        # The point in time the window was anchored on, echoed so the UI can
        # confirm which point a report belongs to after the fact.
        "as_of": as_of,
        "generated_at": pd.Timestamp.utcnow().isoformat(),
    }
    report["meta"] = meta
    return report


@app.get("/unavailable")
def unavailable() -> Any:
    """The branded "temporarily unavailable" holding page, always reachable.

    Served at its own path so it can be previewed (and linked to) while the
    dashboard is still up; `/` only swaps to it when MAINTENANCE_MODE is on.
    It is kept for future outage windows even though the main page now shows
    the development notice - a real outage and a planned build are different
    messages, and this is the one for outages.
    """
    return FileResponse(
        STATIC_DIR / "maintenance.html",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@app.get("/dashboard")
def dashboard() -> Any:
    """The dashboard itself, retained at its own path.

    `/` serves this page now (the build notice held the root while the app was
    being rebuilt and is gone), but the path is kept so links and bookmarks
    formed during that period still resolve. It is the same file as `/`.
    """
    return FileResponse(
        STATIC_DIR / "index.html",
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
    """The dashboard, or the outage page while maintenance mode is on.

    The dashboard is the main page at last: `app.axiomanalytics.info/` serves
    it directly (`/dashboard` is only a retained alias). The bare domain and
    `www` are front doors of their own - nginx routes their roots to
    `/welcome`, the explainer - so this route never has to guess which page a
    hostname came for. MAINTENANCE_MODE keeps its original meaning: a real
    outage serves `maintenance.html` with a 503 rather than 200, so proxies and
    uptime checks do not cache the outage as healthy.
    """
    if get_settings().maintenance:
        return FileResponse(
            STATIC_DIR / "maintenance.html",
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