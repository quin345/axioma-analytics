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


def _flatten(d: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten one level of toml tables ([risk] etc.); 'aliases' stays a dict."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, dict) and k != "aliases":
            out.update(v)
        else:
            out[k] = v
    return out


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
    if s.host != DEMO_HOST and not allow_live_host:
        raise ConfigError(f"host {s.host!r} refused: only {DEMO_HOST} is allowed")
    return s
