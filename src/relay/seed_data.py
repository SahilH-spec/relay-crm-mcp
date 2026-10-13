"""Builds the fictional demo data. Pure Python: no Salesforce calls, so it is easy to test.

Everything here is invented. Company and people names come from word lists, phone numbers use
the fictional 555-01xx range, emails use the reserved .example domain, and the "current
software" values are generic descriptions rather than real products. Dates are relative to the
day the data is built, so the demo always looks current. The same seed gives the same data.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from relay.constants import (
    BDR_STATUSES,
    CLOSED_LOST_STAGE,
    CLOSED_WON_STAGE,
    DEMO_PREFIX,
    OPEN_DEAL_STAGES,
    PORTFOLIO_TYPES,
    REHOME_REASONS,
)

DEFAULT_COUNT = 50
DEFAULT_SEED = 42

NAME_PREFIXES = [
    "Harbor", "Cedar", "Summit", "Lone Star", "Maple", "Ironwood", "Bluebonnet", "Riverbend",
    "Oakline", "Granite", "Willow", "Redwood", "Sterling", "Prairie", "Lakeshore", "Pinecrest",
    "Foxglove", "Copper", "Magnolia", "Brightwater", "Stonegate", "Juniper", "Meridian",
    "Northgate", "Silverleaf", "Eastfield", "Westbrook", "Highland", "Clearwater", "Amberly",
    "Tidewater", "Ridgeway", "Hollis", "Larkspur", "Kestrel", "Bramble", "Quarry", "Windmere",
    "Aspen", "Cobalt",
]  # fmt: skip
NAME_SUFFIXES = [
    "Property Management", "Realty Group", "Residential", "Properties",
    "Management Co.", "Rentals", "Asset Partners", "Property Group",
]  # fmt: skip
LOCATIONS = [
    ("Dallas", "TX"), ("Plano", "TX"), ("Fort Worth", "TX"), ("Austin", "TX"), ("Houston", "TX"),
    ("San Antonio", "TX"), ("Denver", "CO"), ("Phoenix", "AZ"), ("Atlanta", "GA"),
    ("Charlotte", "NC"), ("Raleigh", "NC"), ("Nashville", "TN"), ("Tampa", "FL"),
    ("Orlando", "FL"), ("Columbus", "OH"), ("Kansas City", "MO"), ("Salt Lake City", "UT"),
    ("Las Vegas", "NV"), ("Indianapolis", "IN"), ("Minneapolis", "MN"),
]  # fmt: skip
SOFTWARE = [  # generic descriptions on purpose; edit freely
    ("Spreadsheets only", 22),
    ("Accounting software + spreadsheets", 22),
    ("Legacy desktop software", 20),
    ("Basic online tool", 16),
    ("Mid-market suite", 14),
    ("No system", 6),
]
FIRST_NAMES = [
    "Maria", "James", "Priya", "Daniel", "Aisha", "Robert", "Mei", "Carlos", "Fatima", "Thomas",
    "Elena", "Marcus", "Hannah", "Omar", "Grace", "Victor", "Sofia", "Andre", "Linh", "Patrick",
    "Naomi", "Ethan", "Camila", "Raj", "Olivia", "Tariq", "Jasmine", "Henry", "Ana", "Kevin",
]  # fmt: skip
LAST_NAMES = [
    "Alvarez", "Brooks", "Chen", "Dawson", "Embry", "Foster", "Garza", "Hughes", "Ibrahim",
    "Jensen", "Kapoor", "Lawson", "Mendez", "Nguyen", "Okafor", "Patel", "Quinn", "Reyes",
    "Sutton", "Tran", "Underwood", "Vargas", "Whitaker", "Xu", "Young", "Zimmerman", "Abbott",
    "Bishop", "Castillo", "Delgado",
]  # fmt: skip
TITLES = [
    "Owner", "Principal Broker", "Operations Manager", "Property Manager",
    "Director of Operations", "Portfolio Manager", "Controller", "Leasing Manager",
]  # fmt: skip

CALL_TEMPLATES = {
    "intro": (
        "Call - Intro / discovery",
        (
            "Spoke with {first}. Currently on {software}. Manages about {units} units. "
            "Open to a short follow-up."
        ),
    ),
    "voicemail": ("Call - Voicemail", "Left a voicemail for {first}. Will try again this week."),
    "gatekeeper": (
        "Call - Gatekeeper",
        "Front desk confirmed {first} owns software decisions. Best time to call is mornings.",
    ),
    "followup": (
        "Call - Follow-up",
        "Followed up with {first}. Pain points: manual rent collection and owner reporting.",
    ),
    "renewal": (
        "Call - Objection: contract timing",
        "{first} said the current contract renews around {renewal}. Revisit before then.",
    ),
    "demo": (
        "Call - Demo scheduling",
        "Confirmed a time for a walkthrough with {first} and one teammate.",
    ),
    "notnow": (
        "Call - Not interested right now",
        "{first} is happy with the current setup and asked us to check back later.",
    ),
}

# How each BDR status shapes the history: (min calls, max calls, call types that fit the story)
STATUS_PLAN: dict[str, tuple[int, int, list[str]]] = {
    "New": (0, 1, ["voicemail", "gatekeeper"]),
    "In Flow": (2, 5, ["intro", "voicemail", "gatekeeper", "followup"]),
    "Resting": (3, 6, ["intro", "followup", "renewal", "notnow"]),
    "Rehome": (2, 4, ["voicemail", "gatekeeper", "intro"]),
    "Active Opp": (3, 7, ["intro", "followup", "demo", "renewal"]),
}
STATUS_WEIGHTS = {"New": 20, "In Flow": 30, "Resting": 15, "Rehome": 10, "Active Opp": 25}


@dataclass
class DemoAccount:
    fields: dict[str, Any]
    contacts: list[dict[str, Any]] = field(default_factory=list)
    opportunities: list[dict[str, Any]] = field(default_factory=list)
    # Each task names which contact (by position in `contacts`) it was with.
    tasks: list[dict[str, Any]] = field(default_factory=list)


def allocate_statuses(rng: random.Random, count: int) -> list[str]:
    """Hand out BDR statuses in fixed proportions (then shuffle) so the demo is always balanced."""
    total = sum(STATUS_WEIGHTS.values())
    shares = {s: count * w / total for s, w in STATUS_WEIGHTS.items()}
    counts = {s: int(v) for s, v in shares.items()}
    # give the leftover accounts to the statuses with the biggest fractional remainder
    leftovers = sorted(shares, key=lambda s: shares[s] - counts[s], reverse=True)
    for status in leftovers[: count - sum(counts.values())]:
        counts[status] += 1
    statuses = [s for s, n in counts.items() for _ in range(n)]
    rng.shuffle(statuses)
    return statuses


def tier_for_units(units: int) -> str:
    if units >= 300:
        return "1"
    if units >= 150:
        return "2"
    if units >= 75:
        return "3"
    return "4"


def pick_units(rng: random.Random) -> int:
    band = rng.choices([(40, 100), (101, 250), (251, 500)], weights=[40, 35, 25])[0]
    return rng.randint(*band)


def slugify(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def build_contacts(rng: random.Random, domain: str, area_code: str) -> list[dict[str, Any]]:
    contacts = []
    used: set[tuple[str, str]] = set()
    for _ in range(rng.choice([1, 2, 2, 3])):
        while True:
            first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
            if (first, last) not in used:
                used.add((first, last))
                break
        contacts.append(
            {
                "FirstName": first,
                "LastName": last,
                "Title": rng.choice(TITLES),
                "Email": f"{first.lower()}.{last.lower()}@{domain}",
                "Phone": f"({area_code}) 555-01{rng.randint(0, 99):02d}",
            }
        )
    return contacts


def build_demo_data(
    today: date | None = None, count: int = DEFAULT_COUNT, seed: int = DEFAULT_SEED
) -> list[DemoAccount]:
    """Create `count` fictional accounts with contacts, opportunities, and call history."""
    today = today or date.today()
    rng = random.Random(seed)
    if count > len(NAME_PREFIXES) * len(NAME_SUFFIXES):
        raise ValueError("count is larger than the number of unique company names available")

    combos = [(p, s) for p in NAME_PREFIXES for s in NAME_SUFFIXES]
    rng.shuffle(combos)
    area_codes = {"TX": "214", "CO": "303", "AZ": "602", "GA": "404", "NC": "704", "TN": "615",
                  "FL": "813", "OH": "614", "MO": "816", "UT": "801", "NV": "702", "IN": "317",
                  "MN": "612"}  # fmt: skip
    software_values = [s for s, _ in SOFTWARE]
    software_weights = [w for _, w in SOFTWARE]
    statuses = allocate_statuses(rng, count)

    accounts: list[DemoAccount] = []
    for index in range(count):
        prefix, suffix = combos[index]
        name = f"{prefix} {suffix}"
        city, state = rng.choice(LOCATIONS)
        units = pick_units(rng)
        status = statuses[index]
        software = rng.choices(software_values, weights=software_weights)[0]
        renewal = (
            today + timedelta(days=rng.randint(10, 420)) if rng.random() < 0.8 else None
        )

        fields: dict[str, Any] = {
            "Name": name,
            "AccountNumber": f"{DEMO_PREFIX}{index + 1:04d}",
            "Site": f"{city}, {state}",
            "Website": f"https://www.{slugify(name)}.example",
            "Units__c": units,
            "Portfolio_Type__c": rng.choices(PORTFOLIO_TYPES, weights=[45, 35, 20])[0],
            "Current_Software__c": software,
            "Account_Tier__c": tier_for_units(units),
            "BDR_Status__c": status,
        }
        if renewal:
            fields["Contract_Renewal_Date__c"] = renewal.isoformat()
        if status == "Resting":
            fields["Re_engage_Date__c"] = (today + timedelta(days=rng.randint(20, 120))).isoformat()
        if status == "Rehome":
            fields["Rehome_Reason__c"] = rng.choice(REHOME_REASONS)

        account = DemoAccount(fields=fields)
        account.contacts = build_contacts(
            rng, f"{slugify(name)}.example", area_codes.get(state, "555")
        )

        # Opportunities: only Active Opp accounts carry an open deal; a few have history too.
        rate = rng.randint(7, 12)  # fictional dollars per unit per month
        value = int(round(units * rate * 12, -2))
        if status == "Active Opp":
            account.opportunities.append(
                {
                    "Name": f"{name} - New Business",
                    "StageName": rng.choice(OPEN_DEAL_STAGES),
                    "Amount": value,
                    "CloseDate": (today + timedelta(days=rng.randint(14, 75))).isoformat(),
                    "NextStep": rng.choice(
                        ["Send recap and pricing", "Schedule technical walkthrough",
                         "Confirm decision makers", "Get owner sign-off"]
                    ),
                }
            )  # fmt: skip
            if rng.random() < 0.2:
                account.opportunities.append(
                    {
                        "Name": f"{name} - Pilot",
                        "StageName": CLOSED_WON_STAGE,
                        "Amount": int(round(value * 0.3, -2)),
                        "CloseDate": (today - timedelta(days=rng.randint(30, 200))).isoformat(),
                    }
                )
        if status in ("Resting", "Rehome") and rng.random() < 0.4:
            account.opportunities.append(
                {
                    "Name": f"{name} - Earlier Evaluation",
                    "StageName": CLOSED_LOST_STAGE,
                    "Amount": value,
                    "CloseDate": (today - timedelta(days=rng.randint(30, 200))).isoformat(),
                }
            )

        # Call history: dates spread over the last ~90 days, oldest first.
        low, high, kinds = STATUS_PLAN[status]
        calls = rng.randint(low, high)
        days_ago = sorted((rng.randint(2, 90) for _ in range(calls)), reverse=True)
        for position, ago in enumerate(days_ago):
            kind = "intro" if position == 0 and "intro" in kinds else rng.choice(kinds)
            subject, body = CALL_TEMPLATES[kind]
            contact_index = rng.randrange(len(account.contacts))
            first = account.contacts[contact_index]["FirstName"]
            account.tasks.append(
                {
                    "contact_index": contact_index,
                    "Subject": subject,
                    "Description": body.format(
                        first=first,
                        software=software.lower(),
                        units=units,
                        renewal=renewal.strftime("%B %Y") if renewal else "an unknown date",
                    ),
                    "Status": "Completed",
                    "Priority": "Normal",
                    "ActivityDate": (today - timedelta(days=ago)).isoformat(),
                }
            )
        accounts.append(account)

    return accounts


def summarize(accounts: list[DemoAccount]) -> dict[str, Any]:
    """Counts for the dry-run printout and the final report."""
    by_status = {s: 0 for s in BDR_STATUSES}
    for a in accounts:
        by_status[a.fields["BDR_Status__c"]] += 1
    return {
        "accounts": len(accounts),
        "contacts": sum(len(a.contacts) for a in accounts),
        "opportunities": sum(len(a.opportunities) for a in accounts),
        "tasks": sum(len(a.tasks) for a in accounts),
        "by_status": by_status,
    }
