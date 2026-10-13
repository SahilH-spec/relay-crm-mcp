# Relay

An MCP server that connects Claude to a Salesforce org so a sales rep can ask for account
context in plain English instead of clicking through records.

> **Status: Phase 1 (connection).** One tool works today: `check_salesforce_connection`.
> Account lookup, call logging, and deal tools are planned next. All data in the demo org
> is fictional. This project is independent and is not affiliated with any employer.

## What it does today

- Logs in to a Salesforce Developer Edition org with the OAuth 2.0 Client Credentials flow
- Refreshes the session automatically and retries once if it expires
- Validates `.env` settings and catches common mistakes (for example, pasting the browser URL
  instead of the My Domain URL)
- Exposes `check_salesforce_connection` to Claude: org name, API calls left today, and any
  setup steps still missing

## Quick start (Windows, PowerShell)

```powershell
uv sync
Copy-Item .env.example .env   # then fill in your Salesforce values
uv run relay-check
uv run pytest
```

Connect to Claude Desktop (Settings > Developer > Edit Config):

```json
{
  "mcpServers": {
    "relay-crm": {
      "command": "uv",
      "args": ["--directory", "C:\\path\\to\\relay-crm-mcp", "run", "relay-server"]
    }
  }
}
```

## Security notes

- Credentials live only in `.env`, which is git-ignored. Secrets are never printed or logged.
- The Run As user for the app should be a dedicated low-permission user in production;
  this demo uses a personal Developer Edition org with fake data.

## License

MIT
