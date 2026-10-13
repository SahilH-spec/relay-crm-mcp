from datetime import date

import pytest

from relay import tools
from relay.tools import ToolInputError
from tests.fakes import FakeQueryClient

TODAY = date(2026, 10, 13)
RECENT = [{"date": "2026-10-10"}]
ACCOUNT_ID = "001000000000001AAA"
CONTACT_ID = "003000000000001AAA"


def account_row(**overrides):
    row = {
        "Id": ACCOUNT_ID,
        "Name": "Harbor Realty Group",
        "AccountNumber": "RELAY-0001",
        "Site": "Dallas, TX",
        "Units__c": 120.0,
        "Portfolio_Type__c": "Fee Managed",
        "Current_Software__c": "Spreadsheets only",
        "Contract_Renewal_Date__c": "2026-12-01",
        "Account_Tier__c": "3",
        "BDR_Status__c": "Active Opp",
        "Re_engage_Date__c": None,
        "Rehome_Reason__c": None,
    }
    row.update(overrides)
    return row


# ---- helpers -----------------------------------------------------------------------------


def test_soql_string_escapes_quotes_and_backslashes():
    assert tools.soql_string("O'Brien") == "'O\\'Brien'"
    assert tools.soql_string("a\\b") == "'a\\\\b'"


def test_like_contains_escapes_wildcards():
    assert tools.like_contains("50%_off") == "'%50\\%\\_off%'"


def test_injection_attempt_stays_inside_the_string():
    client = FakeQueryClient()
    tools.find_account(client, name="x' OR Name != '", today=TODAY)
    assert "x\\' OR Name != \\'" in client.queries[0]


@pytest.mark.parametrize("bad", ["", "abc", "001'; DROP", "0" * 14, "0" * 19])
def test_require_id_rejects_bad_ids(bad):
    with pytest.raises(ToolInputError):
        tools.require_id(bad, "account_id")


def test_require_id_accepts_15_and_18_characters():
    assert tools.require_id("a" * 15, "x") == "a" * 15
    assert tools.require_id(ACCOUNT_ID, "x") == ACCOUNT_ID


# ---- find_account ------------------------------------------------------------------------


def test_find_account_by_name_builds_expected_query():
    client = FakeQueryClient([("FROM Account", [account_row()])])
    result = tools.find_account(client, name="Harbor", limit=5, today=TODAY)
    soql = client.queries[0]
    assert "Name LIKE '%Harbor%'" in soql
    assert soql.endswith("ORDER BY Name LIMIT 6")  # one extra row to detect "more available"
    assert result["ok"] and result["count"] == 1 and not result["more_available"]
    assert result["accounts"][0]["units"] == 120
    assert result["accounts"][0]["location"] == "Dallas, TX"


def test_find_account_filters_combine_with_and():
    client = FakeQueryClient()
    tools.find_account(
        client, bdr_status="Resting", account_tier=2, portfolio_type="Mixed",
        min_units=100, max_units=300, renewing_within_days=60, today=TODAY,
    )  # fmt: skip
    soql = client.queries[0]
    assert "BDR_Status__c = 'Resting'" in soql
    assert "Account_Tier__c = '2'" in soql
    assert "Portfolio_Type__c = 'Mixed'" in soql
    assert "Units__c >= 100" in soql and "Units__c <= 300" in soql
    assert "Contract_Renewal_Date__c >= 2026-10-13" in soql
    assert "Contract_Renewal_Date__c <= 2026-12-12" in soql
    assert soql.count(" AND ") == 6


def test_find_account_reports_more_available():
    rows = [account_row(Id=f"{i:018d}") for i in range(4)]
    result = tools.find_account(FakeQueryClient([("FROM Account", rows)]), limit=3)
    assert result["count"] == 3 and result["more_available"] is True
    assert len(result["accounts"]) == 3


def test_find_account_rejects_unknown_picklist_values():
    for kwargs in ({"bdr_status": "Hot"}, {"account_tier": "9"}, {"portfolio_type": "Other"}):
        with pytest.raises(ToolInputError):
            tools.find_account(FakeQueryClient(), **kwargs)


def test_find_account_limit_is_clamped():
    client = FakeQueryClient()
    tools.find_account(client, limit=10_000)
    assert client.queries[0].endswith(f"LIMIT {tools.MAX_LIMIT + 1}")
    tools.find_account(client, limit=-5)
    assert client.queries[1].endswith("LIMIT 2")


# ---- summarize_account -------------------------------------------------------------------


def summary_client(account=None, opps=None, tasks=None):
    return FakeQueryClient(
        [
            ("FROM Account WHERE Id", [account or account_row()]),
            (
                "FROM Contact",
                [{"Id": CONTACT_ID, "Name": "Maria Alvarez", "Title": "Owner",
                  "Email": "m@x.example", "Phone": "(214) 555-0100"}],
            ),
            ("FROM Opportunity", opps or []),
            ("FROM Task", tasks or []),
        ]
    )


def test_summarize_account_assembles_everything():
    opps = [
        {"Id": "006000000000001AAA", "Name": "Deal A", "StageName": "Demo", "Amount": 12000.0,
         "CloseDate": "2026-11-01", "IsClosed": False, "IsWon": False},
        {"Id": "006000000000002AAA", "Name": "Old Pilot", "StageName": "Closed Won", "Amount": 3000.0,
         "CloseDate": "2026-03-01", "IsClosed": True, "IsWon": True},
        {"Id": "006000000000003AAA", "Name": "Earlier", "StageName": "Closed Lost", "Amount": 9000.0,
         "CloseDate": "2026-02-01", "IsClosed": True, "IsWon": False},
    ]  # fmt: skip
    tasks = [
        {"Subject": "Call - Follow-up", "ActivityDate": "2026-10-10", "Status": "Completed",
         "Description": "x" * 500, "Who": {"Name": "Maria Alvarez"}},
    ]  # fmt: skip
    result = tools.summarize_account(summary_client(opps=opps, tasks=tasks), ACCOUNT_ID, today=TODAY)
    assert result["ok"]
    assert result["account"]["name"] == "Harbor Realty Group"
    assert result["contacts"][0]["name"] == "Maria Alvarez"
    assert [d["name"] for d in result["opportunities"]["open"]] == ["Deal A"]
    assert result["opportunities"]["open_amount"] == 12000.0
    assert result["opportunities"]["closed_won"] == 1 and result["opportunities"]["closed_lost"] == 1
    assert len(result["recent_activity"][0]["notes"]) == 300  # long notes are trimmed
    assert result["recent_activity"][0]["contact"] == "Maria Alvarez"


def test_summarize_account_unknown_id():
    client = FakeQueryClient([("FROM Account WHERE Id", [])])
    with pytest.raises(ToolInputError):
        tools.summarize_account(client, ACCOUNT_ID, today=TODAY)


def test_summarize_account_rejects_bad_id_before_querying():
    client = FakeQueryClient()
    with pytest.raises(ToolInputError):
        tools.summarize_account(client, "not-an-id")
    assert client.queries == []


def view(**overrides):
    base = {"contract_renewal_date": None, "re_engage_date": None, "bdr_status": "In Flow",
            "rehome_reason": None}  # fmt: skip
    base.update(overrides)
    return base


def test_signal_renewal_soon_and_passed():
    soon = tools.build_signals(view(contract_renewal_date="2026-11-20"), [], RECENT, TODAY)
    assert any("renews in 38 days" in s for s in soon)
    passed = tools.build_signals(view(contract_renewal_date="2026-09-01"), [], RECENT, TODAY)
    assert any("has passed" in s for s in passed)
    far = tools.build_signals(view(contract_renewal_date="2027-08-01"), [], RECENT, TODAY)
    assert far == []


def test_signal_activity_gaps():
    none = tools.build_signals(view(), [], [], TODAY)
    assert "No activity has been logged on this account." in none
    stale = tools.build_signals(view(), [], [{"date": "2026-08-01"}], TODAY)
    assert any("No activity logged in 73 days" in s for s in stale)
    fresh = tools.build_signals(view(), [], [{"date": "2026-10-01"}], TODAY)
    assert fresh == []


def test_signal_re_engage_and_rehome():
    due = tools.build_signals(view(re_engage_date="2026-10-01"), [], RECENT, TODAY)
    assert any("Re-engage date reached" in s for s in due)
    resting = tools.build_signals(view(re_engage_date="2026-12-01"), [], RECENT, TODAY)
    assert "Resting until 2026-12-01." in resting
    rehome = tools.build_signals(
        view(bdr_status="Rehome", rehome_reason="Out of Range"), [], [{"date": "2026-10-10"}], TODAY
    )
    assert "Marked for rehome (Out of Range)." in rehome


def test_signal_open_deal_problems():
    late = [{"name": "Deal A", "close_date": "2026-09-30"}]
    flags = tools.build_signals(view(), late, [{"date": "2026-10-10"}], TODAY)
    assert any("past its close date" in s for s in flags)
    missing = tools.build_signals(view(bdr_status="Active Opp"), [], RECENT, TODAY)
    assert any("no open opportunity" in s for s in missing)


# ---- list_open_deals ---------------------------------------------------------------------


def deal(i, stage, amount, close="2026-11-01"):
    return {"Id": f"{i:018d}", "Name": f"Deal {i}", "AccountId": ACCOUNT_ID,
            "Account": {"Name": "Harbor Realty Group"}, "StageName": stage, "Amount": amount,
            "CloseDate": close, "NextStep": "Call back"}  # fmt: skip


def test_list_open_deals_totals_and_grouping():
    rows = [deal(1, "Demo", 1000.0), deal(2, "Demo", 500.0), deal(3, "Meeting Booked", None)]
    client = FakeQueryClient([("FROM Opportunity", rows)])
    result = tools.list_open_deals(client)
    assert result["count"] == 3
    assert result["total_amount"] == 1500.0
    assert result["by_stage"] == {"Demo": 2, "Meeting Booked": 1}
    assert result["deals"][0]["account_name"] == "Harbor Realty Group"
    assert "IsClosed = false" in client.queries[0]
    assert "ORDER BY CloseDate ASC" in client.queries[0]


def test_list_open_deals_filters():
    client = FakeQueryClient()
    tools.list_open_deals(client, account_id=ACCOUNT_ID, stage="Demo", limit=3)
    soql = client.queries[0]
    assert f"AccountId = '{ACCOUNT_ID}'" in soql and "StageName = 'Demo'" in soql
    assert soql.endswith("LIMIT 4")


def test_list_open_deals_rejects_bad_account_id():
    with pytest.raises(ToolInputError):
        tools.list_open_deals(FakeQueryClient(), account_id="bad")


# ---- log_note ----------------------------------------------------------------------------


def note_client(account=None, contact_ok=True):
    return FakeQueryClient(
        [
            ("FROM Account WHERE Id", [account or {"Id": ACCOUNT_ID, "Name": "Harbor Realty Group",
                                                    "AccountNumber": "RELAY-0001"}]),
            ("FROM Contact WHERE Id", [{"Id": CONTACT_ID}] if contact_ok else []),
        ]
    )  # fmt: skip


def test_log_note_creates_completed_task_with_footer():
    client = note_client()
    result = tools.log_note(client, ACCOUNT_ID, " Call - Follow-up ", " Spoke with owner. ", today=TODAY)
    assert result["ok"] and result["task_id"] and result["account_name"] == "Harbor Realty Group"
    sobject, data = client.created[0]
    assert sobject == "Task"
    assert data["Subject"] == "Call - Follow-up"
    assert data["Description"].startswith("Spoke with owner.") and "Logged via Relay" in data["Description"]
    assert data["Status"] == "Completed" and data["ActivityDate"] == "2026-10-13"
    assert data["WhatId"] == ACCOUNT_ID and "WhoId" not in data


def test_log_note_with_contact_checks_ownership():
    client = note_client()
    tools.log_note(client, ACCOUNT_ID, "Call", "Notes", contact_id=CONTACT_ID, today=TODAY)
    assert client.created[0][1]["WhoId"] == CONTACT_ID

    bad = note_client(contact_ok=False)
    with pytest.raises(ToolInputError):
        tools.log_note(bad, ACCOUNT_ID, "Call", "Notes", contact_id=CONTACT_ID)
    assert bad.created == []


def test_log_note_refuses_non_demo_accounts():
    real = {"Id": ACCOUNT_ID, "Name": "Burlington Textiles", "AccountNumber": "CD656092"}
    client = note_client(account=real)
    with pytest.raises(ToolInputError, match="demo accounts"):
        tools.log_note(client, ACCOUNT_ID, "Call", "Notes")
    assert client.created == []

    no_number = {"Id": ACCOUNT_ID, "Name": "X", "AccountNumber": None}
    with pytest.raises(ToolInputError):
        tools.log_note(note_client(account=no_number), ACCOUNT_ID, "Call", "Notes")


@pytest.mark.parametrize(
    ("subject", "notes"),
    [("", "n"), ("   ", "n"), ("s", ""), ("s", "   "), ("s" * 201, "n"), ("s", "n" * 4001)],
)
def test_log_note_validates_text(subject, notes):
    client = note_client()
    with pytest.raises(ToolInputError):
        tools.log_note(client, ACCOUNT_ID, subject, notes)
    assert client.created == []


def test_log_note_unknown_account():
    client = FakeQueryClient([("FROM Account WHERE Id", [])])
    with pytest.raises(ToolInputError):
        tools.log_note(client, ACCOUNT_ID, "Call", "Notes")
