"""Values shared by the seed script and the MCP tools.

The picklist values here must match what you created in Salesforce (see README, "Salesforce setup").
"""

# Every record the seed script creates is tagged by giving the Account an AccountNumber that
# starts with this prefix. It is how we find (and safely delete) demo data, and how the write
# tool knows an account is a demo account.
DEMO_PREFIX = "RELAY-"

PORTFOLIO_TYPES = ("Fee Managed", "Owner Operator", "Mixed")
ACCOUNT_TIERS = ("1", "2", "3", "4")
BDR_STATUSES = ("New", "In Flow", "Resting", "Rehome", "Active Opp")
REHOME_REASONS = ("Invalid", "Out of Range", "Reassign")

OPEN_DEAL_STAGES = ("Meeting Booked", "Meeting Held", "Demo")
CLOSED_WON_STAGE = "Closed Won"
CLOSED_LOST_STAGE = "Closed Lost"

# Custom Account fields (API names) that Relay reads. Order matters only for readability.
ACCOUNT_FIELDS = (
    "Id",
    "Name",
    "AccountNumber",
    "Site",
    "Units__c",
    "Portfolio_Type__c",
    "Current_Software__c",
    "Contract_Renewal_Date__c",
    "Account_Tier__c",
    "BDR_Status__c",
    "Re_engage_Date__c",
    "Rehome_Reason__c",
)
