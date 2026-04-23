---
name: lead-routing
description: >
  GWC Lead Maturity Automation — Phase 2 routing skill.
  Queries leads_maturity.csv for NO_ACTION unrouted leads, looks up the destination country
  in country_rep_mapping.csv, sends a GWC-branded notification to the assigned sales rep
  via Teams DMs, updates the lead record with rep assignment and routed_at timestamp
  (status stays NO_ACTION until Phase 3 detects the rep's first reply), and logs all activity. Trigger when the user says: "route new leads", "assign leads to reps",
  "notify sales team", "send lead notifications", "route today's leads", or any variation
  of wanting to assign and notify reps about new freight leads. Also trigger if the user asks
  to run Phase 2 of the lead automation pipeline.
---

# Lead Routing Skill

Reads unrouted `NO_ACTION` leads from `leads_maturity.csv`, looks up sales reps by destination
country, sends GWC-branded notification emails via Teams DMs, and updates the CSV with assignment info.

## Prerequisites & paths
Replace `<workspace>` with the actual path to the workspace folder (the one selected by the user)
```
DATA_DIR  = WORKSPACE/data/
SCRIPTS   = WORKSPACE/skills/lead-routing/scripts/
  teams_templates.py  — GWC-branded Adaptive Card builder (QUALIFIED + PARTIAL + unroutable)
  route_lead.py       — country normalisation, rep lookup, routing decisions

SHARED_SCRIPTS = WORKSPACE/skills/lead-ingestion/scripts/
  csv_store.py        — CSV read/write (shared with lead-ingestion)
```

## Connector

**teams-composio** — used to send 1:1 Teams DMs to reps and the manager.

## Step-by-step execution

### Step 1 — Find unrouted NO_ACTION leads
Replace `<workspace>` with the actual path to the workspace folder (the one selected by the user)
```python
import sys
sys.path.insert(0, f"{WORKSPACE}/skills/lead-ingestion/scripts")
sys.path.insert(0, f"{WORKSPACE}/skills/lead-routing/scripts")

from csv_store import CSVStore
from route_lead import get_unrouted_leads, get_routing_decision, MANAGER_EMAIL

store = CSVStore(f"{WORKSPACE}/data")
unrouted = get_unrouted_leads(store)
print(f"Found {len(unrouted)} unrouted NO_ACTION lead(s)")

```

If `len(unrouted) == 0`, report: "No leads to route. All NO_ACTION leads already have assigned reps." and stop.

### Step 2 — For each lead, determine routing

```python
from route_lead import get_routing_decision

for lead in unrouted:
    decision = get_routing_decision(lead, store)
    # decision["routable"]         → True/False
    # decision["notify_reps"] → 1–2 primary reps who receive Teams DMs
    # decision["all_reps"]    → full team for the country, tracked in log only
    # decision["manager_email"] → country manager (or global fallback)

    notifications_to_send = []   # list of (to_email, rep_name, card)

    if not decision["routable"]:
        title, card = build_routing_card_unroutable(lead, decision.get("manager_email", MANAGER_EMAIL), missing_fields)
        notifications_to_send.append((decision.get("manager_email", MANAGER_EMAIL), "Manager", card))
    else:
        for rep in decision["notify_reps"]:   # max 2 iterations
            if lead.get("classification") == "QUALIFIED":
                title, card = build_routing_card_qualified(lead, rep["rep_name"])
            else:
                title, card = build_routing_card_partial(lead, rep["rep_name"], missing_fields)
            notifications_to_send.append((rep["rep_email"], rep["rep_name"], card))
```

### Step 3 — Build the notification email

```python
from teams_templates import (
    build_routing_card_qualified,
    build_routing_card_partial,
    build_routing_card_unroutable,
    card_to_attachment,
)
import json

classification = lead.get("classification", "")
missing_raw = lead.get("missing_fields", "[]")
try:
    missing_fields = json.loads(missing_raw) if missing_raw else []
except Exception:
    missing_fields = []

if not decision["routable"]:
    # Unroutable → notify manager via Teams DM
    title, card = build_routing_card_unroutable(lead, MANAGER_EMAIL, missing_fields)
    to_email = MANAGER_EMAIL
    rep_name = "Manager"
else:
    rep = decision["primary_rep"]
    to_email = rep["rep_email"]
    rep_name = rep["rep_name"]

    if classification == "QUALIFIED":
        title, card = build_routing_card_qualified(lead, rep_name)
    else:  # PARTIALLY_QUALIFIED
        title, card = build_routing_card_partial(lead, rep_name, missing_fields)
```

### Step 4 — Send via Teams 1:1 DM

Use the `teams-composio` connector. Two calls per lead:

**4a — Create or retrieve the 1:1 chat:**
```python
# to_email is the rep's email (or MANAGER_EMAIL if unroutable)
# This is idempotent — if the chat already exists Teams returns the existing chat_id
# Send Teams DM to each notified rep (max 2) — NOT to all tracked reps
for (to_email, rep_name, card) in notifications_to_send:
    chat = MICROSOFT_TEAMS_TEAMS_CREATE_CHAT(
        chatType="oneOnOne",
        members=[to_email]
    )
    chat_id = chat["id"]
    attachment = card_to_attachment(card)
    MICROSOFT_TEAMS_TEAMS_POST_CHAT_MESSAGE(
        chat_id=chat_id,
        body={"contentType": "html", "content": f"<attachment id='{attachment['id']}'></attachment>"},
        attachments=[attachment]
    )

# Reps in decision["all_reps"] who are NOT in notify_reps are tracked only — no DM sent
tracked_only = [
    r["rep_email"] for r in decision.get("all_reps", [])
    if r["rep_email"] not in [n[0] for n in notifications_to_send]
]
```

**4b — Build the attachment and post the Adaptive Card:**
```python
attachment = card_to_attachment(card)

MICROSOFT_TEAMS_TEAMS_POST_CHAT_MESSAGE(
    chat_id=chat_id,
    body={
        "contentType": "html",
        "content": f"<attachment id='{attachment['id']}'></attachment>"
    },
    attachments=[attachment]
)
```

- Only the rep (or manager) receives the DM — no broadcast to channels.
- After sending, note the chat_id for the activity log.

### Step 5 — Update leads_maturity.csv

```python
from datetime import datetime

now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
gwc_id = lead["gwc_id"]

if decision["routable"]:
    rep = decision["primary_rep"]
    import json

    # Do NOT set current_status to ENGAGED here.
    # ENGAGED is only set by Phase 3 (lead-status-tracker) once the rep sends
    # at least one actual email to the customer.
    # routed_at is stored so Phase 4 cadence can count days since notification.
    store.update_lead_field(gwc_id, {
        "assigned_rep_email":  rep["rep_email"],
        "assigned_rep_name":   rep["rep_name"],
        "assigned_country":    decision["canonical_country"],
        "routed_at":           now_iso,
    })
else:
    # Unroutable — keep NO_ACTION, just note it was flagged
    store.update_lead_field(gwc_id, {
        "assigned_country": decision.get("canonical_country", ""),
        "notes": lead.get("notes", "") + f" | UNROUTABLE: {decision['unroutable_reason']}",
    })
```

### Step 6 — Log activity

```python
activity_type = "REP_NOTIFIED" if decision["routable"] else "ESCALATION"
store.log_activity(
    gwc_id=gwc_id,
    activity_type=activity_type,
    detail={
        "action": "routing_teams_dm_sent" if decision["routable"] else "unroutable_alert_sent",
        "notified_reps":  [n[1] for n in notifications_to_send],   # names of DM recipients
        "notified_emails": [n[0] for n in notifications_to_send],  # emails of DM recipients
        "tracked_reps":   [r["rep_email"] for r in decision.get("all_reps", [])],  # all CC watchers
        "classification":  classification,
        "canonical_country": decision.get("canonical_country", ""),
        "unroutable_reason": decision.get("unroutable_reason", ""),
        "in_quip_sheet":  lead.get("in_quip_sheet", "NO"),
    },
    performed_by="SYSTEM",
)
```

### Step 7 — Print summary

After processing all leads:

```
✅ Lead Routing Complete
─────────────────────────────────────────
Leads processed:   3
  Routed:          2  (status remains NO_ACTION — awaiting first rep reply)
  Unroutable:      1

Routing breakdown:
  GWC-754316484833 → [rep name] (rep@gwclogistics.com) — PARTIALLY_QUALIFIED ✓
  GWC-123456789012 → [rep name] (rep@gwclogistics.com) — QUALIFIED ✓
  GWC-999999999999 → UNROUTABLE (no rep for 'India') — manager notified

leads_maturity.csv updated ✓
Activity logged ✓
```

## Email rules (enforced in templates)

All notification emails instruct the rep to:
1. **Include the GWC ID in every email subject line** to the customer
2. **CC `Sales.rfq@gwclogistics.com`** on every email to the customer
3. Not reply to the automated notification email

## Routing logic

| Classification | Destination rep found | Action |
|---|---|---|
| QUALIFIED | Yes | Email primary rep: "Please quote" |
| PARTIALLY_QUALIFIED | Yes | Email primary rep: "Collect missing fields, then quote" |
| QUALIFIED / PARTIAL | No | Email MANAGER_EMAIL: "Manual assignment needed" |
| REJECTED | Any | Skip — REJECTED leads are never routed |
| Already routed | Any | Skip — `assigned_rep_email` already set |

## Country normalisation

The `route_lead.py` script handles common country name variants:
- "Saudi Arabia", "KSA", "Riyadh" → routes to KSA & Bahrain reps
- "UAE", "Dubai", "United Arab Emirates" → routes to UAE reps
- "Doha", "State of Qatar" → routes to Qatar reps
- "Muscat", "Sultanate of Oman" → routes to Oman reps
- Countries not in this list → UNROUTABLE, manager alerted

## Script reference

| Script | Key functions |
|--------|--------------|
| `scripts/route_lead.py` | `get_unrouted_leads(store)`, `get_routing_decision(lead, store)` |
| `scripts/teams_templates.py` | `build_routing_card_qualified(lead, rep_name)`, `build_routing_card_partial(lead, rep_name, missing)`, `build_routing_card_unroutable(lead, manager_email)`, `card_to_attachment(card)` |
| `../lead-ingestion/scripts/csv_store.py` | `CSVStore(data_dir)`, `.update_lead_field()`, `.log_activity()` |
