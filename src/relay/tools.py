"""The logic behind Relay's tools.

Each function takes a client (anything with .query() and .create()) so it can be tested with a
fake. Functions return plain dicts, raise ToolInputError for bad input, and never build SOQL
from raw user text: strings are escaped and picklist-style inputs are checked against allowed
values first.

Design note: the server fetches and computes facts (dates, counts, flags). Claude turns those
facts into prose. That keeps numbers reliable and keeps the server free of any LLM dependency.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Protocol

from relay.constants import (
    ACCOUNT_FIELDS,
    ACCOUNT_TIERS,
    BDR_STATUSES,
    DEMO_PREFIX,
    PORTFOLIO_TYPES,
)

MAX_LIMIT = 50
STALE_ACTIVITY_DAYS = 30
RENEWAL_WINDOW_DAYS = 90
NOTE_FOOTER = "-- Logged via Relay (Claude)"

SF_ID_PATTERN = re.compile(r"^(?:[a-zA-Z0-9]{15}|[a-zA-Z0-9]{18})$")


class ToolInputError(ValueError):
    """The caller (Claude) passed something we will not run. The message is shown to Claude."""


class QueryClient(Protocol):
    def query(self, soql: str) -> list[dict[str, Any]]: ...


class WriteClient(QueryClient, Protocol):
    def create(self, sobject: str, data: dict[str, Any]) -> str: ...


# ---- small helpers -----------------------------------------------------------------------


def soql_string(value: str) -> str:
    """Quote a value as a SOQL string literal, escaping backslashes and quotes."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def like_contains(value: str) -> str:
    """Build a quoted SOQL LIKE pattern that matches `value` anywhere, with wildcards escaped."""
    # First make the text safe inside a quoted string, then escape LIKE's own wildcards.
    # Doing both through soql_string() would escape the backslashes a second time.
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    escaped = escaped.replace("%", "\\%").replace("_", "\\_")
    return f"'%{escaped}%'"


def require_id(value: str, label: str) -> str:
    value = (value or "").strip()
    if not SF_ID_PATTERN.match(value):
        raise ToolInputError(
            f"{label} must be a 15 or 18 character Salesforce Id. "
            "Use find_account to look up the Id first."
        )
    return value


def clamp_limit(limit: int) -> int:
    try:
        limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise ToolInputError("limit must be a whole number") from exc
    return max(1, min(limit, MAX_LIMIT))


def one_of(value: Any, allowed: tuple[str, ...], label: str) -> str:
    text = str(value).strip()
    if text not in allowed:
        raise ToolInputError(f"{label} must be one of: {', '.join(allowed)}")
    return text


def parse_day(value: Any) -> date | None:
    if not value:
        return None
    return date.fromisoformat(str(value)[:10])


def account_view(row: dict[str, Any]) -> dict[str, Any]:
    """Pick the friendly fields out of an Account row."""
    return {
        "id": row.get("Id"),
        "name": row.get("Name"),
        "account_number": row.get("AccountNumber"),
        "location": row.get("Site"),
        "units": int(row["Units__c"]) if row.get("Units__c") is not None else None,
        "portfolio_type": row.get("Portfolio_Type__c"),
        "current_software": row.get("Current_Software__c"),
        "contract_renewal_date": row.get("Contract_Renewal_Date__c"),
        "account_tier": row.get("Account_Tier__c"),
        "bdr_status": row.get("BDR_Status__c"),
        "re_engage_date": row.get("Re_engage_Date__c"),
        "rehome_reason": row.get("Rehome_Reason__c"),
    }


# ---- find_account ------------------------------------------------------------------------


def find_account(
    client: QueryClient,
    name: str | None = None,
    bdr_status: str | None = None,
    account_tier: str | int | None = None,
    portfolio_type: str | None = None,
    min_units: int | None = None,
    max_units: int | None = None,
    renewing_within_days: int | None = None,
    limit: int = 10,
    today: date | None = None,
) -> dict[str, Any]:
    """Search accounts by name and/or filters. Returns a short list; use summarize_account next."""
    today = today or date.today()
    limit = clamp_limit(limit)
    where: list[str] = []

    if name and name.strip():
        where.append(f"Name LIKE {like_contains(name.strip())}")
    if bdr_status:
        where.append(f"BDR_Status__c = {soql_string(one_of(bdr_status, BDR_STATUSES, 'bdr_status'))}")
    if account_tier is not None and str(account_tier).strip():
        where.append(
            f"Account_Tier__c = {soql_string(one_of(account_tier, ACCOUNT_TIERS, 'account_tier'))}"
        )
    if portfolio_type:
        value = one_of(portfolio_type, PORTFOLIO_TYPES, "portfolio_type")
        where.append(f"Portfolio_Type__c = {soql_string(value)}")
    if min_units is not None:
        where.append(f"Units__c >= {int(min_units)}")
    if max_units is not None:
        where.append(f"Units__c <= {int(max_units)}")
    if renewing_within_days is not None:
        days = max(0, int(renewing_within_days))
        where.append(f"Contract_Renewal_Date__c >= {today.isoformat()}")
        where.append(f"Contract_Renewal_Date__c <= {(today + timedelta(days=days)).isoformat()}")

    soql = f"SELECT {', '.join(ACCOUNT_FIELDS)} FROM Account"
    if where:
        soql += " WHERE " + " AND ".join(where)
    # Ask for one extra row so we can tell Claude whether there are more matches.
    soql += f" ORDER BY Name LIMIT {limit + 1}"

    rows = client.query(soql)
    return {
        "ok": True,
        "count": min(len(rows), limit),
        "more_available": len(rows) > limit,
        "accounts": [account_view(r) for r in rows[:limit]],
    }


# ---- summarize_account -------------------------------------------------------------------


def build_signals(
    account: dict[str, Any],
    open_deals: list[dict[str, Any]],
    activity: list[dict[str, Any]],
    today: date,
) -> list[str]:
    """Plain-English flags a rep would want to know before a call. Pure function: easy to test."""
    signals: list[str] = []

    renewal = parse_day(account.get("contract_renewal_date"))
    if renewal:
        days = (renewal - today).days
        if 0 <= days <= RENEWAL_WINDOW_DAYS:
            signals.append(f"Current software contract renews in {days} days ({renewal}).")
        elif days < 0:
            signals.append(f"Recorded renewal date ({renewal}) has passed; confirm the new term.")

    last_dates = [d for d in (parse_day(a.get("date")) for a in activity) if d]
    if not last_dates:
        signals.append("No activity has been logged on this account.")
    else:
        idle = (today - max(last_dates)).days
        if idle > STALE_ACTIVITY_DAYS:
            signals.append(f"No activity logged in {idle} days.")

    re_engage = parse_day(account.get("re_engage_date"))
    if re_engage:
        if re_engage <= today:
            signals.append(f"Re-engage date reached ({re_engage}); this account is due for outreach.")
        else:
            signals.append(f"Resting until {re_engage}.")

    if account.get("bdr_status") == "Rehome":
        reason = account.get("rehome_reason") or "no reason recorded"
        signals.append(f"Marked for rehome ({reason}).")

    for deal in open_deals:
        close = parse_day(deal.get("close_date"))
        if close and close < today:
            signals.append(f"Open opportunity '{deal.get('name')}' is past its close date ({close}).")

    if account.get("bdr_status") == "Active Opp" and not open_deals:
        signals.append("Status says Active Opp but there is no open opportunity.")

    return signals


def summarize_account(
    client: QueryClient, account_id: str, today: date | None = None
) -> dict[str, Any]:
    """Everything a rep needs before a call: account, contacts, deals, recent activity, flags."""
    today = today or date.today()
    account_id = require_id(account_id, "account_id")
    quoted = soql_string(account_id)

    rows = client.query(
        f"SELECT {', '.join(ACCOUNT_FIELDS)} FROM Account WHERE Id = {quoted} LIMIT 1"
    )
    if not rows:
        raise ToolInputError("No account found with that Id.")
    account = account_view(rows[0])

    contacts = client.query(
        f"SELECT Id, Name, Title, Email, Phone FROM Contact WHERE AccountId = {quoted} "
        "ORDER BY Name LIMIT 10"
    )
    opps = client.query(
        "SELECT Id, Name, StageName, Amount, CloseDate, IsClosed, IsWon FROM Opportunity "
        f"WHERE AccountId = {quoted} ORDER BY CloseDate DESC LIMIT 25"
    )
    tasks = client.query(
        "SELECT Id, Subject, ActivityDate, Status, Description, Who.Name FROM Task "
        f"WHERE WhatId = {quoted} ORDER BY ActivityDate DESC NULLS LAST, CreatedDate DESC LIMIT 10"
    )

    deal_views = [
        {
            "id": o.get("Id"),
            "name": o.get("Name"),
            "stage": o.get("StageName"),
            "amount": o.get("Amount"),
            "close_date": o.get("CloseDate"),
            "is_closed": bool(o.get("IsClosed")),
            "is_won": bool(o.get("IsWon")),
        }
        for o in opps
    ]
    open_deals = [d for d in deal_views if not d["is_closed"]]

    activity = [
        {
            "date": t.get("ActivityDate"),
            "subject": t.get("Subject"),
            "status": t.get("Status"),
            "contact": (t.get("Who") or {}).get("Name"),
            "notes": (t.get("Description") or "")[:300],
        }
        for t in tasks
    ]

    return {
        "ok": True,
        "account": account,
        "contacts": [
            {
                "id": c.get("Id"),
                "name": c.get("Name"),
                "title": c.get("Title"),
                "email": c.get("Email"),
                "phone": c.get("Phone"),
            }
            for c in contacts
        ],
        "opportunities": {
            "open": open_deals,
            "open_amount": sum(d["amount"] or 0 for d in open_deals),
            "closed_won": sum(1 for d in deal_views if d["is_closed"] and d["is_won"]),
            "closed_lost": sum(1 for d in deal_views if d["is_closed"] and not d["is_won"]),
        },
        "recent_activity": activity,
        "signals": build_signals(account, open_deals, activity, today),
    }


# ---- list_open_deals ---------------------------------------------------------------------


def list_open_deals(
    client: QueryClient,
    account_id: str | None = None,
    stage: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Open opportunities, soonest close date first. Optionally for one account or one stage."""
    limit = clamp_limit(limit)
    where = ["IsClosed = false"]
    if account_id:
        where.append(f"AccountId = {soql_string(require_id(account_id, 'account_id'))}")
    if stage and stage.strip():
        where.append(f"StageName = {soql_string(stage.strip())}")

    rows = client.query(
        "SELECT Id, Name, AccountId, Account.Name, StageName, Amount, CloseDate, NextStep "
        f"FROM Opportunity WHERE {' AND '.join(where)} ORDER BY CloseDate ASC LIMIT {limit + 1}"
    )
    shown = rows[:limit]
    deals = [
        {
            "id": r.get("Id"),
            "name": r.get("Name"),
            "account_id": r.get("AccountId"),
            "account_name": (r.get("Account") or {}).get("Name"),
            "stage": r.get("StageName"),
            "amount": r.get("Amount"),
            "close_date": r.get("CloseDate"),
            "next_step": r.get("NextStep"),
        }
        for r in shown
    ]
    by_stage: dict[str, int] = {}
    for d in deals:
        by_stage[d["stage"]] = by_stage.get(d["stage"], 0) + 1

    return {
        "ok": True,
        "count": len(deals),
        "more_available": len(rows) > limit,
        "total_amount": sum(d["amount"] or 0 for d in deals),
        "by_stage": by_stage,
        "deals": deals,
    }


# ---- log_note ----------------------------------------------------------------------------


def log_note(
    client: WriteClient,
    account_id: str,
    subject: str,
    notes: str,
    contact_id: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Log a completed call/activity on a demo account.

    Guardrail: only accounts created by the seed script (AccountNumber starts with RELAY-) can
    be written to, so a mistake or a bad prompt can never touch real records in a shared org.
    """
    today = today or date.today()
    account_id = require_id(account_id, "account_id")
    subject = (subject or "").strip()
    notes = (notes or "").strip()
    if not subject:
        raise ToolInputError("subject is required (a short title for the note).")
    if len(subject) > 200:
        raise ToolInputError("subject must be 200 characters or fewer.")
    if not notes:
        raise ToolInputError("notes is required.")
    if len(notes) > 4000:
        raise ToolInputError("notes must be 4000 characters or fewer.")

    rows = client.query(
        f"SELECT Id, Name, AccountNumber FROM Account WHERE Id = {soql_string(account_id)} LIMIT 1"
    )
    if not rows:
        raise ToolInputError("No account found with that Id.")
    account = rows[0]
    if not (account.get("AccountNumber") or "").startswith(DEMO_PREFIX):
        raise ToolInputError(
            "Relay only writes to demo accounts (account number starting with "
            f"'{DEMO_PREFIX}'). This account is not one of them."
        )

    task: dict[str, Any] = {
        "Subject": subject,
        "Description": f"{notes}\n\n{NOTE_FOOTER}",
        "Status": "Completed",
        "Priority": "Normal",
        "ActivityDate": today.isoformat(),
        "WhatId": account_id,
    }
    if contact_id:
        contact_id = require_id(contact_id, "contact_id")
        owns = client.query(
            f"SELECT Id FROM Contact WHERE Id = {soql_string(contact_id)} "
            f"AND AccountId = {soql_string(account_id)} LIMIT 1"
        )
        if not owns:
            raise ToolInputError("That contact does not belong to this account.")
        task["WhoId"] = contact_id

    task_id = client.create("Task", task)
    return {
        "ok": True,
        "task_id": task_id,
        "account_id": account_id,
        "account_name": account.get("Name"),
        "logged_on": today.isoformat(),
    }
