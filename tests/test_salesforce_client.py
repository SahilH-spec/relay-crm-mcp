"""Checks the parts of SalesforceClient that talk to Salesforce, using a stub instead of the network."""

import json
import time
from types import SimpleNamespace

import pytest
from simple_salesforce.exceptions import SalesforceExpiredSession

from relay.config import Settings
from relay.salesforce import SalesforceClient, SalesforceWriteError, chunks, describe_errors


class StubSF:
    def __init__(self):
        self.calls = []
        self.Task = SimpleNamespace(create=self._create)
        self.fail_once = False

    def _create(self, data):
        self.calls.append(("create", data))
        return {"id": "00T000000000001AAA", "success": True, "errors": []}

    def restful(self, path, params=None, method="GET", **kwargs):
        self.calls.append((method, path, params, kwargs))
        if self.fail_once:
            self.fail_once = False
            raise SalesforceExpiredSession("https://x", 401, path, b"expired")
        if method == "POST":
            records = json.loads(kwargs["data"])["records"]
            return [{"id": f"id{i}", "success": True, "errors": []} for i, _ in enumerate(records)]
        if method == "DELETE":
            return [{"id": i, "success": True, "errors": []} for i in params["ids"].split(",")]
        return {"DailyApiRequests": {"Max": 15000, "Remaining": 14000}}


@pytest.fixture
def client():
    c = SalesforceClient(Settings("https://x.my.salesforce.com", "key", "secret"))
    c._sf = StubSF()
    c._issued_at = time.monotonic()
    return c


def test_chunks():
    assert list(chunks(list(range(5)), 2)) == [[0, 1], [2, 3], [4]]
    assert list(chunks([], 2)) == []


def test_create_many_batches_of_200_and_tags_the_type(client):
    results = client.create_many("Account", [{"Name": f"A{i}"} for i in range(450)])
    assert len(results) == 450
    posts = [c for c in client._sf.calls if c[0] == "POST"]
    assert len(posts) == 3
    body = json.loads(posts[0][3]["data"])
    assert body["allOrNone"] is False and len(body["records"]) == 200
    assert body["records"][0] == {"attributes": {"type": "Account"}, "Name": "A0"}
    assert posts[0][1] == "composite/sobjects"


def test_delete_many_batches_and_sends_ids(client):
    ids = [f"id{i}" for i in range(250)]
    results = client.delete_many(ids)
    assert len(results) == 250
    deletes = [c for c in client._sf.calls if c[0] == "DELETE"]
    assert len(deletes) == 2
    assert deletes[0][2]["allOrNone"] == "false" and deletes[0][2]["ids"].count(",") == 199


def test_create_returns_id_and_raises_on_refusal(client):
    assert client.create("Task", {"Subject": "x"}) == "00T000000000001AAA"
    client._sf.Task = SimpleNamespace(
        create=lambda data: {"success": False, "errors": [{"statusCode": "BAD", "message": "no"}]}
    )
    with pytest.raises(SalesforceWriteError, match="BAD: no"):
        client.create("Task", {"Subject": "x"})


def test_expired_session_logs_in_again_and_retries(client, monkeypatch):
    reconnects = []
    monkeypatch.setattr(client, "_connect", lambda: reconnects.append(1))
    client._sf.fail_once = True
    assert client.api_usage() == {"max": 15000, "remaining": 14000}
    assert reconnects == [1]


def test_describe_errors():
    result = {"errors": [{"statusCode": "REQUIRED_FIELD_MISSING", "message": "Missing", "fields": ["Name"]}]}
    assert describe_errors(result) == "REQUIRED_FIELD_MISSING: Missing (Name)"
    assert describe_errors({"errors": []}) == "unknown error"
