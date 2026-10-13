"""Relay MCP server: exposes Salesforce account tools to Claude over stdio.

Logs go to stderr. Never print() in this file: over stdio, stdout carries the
MCP protocol and stray text breaks the connection.
"""

from __future__ import annotations

import functools
import logging
import sys
from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from simple_salesforce.exceptions import SalesforceError

from relay import tools
from relay.check_connection import run_checks
from relay.config import ConfigError
from relay.salesforce import SalesforceAuthError, SalesforceClient, SalesforceWriteError

logging.basicConfig(
    stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("relay.server")

mcp = MCPServer("Relay CRM")
_client: SalesforceClient | None = None

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)


def client() -> SalesforceClient:
    """Create the Salesforce client on first use, so the server starts even if .env is wrong."""
    global _client
    if _client is None:
        _client = SalesforceClient()
    return _client


def safe(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Turn expected failures into {"ok": False, "error": ...} so Claude can tell the user why.

    Without this, MCP shows Claude a generic "Error executing tool" and the useful message
    (missing .env value, bad Id, Salesforce refusing a record) is lost.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except (
            tools.ToolInputError,
            ValueError,
            ConfigError,
            SalesforceAuthError,
            SalesforceWriteError,
        ) as exc:
            return {"ok": False, "error": str(exc)}
        except SalesforceError as exc:
            log.warning("Salesforce API error: %s", exc)
            return {"ok": False, "error": f"Salesforce returned an error: {exc}"}
        except Exception:
            log.exception("Unexpected error in tool %s", fn.__name__)
            return {"ok": False, "error": "Unexpected error. Details are in the server log."}

    return wrapper


@mcp.tool(annotations=READ_ONLY)
@safe
def check_salesforce_connection() -> dict[str, Any]:
    """Check the Salesforce connection: org name, API calls left today, and any setup steps
    still missing. Use this first if other Relay tools fail."""
    return {"ok": True, **run_checks(client())}


@mcp.tool(annotations=READ_ONLY)
@safe
def find_account(
    name: str | None = None,
    bdr_status: str | None = None,
    account_tier: str | None = None,
    portfolio_type: str | None = None,
    min_units: int | None = None,
    max_units: int | None = None,
    renewing_within_days: int | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Search accounts by part of the name and/or filters. Returns Ids you can pass to the
    other tools.

    bdr_status: New, In Flow, Resting, Rehome, or Active Opp.
    account_tier: 1 (largest) to 4 (smallest).
    portfolio_type: Fee Managed, Owner Operator, or Mixed.
    renewing_within_days: only accounts whose current contract renews within this many days.
    """
    return tools.find_account(
        client(),
        name=name,
        bdr_status=bdr_status,
        account_tier=account_tier,
        portfolio_type=portfolio_type,
        min_units=min_units,
        max_units=max_units,
        renewing_within_days=renewing_within_days,
        limit=limit,
    )


@mcp.tool(annotations=READ_ONLY)
@safe
def summarize_account(account_id: str) -> dict[str, Any]:
    """Get everything needed before a call with one account: details, contacts, open and past
    deals, the latest activity, and plain-English flags (renewal coming up, gone quiet,
    re-engage date reached). Use find_account first to get the account_id."""
    return tools.summarize_account(client(), account_id)


@mcp.tool(annotations=READ_ONLY)
@safe
def list_open_deals(
    account_id: str | None = None, stage: str | None = None, limit: int = 25
) -> dict[str, Any]:
    """List open opportunities, soonest close date first, with a pipeline total. Optionally
    limit to one account_id or one stage (Meeting Booked, Meeting Held, or Demo)."""
    return tools.list_open_deals(client(), account_id=account_id, stage=stage, limit=limit)


@mcp.tool(annotations=WRITE)
@safe
def log_note(
    account_id: str, subject: str, notes: str, contact_id: str | None = None
) -> dict[str, Any]:
    """Log a completed call or activity note on an account. Writes to Salesforce, so confirm
    the account and wording with the user first. Only demo accounts can be written to.
    contact_id is optional and must belong to the account."""
    return tools.log_note(
        client(), account_id=account_id, subject=subject, notes=notes, contact_id=contact_id
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
