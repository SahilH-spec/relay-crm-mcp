"""Checks that Salesforce is reachable and set up the way Relay expects.

Run from the project folder:  uv run relay-check
"""

from __future__ import annotations

import sys
from typing import Any

from relay.config import ConfigError
from relay.salesforce import SalesforceAuthError, SalesforceClient

# API names Salesforce generates from the field labels in the setup guide.
EXPECTED_ACCOUNT_FIELDS = [
    "Units__c",
    "Portfolio_Type__c",
    "Current_Software__c",
    "Contract_Renewal_Date__c",
    "Account_Tier__c",
    "BDR_Status__c",
    "Re_engage_Date__c",
    "Rehome_Reason__c",
]
EXPECTED_STAGES = ["Meeting Booked", "Meeting Held", "Demo", "Closed Won", "Closed Lost"]


def run_checks(client: SalesforceClient) -> dict[str, Any]:
    """Return a plain summary of the org and anything still missing from setup."""
    org = client.org_info()
    usage = client.api_usage()
    account_fields = client.field_names("Account")
    stages = client.picklist_values("Opportunity", "StageName")
    return {
        "org_name": org.get("Name"),
        "org_type": org.get("OrganizationType"),
        "api_calls_remaining_today": usage["remaining"],
        "api_calls_max_today": usage["max"],
        "missing_account_fields": [f for f in EXPECTED_ACCOUNT_FIELDS if f not in account_fields],
        "missing_opportunity_stages": [s for s in EXPECTED_STAGES if s not in stages],
    }


def main() -> int:
    # Plain ASCII output: the default Windows console can choke on symbols like check marks.
    try:
        client = SalesforceClient()
        print(f"[..] Logging in to {client.settings.sf_domain}")
        result = run_checks(client)
    except ConfigError as exc:
        print(f"[!!] Settings problem: {exc}")
        return 1
    except SalesforceAuthError as exc:
        print(f"[!!] Login failed: {exc}")
        print("     Check the key/secret, the Run As user, and wait a few minutes after app changes.")
        return 1

    print(f"[ok] Connected to '{result['org_name']}' ({result['org_type']})")
    print(
        f"[ok] API calls left today: {result['api_calls_remaining_today']:,}"
        f" of {result['api_calls_max_today']:,}"
    )

    ok = True
    if result["missing_account_fields"]:
        ok = False
        print("[!!] Account fields not found: " + ", ".join(result["missing_account_fields"]))
        print("     Check the field labels, or look up the API names in Object Manager.")
    else:
        print("[ok] All Account custom fields found")

    if result["missing_opportunity_stages"]:
        ok = False
        print("[!!] Opportunity stages missing: " + ", ".join(result["missing_opportunity_stages"]))
    else:
        print("[ok] All Opportunity stages found")

    print("\nReady for Phase 2." if ok else "\nConnection works. Fix the items above, then rerun.")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
