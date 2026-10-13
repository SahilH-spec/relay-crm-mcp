"""Stand-ins for Salesforce so the tests never touch a real org."""

from __future__ import annotations

import re
from typing import Any


class FakeQueryClient:
    """Returns canned rows for any query containing a given piece of text, and records queries."""

    def __init__(self, responses: list[tuple[str, list[dict[str, Any]]]] | None = None) -> None:
        self.responses = responses or []
        self.queries: list[str] = []
        self.created: list[tuple[str, dict[str, Any]]] = []

    def query(self, soql: str) -> list[dict[str, Any]]:
        self.queries.append(soql)
        for needle, rows in self.responses:
            if needle in soql:
                return rows
        return []

    def create(self, sobject: str, data: dict[str, Any]) -> str:
        self.created.append((sobject, data))
        return "00T000000000001AAA"


class FakeOrg:
    """A tiny in-memory Salesforce: stores records, hands out Ids, answers the exact queries
    the seed script runs, and remembers the order in which things were deleted."""

    def __init__(self, org_type: str = "Developer Edition", name: str = "Demo Org") -> None:
        self.org_type = org_type
        self.name = name
        self.records: dict[str, dict[str, dict[str, Any]]] = {
            "Account": {}, "Contact": {}, "Opportunity": {}, "Task": {},
        }  # fmt: skip
        self.delete_order: list[str] = []
        self.refuse: tuple[str, str] | None = None  # (sobject, field) to simulate a rejection
        self._next = 0

    def _new_id(self, sobject: str) -> str:
        self._next += 1
        return f"{sobject[:3].lower()}{self._next:015d}"[:18]

    # --- the client interface the seed script uses ---
    def org_info(self) -> dict[str, Any]:
        return {"Name": self.name, "OrganizationType": self.org_type}

    def create_many(self, sobject: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results = []
        for rec in records:
            if self.refuse and self.refuse[0] == sobject:
                results.append(
                    {
                        "success": False,
                        "errors": [
                            {"statusCode": "INVALID_FIELD", "message": "nope", "fields": [self.refuse[1]]}
                        ],
                    }
                )
                continue
            new_id = self._new_id(sobject)
            self.records[sobject][new_id] = {"Id": new_id, **rec}
            results.append({"id": new_id, "success": True, "errors": []})
        return results

    def delete_many(self, ids: Any) -> list[dict[str, Any]]:
        results = []
        for record_id in ids:
            for sobject, store in self.records.items():
                if record_id in store:
                    del store[record_id]
                    self.delete_order.append(sobject)
            results.append({"id": record_id, "success": True, "errors": []})
        return results

    def query(self, soql: str) -> list[dict[str, Any]]:
        if "FROM Account WHERE AccountNumber LIKE" in soql:
            prefix = re.search(r"LIKE '([^%']*)%'", soql).group(1)
            return [
                {"Id": i}
                for i, r in self.records["Account"].items()
                if r.get("AccountNumber", "").startswith(prefix)
            ]
        match = re.match(r"SELECT Id FROM (\w+) WHERE (\w+) IN \((.*)\)", soql)
        if match:
            sobject, parent_field, raw = match.groups()
            parents = set(re.findall(r"'([^']+)'", raw))
            return [{"Id": i} for i, r in self.records[sobject].items() if r.get(parent_field) in parents]
        raise AssertionError(f"FakeOrg got an unexpected query: {soql}")
