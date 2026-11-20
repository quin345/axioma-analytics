"""Market data from the Fabric KQL table agg_dom (DESIGN.md section 4, [data] source = "kql").

agg_dom holds one row per depth event, written by the ctrader-collector@ units with the stamping
rule of book.Stamper (timestamp = the latest spot timestamp). The bot asks KQL for 10 s buckets
of its own streams, keyed by (ctidTraderAccountId, symbolId), with the semantics of
bars.Bucketiser and webapp queries.series_range (webapp/queries.py:154-166): per bin the last
book state and the event count.

Last book state: the row with the greatest timestamp; ties go to the greatest eventSeq, which is
the Bucketiser's "latest stamp wins, ties to the later row". Plain arg_max(timestamp, ...) (the
webapp query) picks an arbitrary row among ties, which happens in roughly 1% of buckets with
different books. The tie key is a fixed-width string "<19-digit ms>|<19-digit eventSeq>", so
string order is (timestamp, eventSeq) order whatever eventSeq starts at.

One query returns two row kinds:
  row_kind "b": symbolId, ms (bucket start, epoch ms), best_bid, best_ask, events  - for [from, to)
  row_kind "w": symbolId, ms = max(timestamp) of the stream from `from - WATERMARK_LOOKBACK` on, with no
            upper bound (the watermark: how far KQL has data for the stream).

Rows with timestamp >= `to` are left out of the buckets but count for the watermark: a row stamped
at or after the slot end proves that every row stamped before it is in (one Event Hub partition per
stream keeps them in order). Prices are the raw integers (1/100000), as the bot uses throughout.

Network I/O (azure-kusto-data, blocking) runs in the reactor thread pool; tests inject a reader and
a synchronous `defer_fn`. Nothing here logs a credential.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

from twisted.internet import defer

from .types import Bucket

log = logging.getLogger(__name__)

BUCKET_MS = 10_000
WATERMARK_LOOKBACK_DAYS = 7      # the watermark survives a weekend / long break
SEQ_PAD = 10 ** 18               # + this makes any 0 <= n < 8.2e18 a 19-digit string
MARKER = "AUTOTRADE_BUCKETS"
_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENV_KEYS = ("KUSTO_CLUSTER_URI", "KUSTO_DATABASE", "KUSTO_TABLE",
            "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")


class KqlError(Exception):
    """KQL configuration or query failure (never carries a credential)."""


# ---- query -------------------------------------------------------------------------------------

def _kdt(ms: int) -> str:
    """KQL datetime literal for epoch ms (ms precision, UTC)."""
    d = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return f"datetime({d.strftime('%Y-%m-%dT%H:%M:%S')}.{ms % 1000:03d}Z)"


@dataclass(frozen=True)
class KqlRequest:
    """Buckets of `symbol_ids` (account `account`) with start in [from_ms, to_ms), plus watermarks."""
    table: str
    account: str
    symbol_ids: tuple[int, ...]
    from_ms: int
    to_ms: int

    def query(self) -> str:
        if not _TABLE_RE.match(self.table):
            raise KqlError("KUSTO_TABLE must be a plain table name")
        if not str(self.account).isdigit() or not self.symbol_ids:
            raise KqlError("account and symbol ids must be digits")
        ids = ", ".join(f"'{int(s)}'" for s in self.symbol_ids)
        return f"""// {MARKER}
let _from = {_kdt(self.from_ms)};
let _to = {_kdt(self.to_ms)};
let _epoch = datetime(1970-01-01);
let _rows = {self.table}
| where ctidTraderAccountId == '{int(self.account)}' and symbolId in ({ids})
    and timestamp >= _from - {WATERMARK_LOOKBACK_DAYS}d;
union
(_rows
| where timestamp >= _from and timestamp < _to
| extend _k = strcat(tostring(datetime_diff('millisecond', timestamp, _epoch) + {SEQ_PAD}), '|',
                     tostring(coalesce(eventSeq, 0) + {SEQ_PAD}))
| summarize arg_max(_k, best_bid, best_ask), events = count() by symbolId, bucket = bin(timestamp, 10s)
| project row_kind = 'b', symbolId, ms = datetime_diff('millisecond', bucket, _epoch), best_bid, best_ask, events),
(_rows
| summarize wm = max(timestamp) by symbolId
| project row_kind = 'w', symbolId, ms = datetime_diff('millisecond', wm, _epoch), best_bid = real(null),
    best_ask = real(null), events = long(null))
| order by row_kind asc, symbolId asc, ms asc"""


# ---- result ------------------------------------------------------------------------------------

def _price(v) -> Optional[int]:
    if v is None:
        return None
    f = float(v)
    if f != f:          # NaN from a null real
        return None
    return int(f) if f.is_integer() else f  # prices are integers; keep a fraction rather than truncate


@dataclass
class KqlFetch:
    """Parsed result: buckets (source "kql", time ordered) and the watermark per symbol."""
    request: KqlRequest
    buckets: dict[int, list[Bucket]] = field(default_factory=dict)
    watermark: dict[int, Optional[int]] = field(default_factory=dict)

    def all_buckets(self) -> list[Bucket]:
        return [b for sid in sorted(self.buckets) for b in self.buckets[sid]]


def parse_result(request: KqlRequest, rows: Iterable[Mapping]) -> KqlFetch:
    out = KqlFetch(request, {s: [] for s in request.symbol_ids}, {s: None for s in request.symbol_ids})
    for r in rows:
        sid = int(r["symbolId"])
        if sid not in out.buckets:
            continue
        if r["row_kind"] == "w":
            out.watermark[sid] = None if r["ms"] is None else int(r["ms"])
        elif r["row_kind"] == "b":
            out.buckets[sid].append(Bucket(sid, int(r["ms"]), _price(r["best_bid"]), _price(r["best_ask"]),
                                           int(r["events"]), "kql"))
    for sid in out.buckets:
        out.buckets[sid].sort(key=lambda b: b.start_ms)
    return out


def reference_rows(request: KqlRequest, events: Iterable[Mapping]) -> list[dict]:
    """What the query returns, computed in Python (tests, smoke check, fake readers).

    events: dicts with symbol_id, ts_ms, seq, best_bid, best_ask (one per agg_dom row of the account).
    """
    lo_wm = request.from_ms - WATERMARK_LOOKBACK_DAYS * 86_400_000
    ids = set(request.symbol_ids)
    held: dict[tuple[int, int], tuple] = {}
    counts: dict[tuple[int, int], int] = {}
    wm: dict[int, int] = {}
    for e in events:
        sid, ts = int(e["symbol_id"]), int(e["ts_ms"])
        if sid not in ids or ts < lo_wm:
            continue
        wm[sid] = max(wm.get(sid, ts), ts)
        if not request.from_ms <= ts < request.to_ms:
            continue
        key = (sid, ts - ts % BUCKET_MS)
        k = (ts, int(e.get("seq") or 0))
        counts[key] = counts.get(key, 0) + 1
        if key not in held or k > held[key][0]:
            held[key] = (k, e.get("best_bid"), e.get("best_ask"))
    rows = [{"row_kind": "b", "symbolId": str(sid), "ms": start, "best_bid": None if bb is None else float(bb),
             "best_ask": None if ba is None else float(ba), "events": counts[(sid, start)]}
            for (sid, start), (_, bb, ba) in held.items()]
    rows += [{"row_kind": "w", "symbolId": str(sid), "ms": m, "best_bid": None, "best_ask": None, "events": None}
             for sid, m in wm.items()]
    return sorted(rows, key=lambda r: (r["row_kind"], r["symbolId"], r["ms"]))


# ---- gating ------------------------------------------------------------------------------------

def stream_state(watermark_ms: Optional[int], slot_end_ms: int, freshness_ms: float,
                 last_spot_ms: Optional[int] = None) -> str:
    """"complete" | "fresh" | "stale" for one stream at a slot close.

    complete: KQL has a row stamped at/after the slot end (so every row before it is in), or, when
              the market is quiet, it has caught up with the latest spot timestamp the bot itself has
              seen (rows are stamped with the latest spot timestamp, so none can be newer). The caller
              passes last_spot_ms only while its own spot feed is connected.
    fresh:    KQL lags, but by at most freshness_ms: tradable (the slot's last seconds may miss).
    stale:    anything older, or no row at all.
    """
    if watermark_ms is None:
        return "stale"
    if watermark_ms >= slot_end_ms or (last_spot_ms is not None and watermark_ms >= last_spot_ms):
        return "complete"
    if watermark_ms >= slot_end_ms - freshness_ms:
        return "fresh"
    return "stale"


def window_gaps(starts: Iterable[int], start_ms: int, end_ms: int, max_gap_ms: float,
                is_open: Optional[Callable[[int], bool]] = None) -> list[tuple[int, int, int]]:
    """Stretches of [start_ms, end_ms) without a bucket whose open-session part exceeds max_gap_ms.

    Returns [(gap_from_ms, gap_to_ms, open_ms)]. Time is counted in 10 s steps; a step counts as open
    when is_open(step start) (None = always open), so the gold break or a weekend is not a gap. open_ms
    stops counting just past max_gap_ms (it is a lower bound for long gaps when is_open is given).
    """
    out = []
    cursor = start_ms
    for s in sorted(x for x in starts if start_ms - BUCKET_MS < x < end_ms) + [end_ms]:
        if s > cursor and s - cursor > max_gap_ms:
            if is_open is None:
                open_ms = s - cursor
            else:
                open_ms = 0
                t = cursor
                while t < s and open_ms <= max_gap_ms:     # enough once it is over the limit
                    step = min(BUCKET_MS, s - t)
                    if is_open(t):
                        open_ms += step
                    t += step
            if open_ms > max_gap_ms:
                out.append((cursor, s, open_ms))
        cursor = max(cursor, s + BUCKET_MS)
    return out


# ---- access ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class KqlConfig:
    cluster_uri: str
    database: str
    table: str
    creds: Optional[tuple[str, str, str]] = field(default=None, repr=False)  # tenant, client id, secret

    def __repr__(self) -> str:  # never show the credential
        return f"KqlConfig(database={self.database!r}, table={self.table!r}, creds={'***' if self.creds else None})"


def load_kql_config(env_file: Optional[Path], environ: Mapping[str, str] = os.environ) -> KqlConfig:
    """KUSTO_* and AZURE_* from env_file, overridden by the process environment. Other keys are ignored."""
    from .config import parse_env
    vals: dict[str, str] = {}
    if env_file is not None and Path(env_file).is_file():
        vals = {k: v for k, v in parse_env(Path(env_file).read_text()).items() if k in ENV_KEYS}
    vals.update({k: environ[k] for k in ENV_KEYS if environ.get(k)})
    missing = [k for k in ("KUSTO_CLUSTER_URI", "KUSTO_DATABASE") if not vals.get(k)]
    if missing:
        raise KqlError(f"missing {', '.join(missing)} (data.env_file or the environment)")
    table = vals.get("KUSTO_TABLE") or "agg_dom"
    if not _TABLE_RE.match(table):
        raise KqlError("KUSTO_TABLE must be a plain table name")
    creds = tuple(vals.get(k, "") for k in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"))
    return KqlConfig(vals["KUSTO_CLUSTER_URI"], vals["KUSTO_DATABASE"], table,
                     creds if all(creds) else None)


class KustoReader:
    """Blocking reader (azure-kusto-data); a service principal from AZURE_* when given, else
    DefaultAzureCredential. Call from a worker thread."""

    def __init__(self, cfg: KqlConfig, timeout_s: float = 10.0):
        from datetime import timedelta

        from azure.kusto.data import ClientRequestProperties, KustoClient, KustoConnectionStringBuilder
        if cfg.creds:
            from azure.identity import ClientSecretCredential
            cred = ClientSecretCredential(*cfg.creds)
        else:
            from azure.identity import DefaultAzureCredential
            cred = DefaultAzureCredential()
        self._db = cfg.database
        self._client = KustoClient(KustoConnectionStringBuilder.with_azure_token_credential(cfg.cluster_uri, cred))
        self._props = ClientRequestProperties()
        self._props.set_option(ClientRequestProperties.request_timeout_option_name,
                               timedelta(seconds=max(5.0, timeout_s)))

    def run(self, query: str) -> list[dict]:
        res = self._client.execute(self._db, query, self._props)
        table = res.primary_results[0]
        cols = [c.column_name for c in table.columns]
        return [dict(zip(cols, row)) for row in table]

    def rows(self, request: KqlRequest) -> list[dict]:
        return self.run(request.query())


def _thread(fn, *args):
    from twisted.internet import threads
    return threads.deferToThread(fn, *args)


class KqlSource:
    """fetch() -> Deferred[KqlFetch]; failures (config, network, timeout) fail the Deferred.

    reader: object with rows(KqlRequest) -> list[dict] (KustoReader, or a fake in tests). Built lazily
    from `config_fn` on first use, so a missing package or bad config surfaces as a fetch failure.
    """

    def __init__(self, account: str, table: str = "agg_dom", reader=None, config_fn=None,
                 timeout_s: float = 10.0, reactor=None, defer_fn: Callable = _thread):
        self.account = str(account)
        self.table = table
        self._reader = reader
        self._config_fn = config_fn
        self.timeout_s = timeout_s
        self._reactor = reactor
        self._defer = defer_fn
        self.calls = 0

    @classmethod
    def from_settings(cls, settings, reactor=None) -> "KqlSource":
        acct = settings.secrets.account_id if settings.secrets else None
        if acct is None:
            raise KqlError("no account id")
        return cls(str(acct), config_fn=lambda: load_kql_config(settings.data_env_path()),
                   timeout_s=settings.data_timeout_s, reactor=reactor)

    def _ensure_reader(self):
        if self._reader is None:
            cfg = self._config_fn() if self._config_fn else load_kql_config(None)
            self.table = cfg.table
            self._reader = KustoReader(cfg, self.timeout_s)
        return self._reader

    def _blocking(self, symbol_ids: tuple, from_ms: int, to_ms: int) -> KqlFetch:
        reader = self._ensure_reader()
        req = KqlRequest(self.table, self.account, symbol_ids, from_ms, to_ms)
        return parse_result(req, reader.rows(req))

    def fetch(self, symbol_ids, from_ms: int, to_ms: int, timeout_s: Optional[float] = None) -> defer.Deferred:
        """Buckets with start in [from_ms, to_ms). from_ms is floored to a bucket boundary so the first
        bucket is whole (a partial one would overwrite a complete cached bucket)."""
        self.calls += 1
        from_ms = int(from_ms) - int(from_ms) % BUCKET_MS
        ids = tuple(sorted(int(s) for s in symbol_ids))
        d = defer.maybeDeferred(self._defer, self._blocking, ids, from_ms, int(to_ms))
        t = self.timeout_s if timeout_s is None else timeout_s
        if self._reactor is not None and t and not d.called:
            d.addTimeout(t, self._reactor)
        return d

