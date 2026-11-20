"""Configuration: secrets from .env, non-secret settings from config.toml (DESIGN.md sections 2 and 5)."""
from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

ROOT = Path(__file__).resolve().parent.parent
DEMO_HOST = "demo.ctraderapi.com"


class ConfigError(Exception):
    """Invalid or missing configuration."""


def parse_env(text: str) -> dict[str, str]:
    """Parse simple KEY=VALUE lines; '#' comments and blank lines skipped, matching quotes stripped."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, val = line.partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key.strip()] = val
    return out


class Secrets:
    """Credentials. repr/str never show values."""
    __slots__ = ("_client_id", "_client_secret", "_access_token", "_refresh_token", "_account_id")

    def __init__(self, client_id: str, client_secret: str, access_token: str,
                 account_id: int, refresh_token: Optional[str] = None):
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._account_id = account_id

    client_id = property(lambda self: self._client_id)
    client_secret = property(lambda self: self._client_secret)
    access_token = property(lambda self: self._access_token)
    refresh_token = property(lambda self: self._refresh_token)
    account_id = property(lambda self: self._account_id)

    def __repr__(self) -> str:
        rt = "***" if self._refresh_token else "None"
        return (f"Secrets(clientID=***, clientSecret=***, access_token=***, "
                f"refresh_token={rt}, account_id=***)")

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("Secrets is not picklable")


def _load_secrets(env: Mapping[str, str]) -> Secrets:
    missing = [k for k in ("clientID", "clientSecret", "access_token") if not env.get(k)]
    a = env.get("ctidTradeAccountId") or None
    b = env.get("ctidTraderAccountId") or None
    if a and b and a != b:
        raise ConfigError("ctidTradeAccountId and ctidTraderAccountId are both set and differ")
    acct = a or b
    if not acct:
        missing.append("ctidTradeAccountId|ctidTraderAccountId")
    if missing:
        raise ConfigError("missing required .env names: " + ", ".join(missing))
    try:
        acct_id = int(acct)
    except ValueError:
        raise ConfigError("account id must be an integer") from None
    return Secrets(env["clientID"], env["clientSecret"], env["access_token"], acct_id,
                   env.get("refresh_token") or None)


@dataclass
class Settings:
    """Non-secret settings; defaults are the frozen DESIGN.md values."""
    symbols: list[str] = field(default_factory=lambda: ["XAUUSD", "BTCUSD"])
    aliases: dict[str, list[str]] = field(default_factory=lambda: {
        "XAUUSD": ["XAUUSD.pro", "GOLD"],
        "BTCUSD": ["BTCUSD.pro", "BITCOIN"],
    })
    host: str = DEMO_HOST
    port: int = 5035
    entry_z: float = 1.0
    vol_shrink_z: float = 2.0
    confirm_range: str = "off"
    exec: str = "taker"
    limit_ttl_s: int = 120
    rollover_same_side: bool = True
    exit_mode: str = "taker"
    stop_k: float = 4.0
    base_size_x_min: int = 2
    max_positions: int = 2
    max_trades_day: int = 200
    max_orders_min: int = 10
    daily_loss_pct: float = 1.0
    daily_loss_bps: float = 150
    max_consec_losses: int = 15
    pause_after_losses_min: int = 60
    no_entry_before_close_min: int = 10
    flat_before_close_min: int = 2
    spread_guard_x_median: float = 2.0
    stale_s: int = 30
    reject_breaker_count: int = 3
    reject_breaker_window_min: int = 10
    flatten_after_disconnect_s: int = 120
    close_on_shutdown: bool = True
    daily_reset: str = "00:00"       # HH:MM local time in daily_reset_tz: where every "day" starts
    daily_reset_tz: str = "UTC"      # IANA name, e.g. America/New_York with 17:00 for the forex day
    # [kill] P&L kill switch (DESIGN.md section 5); off unless config.toml enables it
    kill_enabled: bool = False
    kill_max_loss: float = 500       # account currency; cumulative P&L since the baseline <= -this: KILL
    kill_profit_target: float = 1000  # cumulative P&L >= this: kill_profit_action; 0 = off
    kill_profit_action: str = "kill"  # "kill" (flatten, KILL file, stop) or "pause" (PAUSE file, no new entries)
    kill_daily_max_loss: float = 200  # today's P&L <= -this: flatten, no entries until the next reset; 0 = off
    kill_check_interval_s: float = 5
    kill_since: Optional[str] = None  # ISO UTC instant; overrides state/kill_baseline.json
    # [data] market-data source for bars (DESIGN.md section 4); keys get a data_ prefix
    data_source: str = "kql"         # "kql": 10 s buckets from Fabric KQL agg_dom; "ctrader": own depth feed
    data_env_file: str = "../.env"   # KUSTO_* / AZURE_* are read from here (process env wins); relative to ROOT
    data_grace_s: float = 3.0        # first KQL query this long after each slot end
    data_retry_s: float = 2.0        # pause between attempts while KQL has not caught up with the slot end
    data_retries: int = 3            # extra attempts after the first
    data_deadline_s: float = 10.0    # stop waiting for the slot's data this long after the slot end; must stay
                                     # below grace_s + execution.EXIT_BACKSTOP_S (the order manager's own exit)
    data_timeout_s: float = 10.0     # per-query timeout
    data_freshness_s: float = 15.0   # data up to slot_end - this is still tradable when KQL lags
    data_gap_max_s: float = 60.0     # longer stretch without a bucket in an open session = gap (no entries)
    label: str = "axq-poc4h-v1"
    grace_s: int = 2
    warmup_hours: int = 9
    state_dir: str = "state"
    log_dir: str = "logs"
    allow_live_host: bool = False
    secrets: Optional[Secrets] = field(default=None, repr=False)

    def state_path(self) -> Path:
        p = Path(self.state_dir)
        return p if p.is_absolute() else ROOT / p

    def log_path(self) -> Path:
        p = Path(self.log_dir)
        return p if p.is_absolute() else ROOT / p

    def data_env_path(self) -> Path:
        p = Path(self.data_env_file)
        return p if p.is_absolute() else (ROOT / p).resolve()


def _flatten(d: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten one level of toml tables ([risk] etc.); 'aliases' stays a dict; [kill] and [data] keys get a
    kill_ / data_ prefix."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, dict) and k in ("kill", "data"):
            out.update({f"{k}_{kk}": vv for kk, vv in v.items()})
        elif isinstance(v, dict) and k != "aliases":
            out.update(v)
        else:
            out[k] = v
    return out


def _num(name: str, v: Any, positive: bool = False) -> None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        raise ConfigError(f"{name} must be a number, got {v!r}")
    if v < 0 or (positive and v == 0):
        raise ConfigError(f"{name} must be {'> 0' if positive else '>= 0'} (a positive loss/profit size), got {v!r}")


def validate(s: Settings) -> None:
    """Reject bad daily-reset, kill-switch and data-source settings at start-up."""
    from .resetday import parse_reset
    try:
        parse_reset(s.daily_reset, s.daily_reset_tz)
    except ValueError as exc:
        raise ConfigError(str(exc)) from None
    if not isinstance(s.kill_enabled, bool):
        raise ConfigError(f"kill.enabled must be true or false, got {s.kill_enabled!r}")
    _num("kill.max_loss", s.kill_max_loss, positive=True)
    _num("kill.profit_target", s.kill_profit_target)
    _num("kill.daily_max_loss", s.kill_daily_max_loss)
    _num("kill.check_interval_s", s.kill_check_interval_s)
    if s.kill_check_interval_s < 1:
        raise ConfigError("kill.check_interval_s must be >= 1 (it also paces the server P&L request)")
    if s.kill_profit_action not in ("kill", "pause"):
        raise ConfigError(f"kill.profit_action must be 'kill' or 'pause', got {s.kill_profit_action!r}")
    if s.kill_since is not None:
        from .killswitch import parse_since
        parse_since(s.kill_since)
    if s.data_source not in ("kql", "ctrader"):
        raise ConfigError(f"data.source must be 'kql' or 'ctrader', got {s.data_source!r}")
    for name in ("grace_s", "retry_s", "deadline_s", "timeout_s", "freshness_s", "gap_max_s"):
        v = getattr(s, f"data_{name}")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v < float("inf") \
                or (v == 0 and name != "grace_s"):
            raise ConfigError(f"data.{name} must be a {'non-negative' if name == 'grace_s' else 'positive'} "
                              f"number of seconds, got {v!r}")
    if isinstance(s.data_retries, bool) or not isinstance(s.data_retries, int) or s.data_retries < 0:
        raise ConfigError(f"data.retries must be an integer >= 0, got {s.data_retries!r}")
    if s.data_grace_s >= s.data_deadline_s:
        raise ConfigError("data.grace_s must be below data.deadline_s")
    from .execution import EXIT_BACKSTOP_S
    if s.data_deadline_s >= s.grace_s + EXIT_BACKSTOP_S:
        # past it the order manager has already time-exited a position the strategy might roll over
        raise ConfigError(f"data.deadline_s must be below strategy grace_s + {EXIT_BACKSTOP_S:g} "
                          f"(the order manager's exit backstop), got {s.data_deadline_s!r}")


def load(env_path=None, toml_path=None, overrides: Optional[Mapping[str, Any]] = None,
         allow_live_host: bool = False) -> Settings:
    """Load secrets and settings.

    A host other than demo.ctraderapi.com is refused unless allow_live_host=True. That flag
    exists only to permit dry-run against another host; real orders are still refused elsewhere
    (session handshake requires isLive == false and DemoBroker asserts verified_demo).
    """
    env_path = Path(env_path) if env_path else ROOT / ".env"
    toml_path = Path(toml_path) if toml_path else ROOT / "config.toml"
    if not env_path.is_file():
        raise ConfigError(f"env file not found: {env_path.name}")
    secrets = _load_secrets(parse_env(env_path.read_text()))

    data: dict[str, Any] = {}
    if toml_path.is_file():
        with open(toml_path, "rb") as fh:
            data = _flatten(tomllib.load(fh))
    data.update(_flatten(dict(overrides or {})))
    if "reject_breaker" in data:  # e.g. [3, 10] = 3 rejects in 10 min
        data["reject_breaker_count"], data["reject_breaker_window_min"] = data.pop("reject_breaker")
    known = {f.name for f in dataclasses.fields(Settings)} - {"secrets"}
    unknown = set(data) - known
    if unknown:
        raise ConfigError("unknown settings: " + ", ".join(sorted(unknown)))
    data.pop("allow_live_host", None)
    s = Settings(**data, allow_live_host=allow_live_host, secrets=secrets)
    validate(s)
    if s.host != DEMO_HOST and not allow_live_host:
        raise ConfigError(f"host {s.host!r} refused: only {DEMO_HOST} is allowed")
    return s
