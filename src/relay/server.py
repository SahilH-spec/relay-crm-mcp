"""Relay MCP server. Phase 1: one tool that proves Claude can reach Salesforce.

Logs go to stderr. Never print() in this file: over stdio, stdout carries the
MCP protocol and stray text breaks the connection.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from mcp.server import MCPServer

from relay.check_connection import run_checks
from relay.config import ConfigError
from relay.salesforce import SalesforceAuthError, SalesforceClient

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

mcp = MCPServer("Relay CRM")
_client: SalesforceClient | None = None


def client() -> SalesforceClient:
    """Create the Salesforce client on first use, so the server starts even if .env is wrong."""
    global _client
    if _client is None:
        _client = SalesforceClient()
    return _client


@mcp.tool()
def check_salesforce_connection() -> dict[str, Any]:
    """Check the Salesforce connection: org name, API calls left today, and any setup steps
    still missing. Use this first if other Relay tools fail."""
    try:
        return {"ok": True, **run_checks(client())}
    except (ConfigError, SalesforceAuthError) as exc:
        # Return the message instead of raising, so Claude can tell the user what to fix.
        return {"ok": False, "error": str(exc)}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
