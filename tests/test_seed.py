import re
from datetime import date

import pytest

from relay import constants
from relay.seed import SeedError, find_demo_account_ids, run_seed
from relay.seed_data import build_demo_data, summarize
from tests.fakes import FakeOrg

TODAY = date(2026, 10, 13)


# ---- the generated data ------------------------------------------------------------------


def test_default_data_matches_the_plan():
    data = build_demo_data(today=TODAY)
    plan = summarize(data)
    assert plan["accounts"] == 50
    assert plan["by_status"] == {"New": 10, "In Flow": 15, "Resting": 8, "Rehome": 5, "Active Opp": 12}
    assert plan["contacts"] >= 50 and plan["tasks"] > 100


def test_data_is_repeatable():
    first = build_demo_data(today=TODAY)
    second = build_demo_data(today=TODAY)
    assert [a.fields for a in first] == [a.fields for a in second]


def test_account_fields_are_valid_and_consistent():
    names, numbers = set(), set()
    for a in build_demo_data(today=TODAY):
        f = a.fields
        assert f["AccountNumber"].startswith(constants.DEMO_PREFIX)
        names.add(f["Name"])
        numbers.add(f["AccountNumber"])
        assert 40 <= f["Units__c"] <= 500
        assert f["Portfolio_Type__c"] in constants.PORTFOLIO_TYPES
        assert f["BDR_Status__c"] in constants.BDR_STATUSES
        assert f["Account_Tier__c"] in constants.ACCOUNT_TIERS
        units = f["Units__c"]
        expected_tier = "1" if units >= 300 else "2" if units >= 150 else "3" if units >= 75 else "4"
        assert f["Account_Tier__c"] == expected_tier
        assert f["Website"].endswith(".example")
    assert len(names) == 50 and len(numbers) == 50


def test_status_specific_fields():
    for a in build_demo_data(today=TODAY):
        f = a.fields
        status = f["BDR_Status__c"]
        if status == "Resting":
            assert date.fromisoformat(f["Re_engage_Date__c"]) > TODAY
        else:
            assert "Re_engage_Date__c" not in f
        if status == "Rehome":
            assert f["Rehome_Reason__c"] in constants.REHOME_REASONS
        else:
            assert "Rehome_Reason__c" not in f
        open_stages = [o for o in a.opportunities if o["StageName"] in constants.OPEN_DEAL_STAGES]
        assert len(open_stages) == (1 if status == "Active Opp" else 0)
        if status == "New":
            assert len(a.tasks) <= 1


def test_people_and_dates_are_fictional_and_sensible():
    for a in build_demo_data(today=TODAY):
        assert 1 <= len(a.contacts) <= 3
        for c in a.contacts:
            assert c["Email"].endswith(".example")
            assert re.fullmatch(r"\(\d{3}\) 555-01\d\d", c["Phone"])
        for t in a.tasks:
            assert 0 <= t["contact_index"] < len(a.contacts)
            assert 2 <= (TODAY - date.fromisoformat(t["ActivityDate"])).days <= 90
            assert t["Status"] == "Completed"
        dates = [t["ActivityDate"] for t in a.tasks]
        assert dates == sorted(dates)  # oldest first
        for o in a.opportunities:
            closed = o["StageName"] in (constants.CLOSED_WON_STAGE, constants.CLOSED_LOST_STAGE)
            close = date.fromisoformat(o["CloseDate"])
            assert (close < TODAY) == closed
            assert o["Amount"] > 0


def test_count_limits():
    assert len(build_demo_data(today=TODAY, count=5)) == 5
    with pytest.raises(ValueError):
        build_demo_data(today=TODAY, count=10_000)


# ---- running the seed against a simulated org --------------------------------------------


def run(org, **kwargs):
    lines: list[str] = []
    run_seed(org, say=lines.append, **kwargs)
    return lines


def test_dry_run_changes_nothing():
    org = FakeOrg()
    lines = run(org, dry_run=True)
    assert any("Would create 50 accounts" in line for line in lines)
    assert all(not store for store in org.records.values())


def test_seed_creates_linked_records():
    org = FakeOrg()
    run(org)
    plan = summarize(build_demo_data())
    assert len(org.records["Account"]) == plan["accounts"]
    assert len(org.records["Contact"]) == plan["contacts"]
    assert len(org.records["Opportunity"]) == plan["opportunities"]
    assert len(org.records["Task"]) == plan["tasks"]

    accounts = org.records["Account"]
    for contact in org.records["Contact"].values():
        assert contact["AccountId"] in accounts
    for opp in org.records["Opportunity"].values():
        assert opp["AccountId"] in accounts
    for task in org.records["Task"].values():
        assert task["WhatId"] in accounts
        contact = org.records["Contact"][task["WhoId"]]
        assert contact["AccountId"] == task["WhatId"]  # contact belongs to that account
        assert "contact_index" not in task


def test_seed_refuses_to_duplicate():
    org = FakeOrg()
    run(org)
    with pytest.raises(SeedError, match="already exists"):
        run(org)
    assert len(org.records["Account"]) == 50


def test_reset_replaces_demo_data_and_deletes_children_first():
    org = FakeOrg()
    run(org)
    first_ids = set(org.records["Account"])
    org.delete_order.clear()
    run(org, reset=True, assume_yes=True)
    assert set(org.records["Account"]).isdisjoint(first_ids)
    assert len(org.records["Account"]) == 50
    order = org.delete_order
    child_positions = [i for i, s in enumerate(order) if s in ("Task", "Opportunity", "Contact")]
    assert order.index("Account") > max(child_positions)


def test_clean_removes_only_demo_records():
    org = FakeOrg()
    org.records["Account"]["001REAL00000000AAA"] = {
        "Id": "001REAL00000000AAA",
        "Name": "Sample",
        "AccountNumber": "CD123",
    }
    run(org)
    assert len(find_demo_account_ids(org)) == 50
    run(org, clean=True, assume_yes=True)
    assert list(org.records["Account"]) == ["001REAL00000000AAA"]
    assert not org.records["Contact"] and not org.records["Task"] and not org.records["Opportunity"]


def test_delete_asks_first_and_cancel_deletes_nothing():
    org = FakeOrg()
    run(org)
    prompts: list[str] = []

    def say_no(message: str) -> bool:
        prompts.append(message)
        return False

    with pytest.raises(SeedError, match="Cancelled"):
        run_seed(org, reset=True, confirm=say_no, say=lambda _: None)
    assert prompts and "50 demo accounts" in prompts[0]
    assert len(org.records["Account"]) == 50


def test_refuses_non_developer_org_unless_overridden():
    org = FakeOrg(org_type="Enterprise Edition")
    with pytest.raises(SeedError, match="not a Developer Edition"):
        run(org)
    assert not org.records["Account"]
    run(org, allow_non_dev=True)
    assert len(org.records["Account"]) == 50


def test_stops_with_clear_message_when_salesforce_refuses_a_record():
    org = FakeOrg()
    org.refuse = ("Contact", "Email")
    with pytest.raises(SeedError, match="refused a Contact"):
        run(org)
    assert len(org.records["Account"]) == 50  # accounts exist, so --clean is needed
    assert not org.records["Task"]  # nothing further was created
    org.refuse = None
    run(org, clean=True, assume_yes=True)
    assert not org.records["Account"]
