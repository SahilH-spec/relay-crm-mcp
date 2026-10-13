"""Salesforce connection using the OAuth 2.0 Client Credentials flow.

We request the token ourselves (one POST to the org's My Domain) and hand it to
simple-salesforce. That keeps the auth step visible and easy to explain, and
avoids depending on how a library parses Developer Edition domain names.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests
from simple_salesforce import Salesforce
from simple_salesforce.exceptions import SalesforceExpiredSession

from relay.config import Settings, load_settings

log = logging.getLogger(__name__)

TOKEN_PATH = "/services/oauth2/token"
TOKEN_REFRESH_SECONDS = 60 * 60  # Salesforce sessions last ~2h by default; refresh early.


class SalesforceAuthError(RuntimeError):
    """Raised when Salesforce rejects the client credentials login."""


def fetch_access_token(settings: Settings) -> tuple[str, str]:
    """Trade the app's key and secret for an access token. Returns (token, instance_url)."""
    resp = requests.post(
        settings.sf_domain + TOKEN_PATH,
        data={
            "grant_type": "client_credentials",
            "client_id": settings.sf_client_id,
            "client_secret": settings.sf_client_secret,
        },
        timeout=20,
    )
    if resp.status_code != 200:
        try:
            err = resp.json()
            detail = f"{err.get('error')}: {err.get('error_description')}"
        except ValueError:
            detail = resp.text[:200]
        raise SalesforceAuthError(f"Salesforce rejected the login ({resp.status_code}). {detail}")
    body = resp.json()
    return body["access_token"], body["instance_url"]


def clean(record: dict[str, Any]) -> dict[str, Any]:
    """Drop Salesforce's 'attributes' metadata so Claude only sees real fields."""
    out = {}
    for key, value in record.items():
        if key == "attributes":
            continue
        if isinstance(value, dict):
            value = clean(value)
        elif isinstance(value, list):
            value = [clean(v) if isinstance(v, dict) else v for v in value]
        out[key] = value
    return out


class SalesforceClient:
    """Thin wrapper that logs in lazily and re-authenticates when the token goes stale."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self._sf: Salesforce | None = None
        self._issued_at = 0.0

    def _connect(self) -> None:
        token, instance_url = fetch_access_token(self.settings)
        self._sf = Salesforce(
            instance_url=instance_url,
            session_id=token,
            version=self.settings.sf_api_version,
        )
        self._issued_at = time.monotonic()
        log.info("Connected to Salesforce at %s", instance_url)

    @property
    def sf(self) -> Salesforce:
        if self._sf is None or time.monotonic() - self._issued_at > TOKEN_REFRESH_SECONDS:
            self._connect()
        return self._sf  # type: ignore[return-value]

    def query(self, soql: str) -> list[dict[str, Any]]:
        """Run a SOQL query and return clean records. Retries once if the session expired."""
        try:
            result = self.sf.query_all(soql)
        except SalesforceExpiredSession:
            self._connect()
            result = self.sf.query_all(soql)
        return [clean(r) for r in result["records"]]

    def org_info(self) -> dict[str, Any]:
        rows = self.query("SELECT Name, OrganizationType FROM Organization LIMIT 1")
        return rows[0] if rows else {}

    def api_usage(self) -> dict[str, int]:
        daily = self.sf.restful("limits/")["DailyApiRequests"]
        return {"max": daily["Max"], "remaining": daily["Remaining"]}

    def field_names(self, sobject: str) -> set[str]:
        describe = getattr(self.sf, sobject).describe()
        return {f["name"] for f in describe["fields"]}

    def picklist_values(self, sobject: str, field: str) -> list[str]:
        describe = getattr(self.sf, sobject).describe()
        for f in describe["fields"]:
            if f["name"] == field:
                return [v["value"] for v in f.get("picklistValues", []) if v.get("active")]
        return []
