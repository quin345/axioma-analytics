"""Redis cache for the KQL query results.

The dashboard never queries KQL without first looking here. Each distinct query
- the per-symbol aggregate rows and the instrument catalogue - is stored under a
key derived from the KQL database/table and the query's parameters, with a
fixed TTL. The result: repeated duration/timeframe selections reuse one KQL
read instead of re-scanning ``agg_dom`` every time.

The endpoint is Redis Enterprise with **Entra ID authentication only** - there
is no access key. The token is minted for the ``https://redis.azure.com/`` scope
by the same identity that authenticates KQL (on an Azure host, the machine's
managed identity) and is handed to redis-py through its
:class:`~redis.credentials.CredentialProvider` hook, so it is refreshed on every
new connection. Redis requires the *object id* of the principal as the username,
which is read from the token's own ``oid`` claim rather than configured by hand.

Frames travel as Parquet bytes. The aggregate rows are timestamped and mostly
floats, which Parquet stores compactly, restores with their dtypes intact and
keeps nulls lossless for the reader.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import threading
from typing import Any

import pandas as pd
import redis
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import (AzureCliCredential, ClientSecretCredential,
                            ManagedIdentityCredential)
from redis.credentials import CredentialProvider

from .config import Settings, get_settings
from .errors import DataSourceError

log = logging.getLogger(__name__)

#: The Entra scope the Redis data-plane accepts.
REDIS_SCOPE = "https://redis.azure.com/.default"


def _credential(settings: Settings):
    """An `azure-identity` credential matching the configured auth mode.

    Managed identity wins over any secret in the environment, exactly as on the
    KQL side, so a leftover service-principal secret cannot silently change the
    auth path.
    """
    if settings.use_managed_identity:
        if settings.managed_identity_client_id:
            return ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
        return ManagedIdentityCredential()
    if settings.has_credentials:
        return ClientSecretCredential(
            tenant_id=settings.tenant_id,
            client_id=settings.client_id,
            client_secret=settings.client_secret,
        )
    return AzureCliCredential()


def object_id(token: str) -> str:
    """The ``oid`` claim of a JWT, which Redis uses as the AUTH username."""
    try:
        payload = token.split(".")[1]
    except IndexError as exc:
        raise DataSourceError("Redis token is not a JWT.") from exc
    payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
    oid = claims.get("oid") or claims.get("sub") or ""
    if not oid:
        raise DataSourceError(
            "The Redis token carries no object id; Redis cannot authenticate the principal."
        )
    return str(oid)


class EntraCredentialProvider(CredentialProvider):
    """Feeds redis-py a fresh `(object id, token)` pair per connection.

    redis-py calls :meth:`get_credentials` whenever it opens a socket, so the
    token is refreshed as the pool grows instead of being pinned at startup.
    `azure-identity` caches the token internally, so a repeated call within its
    lifetime is cheap.
    """

    def __init__(self, credential) -> None:
        self._credential = credential

    def get_credentials(self) -> tuple[str, str]:
        token = self._credential.get_token(REDIS_SCOPE).token
        return object_id(token), token


_clients: dict[tuple, redis.Redis] = {}
_clients_lock = threading.Lock()


def client(settings: Settings | None = None) -> redis.Redis:
    """A pooled Redis client for the configured cache endpoint."""
    s = settings or get_settings()
    if not s.redis_configured:
        raise DataSourceError(
            "The Redis cache endpoint is not configured. Set REDIS_HOST in .env."
        )
    ident = (s.redis_host, s.redis_port, s.redis_db, s.redis_ssl)
    with _clients_lock:
        r = _clients.get(ident)
        if r is None:
            r = redis.Redis(
                host=s.redis_host,
                port=s.redis_port,
                db=s.redis_db,
                ssl=s.redis_ssl,
                credential_provider=EntraCredentialProvider(_credential(s)),
                socket_connect_timeout=s.redis_timeout,
                socket_timeout=s.redis_timeout,
                health_check_interval=30,
            )
            _clients[ident] = r
        return r


def ping(settings: Settings | None = None) -> bool:
    """True when the cache answers; False (never raised) when it does not."""
    try:
        return bool(client(settings).ping())
    except (redis.RedisError, DataSourceError, ClientAuthenticationError) as exc:
        log.warning("Redis cache unavailable: %s", exc)
        return False


def key(settings: Settings | None = None, *parts: object) -> str:
    """Namespaced cache key: prefix, KQL object, then the query's parameters."""
    s = settings or get_settings()
    return ":".join([s.cache_key_prefix, s.kql_database, s.kql_table,
                     *(str(p) for p in parts)])


def _get(k: str, settings: Settings | None) -> bytes | None:
    try:
        return client(settings).get(k)
    except (redis.RedisError, DataSourceError, ClientAuthenticationError) as exc:
        log.warning("Redis read failed for %s: %s", k, exc)
        return None


def _set(k: str, value: bytes, ttl: int, settings: Settings | None) -> bool:
    try:
        client(settings).set(k, value, ex=int(ttl))
        return True
    except (redis.RedisError, DataSourceError, ClientAuthenticationError) as exc:
        log.warning("Redis write failed for %s: %s", k, exc)
        return False


def get_frame(k: str, settings: Settings | None = None) -> pd.DataFrame | None:
    """The cached frame under `k`, or None on a miss or a cache failure."""
    raw = _get(k, settings)
    if raw is None:
        return None
    try:
        return pd.read_parquet(io.BytesIO(raw))
    except Exception as exc:  # corrupt/foreign payload: treat as a miss
        log.warning("Discarding unreadable cache entry %s: %s", k, exc)
        return None


def set_frame(k: str, frame: pd.DataFrame, ttl: int, settings: Settings | None = None) -> bool:
    """Store a frame under `k` for `ttl` seconds."""
    buf = io.BytesIO()
    try:
        frame.to_parquet(buf, index=False)
    except Exception as exc:  # pragma: no cover - a frame we cannot encode
        log.warning("Could not encode frame for %s: %s", k, exc)
        return False
    return _set(k, buf.getvalue(), ttl, settings)


def get_json(k: str, settings: Settings | None = None) -> Any | None:
    """The cached JSON document under `k`, or None on a miss/failure."""
    raw = _get(k, settings)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError as exc:
        log.warning("Discarding unreadable cache entry %s: %s", k, exc)
        return None


def set_json(k: str, value: Any, ttl: int, settings: Settings | None = None) -> bool:
    """Store a JSON document under `k` for `ttl` seconds."""
    return _set(k, json.dumps(value, default=str).encode("utf-8"), ttl, settings)


def delete(settings: Settings | None = None, *keys: str) -> None:
    """Drop cache entries; failures are logged, never raised."""
    if not keys:
        return
    try:
        client(settings).delete(*keys)
    except (redis.RedisError, DataSourceError, ClientAuthenticationError) as exc:
        log.warning("Redis delete failed: %s", exc)


def reset() -> None:
    """Forget the pooled clients (used by tests and on reconfiguration)."""
    with _clients_lock:
        _clients.clear()
