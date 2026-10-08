#!/usr/bin/env python
"""Grant the Axioma service principal a Fabric workspace role on named workspaces.

Fabric workspace roles are **not** Azure RBAC -- ``az role assignment`` does not
apply, and there is no ``az fabric`` extension. Access is granted through the
Fabric REST API::

    POST /v1/workspaces/{workspaceId}/roleAssignments
    { "principal": { "id": <sp-object-id>, "type": "ServicePrincipal" },
      "role": "Contributor" }

The app's read path needs the service principal to hold a role on **every**
workspace it reads from, so each of dev_axioma / test_axioma / prod_axioma must
carry the assignment. That role covers the Eventhouse KQL database
(`ctrader_dom`) the dashboard reads from. Redis is separate: grant the same
identity *Redis Data Contributor* (or a data-access policy on the key prefix)
wherever the cache lives, and if KQL reads return
``UnauthorizedDatabaseAccessException`` (403), the database carries a stricter
database-level permission - add the identity under the Eventhouse database's
*Manage permissions*. This script only manages workspace roles; it cannot grant
those. It is idempotent: it skips a workspace that already holds the role,
updates the role when it differs, and only creates the assignment when missing.

The caller must be signed in with ``az login`` and hold Member or higher on the
target workspaces (Admin is required to grant, in practice).

Usage::

    python scripts/grant_workspace_access.py                     # dev/test/prod -> Contributor
    python scripts/grant_workspace_access.py --role Viewer
    python scripts/grant_workspace_access.py --workspaces dev_axioma --workspaces test_axioma
    python scripts/grant_workspace_access.py --dry-run
    python scripts/grant_workspace_access.py --list
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover - dotenv is a declared dependency
    dotenv_values = None

FABRIC_RESOURCE = "https://api.fabric.microsoft.com"
GRAPH_RESOURCE = "https://graph.microsoft.com"
FABRIC_API = f"{FABRIC_RESOURCE}/v1"
GRAPH_API = f"{GRAPH_RESOURCE}/v1.0"

DEFAULT_WORKSPACES = ("dev_axioma", "test_axioma", "prod_axioma")
VALID_ROLES = ("Admin", "Member", "Contributor", "Viewer")
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


def _token(resource: str) -> str:
    """Mint an access token for a resource from the active ``az login`` session."""
    exe = shutil.which("az")
    if not exe:
        raise SystemExit("Azure CLI (`az`) not found on PATH. Install it and run `az login`.")
    proc = subprocess.run(
        [exe, "account", "get-access-token", "--resource", resource, "--output", "json"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(
            "Could not get a token from the Azure CLI. Run `az login` first.\n"
            f"{(proc.stderr or proc.stdout).strip()}"
        )
    return json.loads(proc.stdout)["accessToken"]


def _request(method: str, url: str, token: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode() or "{}"
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode() or ""
        try:
            detail = json.loads(raw)
            message = detail.get("message") or detail.get("errorCode") or raw
        except json.JSONDecodeError:
            message = raw or exc.reason
        raise ApiError(exc.code, message) from exc


def _client_id(cli_value: str | None) -> str | None:
    if cli_value:
        return cli_value
    if dotenv_values is not None and _ENV_FILE.exists():
        return dotenv_values(_ENV_FILE).get("FABRIC_CLIENT_ID")
    return None


def resolve_principal_id(token: str, app_id: str) -> str:
    """Object ID of the service principal for an app (client) ID."""
    query = urllib.parse.urlencode({"$filter": f"appId eq '{app_id}'"})
    _, data = _request("GET", f"{GRAPH_API}/servicePrincipals?{query}", token)
    values = data.get("value") or []
    if not values:
        raise SystemExit(f"No service principal found for appId {app_id}.")
    return values[0]["id"]


def list_workspaces(token: str) -> list[dict]:
    """All workspaces visible to the caller (paginated)."""
    out: list[dict] = []
    url: str | None = f"{FABRIC_API}/workspaces"
    while url:
        _, data = _request("GET", url, token)
        out.extend(data.get("value") or [])
        cont = data.get("continuationToken")
        url = f"{FABRIC_API}/workspaces?continuationToken={urllib.parse.quote(cont)}" if cont else None
    return out


def role_assignments(token: str, workspace_id: str) -> list[dict]:
    _, data = _request("GET", f"{FABRIC_API}/workspaces/{workspace_id}/roleAssignments", token)
    return data.get("value") or []


def ensure_role(token: str, workspace: dict, principal_id: str, role: str, dry_run: bool) -> str:
    """Create/update/skip the role assignment. Returns an action label."""
    ws_id = workspace["id"]
    existing = next(
        (a for a in role_assignments(token, ws_id) if a["principal"]["id"] == principal_id), None
    )
    if existing and existing.get("role") == role:
        return "unchanged"
    if dry_run:
        return "would-update" if existing else "would-create"
    if existing:
        _request(
            "PUT",
            f"{FABRIC_API}/workspaces/{ws_id}/roleAssignments/{existing['id']}",
            token,
            {"role": role},
        )
        return f"updated ({existing.get('role')} -> {role})"
    _request(
        "POST",
        f"{FABRIC_API}/workspaces/{ws_id}/roleAssignments",
        token,
        {"principal": {"id": principal_id, "type": "ServicePrincipal"}, "role": role},
    )
    return "created"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Grant a service principal a Fabric workspace role.")
    ap.add_argument("--workspaces", action="append", metavar="NAME",
                    help="workspace display name (repeatable); default dev/test/prod_axioma")
    ap.add_argument("--role", default="Contributor", choices=VALID_ROLES,
                    help="workspace role to grant (default: Contributor)")
    ap.add_argument("--client-id", help="app (client) ID of the service principal; "
                                        "defaults to FABRIC_CLIENT_ID in .env")
    ap.add_argument("--principal-id", help="service principal object ID (skips Graph lookup)")
    ap.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    ap.add_argument("--list", action="store_true", help="list workspaces and exit")
    args = ap.parse_args(argv)

    fabric = _token(FABRIC_RESOURCE)
    workspaces = list_workspaces(fabric)

    if args.list:
        print("\n  Workspaces visible to this login:")
        for ws in sorted(workspaces, key=lambda w: w["displayName"].lower()):
            print(f"   {ws['displayName']:<40} {ws['id']}")
        print()
        return 0

    if args.principal_id:
        principal_id = args.principal_id
    else:
        app_id = _client_id(args.client_id)
        if not app_id:
            raise SystemExit("Provide --client-id / --principal-id, or set FABRIC_CLIENT_ID in .env.")
        principal_id = resolve_principal_id(_token(GRAPH_RESOURCE), app_id)

    wanted = args.workspaces or list(DEFAULT_WORKSPACES)
    by_name = {w["displayName"].lower(): w for w in workspaces}

    print(f"\n  Service principal : {principal_id}")
    print(f"  Role              : {args.role}{'  (dry run)' if args.dry_run else ''}\n")

    failures = 0
    for name in wanted:
        ws = by_name.get(name.lower())
        if ws is None:
            print(f"   {name:<22} NOT FOUND (not visible to this login)")
            failures += 1
            continue
        try:
            action = ensure_role(fabric, ws, principal_id, args.role, args.dry_run)
        except ApiError as exc:
            print(f"   {name:<22} FAILED  {exc}")
            failures += 1
            continue
        print(f"   {name:<22} {action}")

    print()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
