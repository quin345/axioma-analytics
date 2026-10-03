"""Entra ID auth for the SQL data source endpoint.

Two credential modes are supported:

1. `az login` session  -> an AAD access token for resource https://database.windows.net
   is minted via the Azure CLI and passed to the driver as
   Authentication=ActiveDirectoryAccessToken. Tokens are cached until shortly
   before expiry, then refreshed automatically.
2. Service principal -> FABRIC_TENANT_ID / FABRIC_CLIENT_ID / FABRIC_CLIENT_SECRET,
   using Authentication=ActiveDirectoryServicePrincipal.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass

SQL_RESOURCE = "https://database.windows.net/"
_REFRESH_MARGIN = 300  # refresh 5 minutes before expiry


@dataclass
class AccessToken:
    value: str
    expires_at: float

    @property
    def valid(self) -> bool:
        return bool(self.value) and time.time() < self.expires_at - _REFRESH_MARGIN


class TokenError(RuntimeError):
    pass


class CliTokenProvider:
    """Mints access tokens from an active `az login` session."""

    def __init__(self) -> None:
        self._token: AccessToken | None = None
        self._lock = threading.Lock()

    def _run(self) -> dict:
        exe = shutil.which("az")
        if not exe:
            raise TokenError(
                "Azure CLI not found on PATH. Install it, run `az login`, "
                "or set FABRIC_CLIENT_ID / FABRIC_CLIENT_SECRET instead."
            )
        proc = subprocess.run(
            [exe, "account", "get-access-token", "--resource", SQL_RESOURCE, "--output", "json"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            raise TokenError(f"`az account get-access-token` failed: {proc.stderr.strip() or proc.stdout.strip()}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise TokenError(f"Could not parse Azure CLI token response: {exc}") from exc

    def get(self) -> AccessToken:
        with self._lock:
            if self._token and self._token.valid:
                return self._token
            data = self._run()
            raw = data.get("accessToken") or data.get("access_token")
            if not raw:
                raise TokenError("Azure CLI returned no access token; run `az login` first.")
            expires = float(data.get("expires_on") or (time.time() + 3300))
            self._token = AccessToken(value=raw, expires_at=expires)
            return self._token


_cli_provider = CliTokenProvider()


def get_access_token() -> AccessToken | None:
    """Return a service-principal token if configured, else fall back to `az login`."""
    from .config import get_settings

    s = get_settings()
    if s.has_credentials:
        # Handled directly in the connection string (no token minting needed).
        return None
    return _cli_provider.get()