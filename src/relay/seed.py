"""Loads (or removes) the fictional demo data in your Salesforce Developer org.

Run from the project folder:
    uv run relay-seed --dry-run      show what would be created, change nothing
    uv run relay-seed                create the demo data (stops if demo data already exists)
    uv run relay-seed --reset        delete existing demo data, then create fresh data
    uv run relay-seed --clean        delete the demo data and stop

Only records created by this script are ever deleted: they are found through the Account
number prefix (RELAY-). Salesforce's own sample records are never touched.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import Any, Protocol

from relay.config import ConfigError
from relay.constants import DEMO_PREFIX
from relay.salesforce import SalesforceAuthError, SalesforceClient, chunks, describe_errors
from relay.seed_data import DEFAULT_COUNT, DemoAccount, build_demo_data, summarize

ID_BATCH = 100  # how many parent Ids go into one "WHERE ... IN (...)" query


class SeedClient(Protocol):
    def query(self, soql: str) -> list[dict[str, Any]]: ...
    def org_info(self) -> dict[str, Any]: ...
    def create_many(self, sobject: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
    def delete_many(self, ids: Any) -> list[dict[str, Any]]: ...


class SeedError(RuntimeError):
    """Something stopped the seed run; the message says what to do next."""


def ensure_developer_org(client: SeedClient, allow_any: bool) -> str:
    """Refuse to write fake data into anything that does not look like a Developer org."""
    info = client.org_info()
    org_type = info.get("OrganizationType") or "unknown"
    if "developer" not in org_type.lower() and not allow_any:
        raise SeedError(
            f"This org reports its type as '{org_type}', not a Developer Edition org. "
            "The seed script is meant for a throwaway demo org only. If you are certain, "
            "run it again with --allow-non-dev."
        )
    return info.get("Name") or "(unnamed org)"


def find_demo_account_ids(client: SeedClient) -> list[str]:
    rows = client.query(f"SELECT Id FROM Account WHERE AccountNumber LIKE '{DEMO_PREFIX}%'")
    return [r["Id"] for r in rows]


def delete_demo_data(client: SeedClient, account_ids: list[str]) -> dict[str, int]:
    """Delete children first, then accounts. Salesforce can block deleting an account that
    still has related records, so the order matters."""
    deleted = {"Task": 0, "Opportunity": 0, "Contact": 0, "Account": 0}
    for sobject, parent_field in (
        ("Task", "WhatId"),
        ("Opportunity", "AccountId"),
        ("Contact", "AccountId"),
    ):
        ids: list[str] = []
        for batch in chunks(account_ids, ID_BATCH):
            quoted = ",".join(f"'{i}'" for i in batch)
            soql = f"SELECT Id FROM {sobject} WHERE {parent_field} IN ({quoted})"
            ids += [r["Id"] for r in client.query(soql)]
        deleted[sobject] = _count_successes(client.delete_many(ids), sobject)
    deleted["Account"] = _count_successes(client.delete_many(account_ids), "Account")
    return deleted


def _count_successes(results: list[dict[str, Any]], label: str) -> int:
    failed = [describe_errors(r) for r in results if not r.get("success")]
    if failed:
        raise SeedError(f"Could not delete some {label} records: {failed[0]}")
    return len(results)


def _create(client: SeedClient, sobject: str, records: list[dict[str, Any]]) -> list[str]:
    """Create records and return their Ids in order. Stops with a clear message on any failure."""
    results = client.create_many(sobject, records)
    ids: list[str] = []
    for position, result in enumerate(results):
        if not result.get("success"):
            label = records[position].get("Name") or records[position].get("LastName") or position
            raise SeedError(
                f"Salesforce refused a {sobject} ('{label}'): {describe_errors(result)}\n"
                "Nothing more will be created. Run `uv run relay-seed --clean` to remove what "
                "was already created, fix the cause, then run the seed again."
            )
        ids.append(result["id"])
    return ids


def load_demo_data(client: SeedClient, accounts: list[DemoAccount]) -> dict[str, int]:
    account_ids = _create(client, "Account", [a.fields for a in accounts])

    contact_rows: list[dict[str, Any]] = []
    owners: list[tuple[int, int]] = []  # (account position, contact position) for each row
    for a_pos, account in enumerate(accounts):
        for c_pos, contact in enumerate(account.contacts):
            contact_rows.append({**contact, "AccountId": account_ids[a_pos]})
            owners.append((a_pos, c_pos))
    contact_ids = _create(client, "Contact", contact_rows)
    contact_lookup = dict(zip(owners, contact_ids, strict=True))

    opp_rows = [
        {**opp, "AccountId": account_ids[a_pos]}
        for a_pos, account in enumerate(accounts)
        for opp in account.opportunities
    ]
    opp_ids = _create(client, "Opportunity", opp_rows) if opp_rows else []

    task_rows = []
    for a_pos, account in enumerate(accounts):
        for task in account.tasks:
            row = {k: v for k, v in task.items() if k != "contact_index"}
            row["WhatId"] = account_ids[a_pos]
            row["WhoId"] = contact_lookup[(a_pos, task["contact_index"])]
            task_rows.append(row)
    task_ids = _create(client, "Task", task_rows) if task_rows else []

    return {
        "Account": len(account_ids),
        "Contact": len(contact_ids),
        "Opportunity": len(opp_ids),
        "Task": len(task_ids),
    }


def run_seed(
    client: SeedClient,
    *,
    count: int = DEFAULT_COUNT,
    reset: bool = False,
    clean: bool = False,
    dry_run: bool = False,
    assume_yes: bool = False,
    allow_non_dev: bool = False,
    confirm: Callable[[str], bool] | None = None,
    say: Callable[[str], None] = print,
) -> None:
    accounts = build_demo_data(count=count)
    plan = summarize(accounts)

    if dry_run:
        say(
            f"[dry run] Would create {plan['accounts']} accounts, {plan['contacts']} contacts, "
            f"{plan['opportunities']} opportunities, and {plan['tasks']} call-history tasks."
        )
        mix = ", ".join(f"{k} {v}" for k, v in plan["by_status"].items())
        say(f"[dry run] Accounts by BDR status: {mix}")
        say("[dry run] Example accounts: " + ", ".join(a.fields["Name"] for a in accounts[:3]))
        say("[dry run] Nothing was changed.")
        return

    org_name = ensure_developer_org(client, allow_non_dev)
    say(f"[ok] Connected to '{org_name}'")

    existing = find_demo_account_ids(client)
    if existing and not (reset or clean):
        raise SeedError(
            f"Demo data already exists ({len(existing)} accounts). "
            "Run with --reset to replace it, or --clean to remove it."
        )

    if existing and (reset or clean):
        if not assume_yes:
            ask = confirm or (lambda msg: input(msg + " Type 'yes' to continue: ").strip() == "yes")
            if not ask(f"This will delete {len(existing)} demo accounts and everything attached to them."):
                raise SeedError("Cancelled. Nothing was deleted.")
        deleted = delete_demo_data(client, existing)
        say("[ok] Deleted: " + ", ".join(f"{n} {k}" for k, n in deleted.items()))

    if clean:
        say("[ok] Demo data removed.")
        return

    created = load_demo_data(client, accounts)
    say("[ok] Created: " + ", ".join(f"{n} {k}" for k, n in created.items()))
    say("Done. Try asking Claude: 'Find accounts renewing in the next 60 days.'")


def main() -> int:
    parser = argparse.ArgumentParser(description="Load fictional demo data into your Salesforce org.")
    parser.add_argument("--dry-run", action="store_true", help="show the plan; change nothing")
    parser.add_argument("--reset", action="store_true", help="delete existing demo data first")
    parser.add_argument("--clean", action="store_true", help="delete demo data and stop")
    parser.add_argument("--yes", action="store_true", help="skip the delete confirmation")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help="number of accounts (5-200)")
    parser.add_argument("--allow-non-dev", action="store_true", help="skip the Developer org check")
    args = parser.parse_args()

    if not 5 <= args.count <= 200:
        print("[!!] --count must be between 5 and 200")
        return 1

    try:
        client = None if args.dry_run else SalesforceClient()
        run_seed(
            client,  # type: ignore[arg-type]
            count=args.count,
            reset=args.reset,
            clean=args.clean,
            dry_run=args.dry_run,
            assume_yes=args.yes,
            allow_non_dev=args.allow_non_dev,
        )
    except ConfigError as exc:
        print(f"[!!] Settings problem: {exc}")
        return 1
    except SalesforceAuthError as exc:
        print(f"[!!] Login failed: {exc}")
        return 1
    except SeedError as exc:
        print(f"[!!] {exc}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
