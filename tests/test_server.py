from relay import server
from relay.config import ConfigError


def test_tool_returns_readable_error_when_settings_are_bad(monkeypatch):
    def boom():
        raise ConfigError("Missing in .env: SF_CLIENT_ID")

    monkeypatch.setattr(server, "client", boom)
    result = server.check_salesforce_connection()
    assert result["ok"] is False
    assert "SF_CLIENT_ID" in result["error"]


def test_tool_reports_ok_with_check_results(monkeypatch):
    monkeypatch.setattr(server, "client", lambda: object())
    monkeypatch.setattr(server, "run_checks", lambda c: {"org_name": "Demo"})
    assert server.check_salesforce_connection() == {"ok": True, "org_name": "Demo"}
