"""Starts the real server as a subprocess and talks to it the way Claude Desktop does (over stdio).

Uses placeholder Salesforce settings and only calls tools that fail validation before any network
request, so these tests never contact Salesforce and never use your real .env values.
"""

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "check_salesforce_connection",
    "find_account",
    "summarize_account",
    "list_open_deals",
    "log_note",
}


def params() -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "relay.server"],
        env={
            "SF_MY_DOMAIN_URL": "https://placeholder.my.salesforce.com",
            "SF_CLIENT_ID": "placeholder",
            "SF_CLIENT_SECRET": "placeholder",
        },
    )


async def talk():
    async with stdio_client(params()) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        listed = (await session.list_tools()).tools
        bad_status = await session.call_tool("find_account", {"bdr_status": "Hot"})
        bad_id = await session.call_tool("summarize_account", {"account_id": "nope"})
        bad_write = await session.call_tool(
            "log_note", {"account_id": "x", "subject": "s", "notes": "n"}
        )
        return listed, bad_status, bad_id, bad_write


def payload(result) -> dict:
    return json.loads(result.content[0].text)


def test_server_lists_all_tools_with_the_right_hints():
    listed, *_ = asyncio.run(talk())
    tools = {t.name: t for t in listed}
    assert set(tools) == EXPECTED_TOOLS
    for name in EXPECTED_TOOLS - {"log_note"}:
        assert tools[name].annotations.read_only_hint is True, name
    assert tools["log_note"].annotations.read_only_hint is False
    assert tools["log_note"].annotations.destructive_hint is False
    assert all(t.description for t in listed)


def test_bad_input_comes_back_as_a_readable_message():
    _, bad_status, bad_id, bad_write = asyncio.run(talk())
    status = payload(bad_status)
    assert status["ok"] is False and "bdr_status must be one of" in status["error"]
    assert payload(bad_id)["ok"] is False and "15 or 18 character" in payload(bad_id)["error"]
    assert payload(bad_write)["ok"] is False
