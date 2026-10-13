"""Salesforce connection using the OAuth 2.0 Client Credentials flow.

We request the token ourselves (one POST to the org's My Domain) and hand it to
simple-salesforce. That keeps the auth step visible and easy to explain, and
avoids depending on how a library parses Developer Edition domain names.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any, TypeVar

import requests
from simple_salesforce import Salesforce
from simple_salesforce.exceptions import SalesforceExpiredSession

from relay.config import Settings, load_settings

log = logging.getLogger(__name__)

T = TypeVar("T")

TOKEN_PATH = "/services/oauth2/token"
TOKEN_REFRESH_SECONDS = 60 * 60  # Salesforce sessions last ~2h by default; refresh early.
COMPOSITE_BATCH = 200  # Salesforce limit for sObject Collections create/delete calls.


class SalesforceAuthError(RuntimeError):
    """Raised when Salesforce rejects the client credentials login."""


class SalesforceWriteError(RuntimeError):
    """Raised when Salesforce refuses to create a record."""


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


def chunks(items: list[T], size: int) -> Iterator[list[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def describe_errors(result: dict[str, Any]) -> str:
    """Turn one entry of a Salesforce create/delete response into a readable message."""
    errors = result.get("errors") or []
    parts = []
    for err in errors:
        fields = err.get("fields") or []
        where = f" ({', '.join(fields)})" if fields else ""
        parts.append(f"{err.get('statusCode', 'ERROR')}: {err.get('message', '')}{where}")
    return "; ".join(parts) or "unknown error"


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

    def _run(self, call: Callable[[Salesforce], T]) -> T:
        """Run a Salesforce call. If the session expired mid-flight, log in again and retry once."""
        try:
            return call(self.sf)
        except SalesforceExpiredSession:
            self._connect()
            return call(self.sf)

    # ---- reads -------------------------------------------------------------------------

    def query(self, soql: str) -> list[dict[str, Any]]:
        """Run a SOQL query and return clean records."""
        result = self._run(lambda sf: sf.query_all(soql))
        return [clean(r) for r in result["records"]]

    def org_info(self) -> dict[str, Any]:
        rows = self.query("SELECT Name, OrganizationType FROM Organization LIMIT 1")
        return rows[0] if rows else {}

    def api_usage(self) -> dict[str, int]:
        daily = self._run(lambda sf: sf.restful("limits/"))["DailyApiRequests"]
        return {"max": daily["Max"], "remaining": daily["Remaining"]}

    def field_names(self, sobject: str) -> set[str]:
        describe = self._run(lambda sf: getattr(sf, sobject).describe())
        return {f["name"] for f in describe["fields"]}

    def picklist_values(self, sobject: str, field: str) -> list[str]:
        describe = self._run(lambda sf: getattr(sf, sobject).describe())
        for f in describe["fields"]:
            if f["name"] == field:
                return [v["value"] for v in f.get("picklistValues", []) if v.get("active")]
        return []

    # ---- writes ------------------------------------------------------------------------

    def create(self, sobject: str, data: dict[str, Any]) -> str:
        """Create one record and return its Id. Raises SalesforceWriteError if refused."""
        result = self._run(lambda sf: getattr(sf, sobject).create(data))
        if not result.get("success"):
            raise SalesforceWriteError(describe_errors(result))
        return result["id"]

    def create_many(self, sobject: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Create records in batches of 200 using the sObject Collections endpoint.

        Returns one result per input record, in order: {"id", "success", "errors"}. A bad record
        does not stop the others (allOrNone is false), so the caller decides what to do.
        """
        results: list[dict[str, Any]] = []
        for batch in chunks(records, COMPOSITE_BATCH):
            payload = {
                "allOrNone": False,
                "records": [{"attributes": {"type": sobject}, **rec} for rec in batch],
            }
            body = self._run(
                lambda sf, payload=payload: sf.restful(
                    "composite/sobjects", method="POST", data=json.dumps(payload)
                )
            )
            results.extend(body)
        return results

    def delete_many(self, ids: Iterable[str]) -> list[dict[str, Any]]:
        """Delete records by Id in batches of 200. Returns one result per Id."""
        results: list[dict[str, Any]] = []
        for batch in chunks(list(ids), COMPOSITE_BATCH):
            params = {"ids": ",".join(batch), "allOrNone": "false"}
            body = self._run(
                lambda sf, params=params: sf.restful(
                    "composite/sobjects", params=params, method="DELETE"
                )
            )
            results.extend(body or [])
        return results
