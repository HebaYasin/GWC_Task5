---
name: lead-status-tracker
description: >
  GWC Lead Maturity Automation — Phase 3 status tracking skill.
  Scans the Sales.rfq@gwclogistics.com shared mailbox for CC'd email conversations
  linked to active leads (by GWC ID in subject line), uses Claude AI to analyse each
  thread and detect status transitions (NO_ACTION→ENGAGED→QUOTED→FOLLOW_UP→WON_LOSS),
  flags "dark leads" where reps have gone silent for 5+ days, and updates leads_maturity.csv.
  Trigger when the user says: "update lead statuses", "scan for status changes",
  "track lead progress", "check email threads", "what's the status of my leads",
  "find dark leads", "check CC emails", or any variation of wanting to see how leads
  have progressed based on email conversations.
---

# Lead Status Tracker Skill

Scans CC'd email threads in the shared mailbox per active lead, uses Claude AI to
detect status transitions, flags dark leads, and updates the CSV data store.

> ⚠️ **This skill uses Claude AI for email thread analysis** — the only place in the
> system where AI tokens are intentionally spent. Everything else is deterministic Python.

## Prerequisites & paths
Replace `<workspace>` with the actual path to the workspace folder (the one selected by the user)
```
DATA_DIR   = WORKSPACE/data/
SCRIPTS    = WORKSPACE/skills/lead-status-tracker/scripts/
  scan_cc_emails.py     — active lead retrieval, thread payload builder, dark lead check
  analyze_thread.py     — AI prompt builder + response parser + status update builder
  dark_lead_detector.py — dark lead logic and summary

SHARED_SCRIPTS = WORKSPACE/skills/lead-ingestion/scripts/
  csv_store.py          — CSV read/write (shared)

REFERENCES = WORKSPACE/skills/lead-status-tracker/references/
  freight_domain_knowledge.md  —  READ THIS before analysing email threads
                                  Contains: pipeline stage signals, GWC freight vocabulary,
                                  top corridors, common questions, disqualification patterns
```

> **Important**: Before analysing any email thread, read
> `references/freight_domain_knowledge.md` to correctly interpret freight terminology,
> identify quote vs. follow-up signals, and recognise unqualified lead patterns.

## Connector

**outlook-composio** — `OUTLOOK_QUERY_EMAILS` or `OUTLOOK_LIST_MESSAGES` to read the shared mailbox.

## Full execution — step by step

### Step 0 — Detect and backfill orphan (pre-pipeline) leads

Before scanning active leads, sweep the mailbox for emails that reference a GWC ID
**not yet in the tracker**. These are pre-April-8 leads whose original HubSpot email
predates the pipeline start date.

```python
import sys, re
sys.path.insert(0, f"{WORKSPACE}/skills/lead-ingestion/scripts")
sys.path.insert(0, f"{WORKSPACE}/skills/lead-status-tracker/scripts")

from csv_store import CSVStore
from scan_cc_emails import (
    is_orphan_email, build_orphan_stub, extract_gwc_id_from_subject
)
from analyze_thread import build_orphan_analysis_prompt, parse_analysis_result, build_status_update

store = CSVStore(f"{WORKSPACE}/data")
known_ids = store.get_all_gwc_ids()
```

**0a — Fetch recent inbox emails** (broad sweep — no GWC-ID filter):

Use `OUTLOOK_QUERY_EMAILS` with:
- `user_id`: `Sales.rfq@gwclogistics.com`
- `folder`: `inbox`
- `top`: 100
- `orderby`: `receivedDateTime desc`
- No subject filter — we need everything to detect orphans

**0b — Identify orphans:**

```python
orphan_gwc_ids = {}   # gwc_id → list[email]

for email in inbox_emails:
    is_orphan, gwc_id = is_orphan_email(email, known_ids)
    if is_orphan:
        orphan_gwc_ids.setdefault(gwc_id, []).append(email)
```

**0c — For each orphan GWC ID, fetch the full thread:**

Use `OUTLOOK_QUERY_EMAILS` with `filter: contains(subject, '{gwc_id}')` to get all
emails for that GWC ID (same as Step 2 for regular leads).

**0d — Backfill the stub row:**

```python
from datetime import datetime, timezone

now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

for gwc_id, emails in orphan_gwc_ids.items():
    stub = build_orphan_stub(gwc_id, emails, store)
    store.upsert_lead(stub)

    # Log the backfill event
    store.log_activity(
        gwc_id=gwc_id,
        activity_type="PRE_PIPELINE_BACKFILL",
        detail={
            "reason": "GWC ID found in email thread but not in tracker",
            "email_count": len(emails),
            "earliest_email": min(e.get("receivedDateTime","") for e in emails),
            "classification": "PRE_PIPELINE",
            "flag": "REQUIRES_HUBSPOT_VERIFICATION",
        },
        performed_by="SYSTEM",
    )
    # Refresh known IDs so duplicates in the same sweep are skipped
    known_ids.add(gwc_id)
```

**0e — Immediately classify each backfilled lead using AI:**

```python
from scan_cc_emails import build_thread_payload

for gwc_id, emails in orphan_gwc_ids.items():
    lead = store.get_lead(gwc_id)   # fetch the stub we just wrote
    thread_payload = build_thread_payload(emails, lead, store)
    prompt = build_orphan_analysis_prompt(thread_payload)

    # ← Analyse inline (you ARE Claude — read the prompt and produce the JSON)
    # analysis_json_string = <your inline analysis of the thread>

    analysis = parse_analysis_result(analysis_json_string, "NO_ACTION")
    updates  = build_status_update(analysis, lead, now_iso)
    store.update_lead_field(gwc_id, updates)

    store.log_activity(
        gwc_id=gwc_id,
        activity_type="STATUS_CHANGE",
        detail={
            "previous_status":    "NO_ACTION",
            "recommended_status": analysis["recommended_status"],
            "confidence":         analysis["confidence"],
            "reasoning":          analysis["reasoning"],
            "key_evidence":       analysis["key_evidence"],
            "source":             "PRE_PIPELINE_BACKFILL",
        },
        performed_by="SYSTEM",
    )
```

**0f — Print backfill summary:**

```
🔁 Pre-Pipeline Backfill
─────────────────────────────────────────
Orphan GWC IDs found:   3
  GWC-111111  →  QUOTED    (high confidence)  ⚑ PRE_PIPELINE
  GWC-222222  →  WON_LOSS  (medium confidence) ⚑ PRE_PIPELINE
  GWC-333333  →  ENGAGED   (high confidence)  ⚑ PRE_PIPELINE

All 3 written to leads_maturity.csv with classification=PRE_PIPELINE.
⚠ Recommend verifying HubSpot for original shipment fields (origin, product, weight).
```

> **Why `classification = PRE_PIPELINE`?**  
> These rows lack HubSpot structural fields (origin country, commodity, weight, etc.) because
> the original intake email is unavailable. The `PRE_PIPELINE` flag tells downstream skills
> (gap analysis, dashboard) to treat missing fields as a data provenance gap, not a rep
> data-quality issue. Marketing should retrieve the original HubSpot record for these GWC IDs.

### Step 1 — Load active leads

```python
import sys
sys.path.insert(0, f"{WORKSPACE}/skills/lead-ingestion/scripts")
sys.path.insert(0, f"{WORKSPACE}/skills/lead-status-tracker/scripts")

from csv_store import CSVStore
from scan_cc_emails import get_active_leads

store = CSVStore(f"{WORKSPACE}/data")
active_leads = get_active_leads(store)
print(f"Active leads to scan: {len(active_leads)}")
```

If `len(active_leads) == 0`: report "No active leads to scan" and stop.

### Step 2 — For each active lead, fetch CC'd emails from the shared mailbox

For each lead, use `OUTLOOK_QUERY_EMAILS` with:
- `user_id`: `Sales.rfq@gwclogistics.com`
- `folder`: `inbox`
- `filter`: `contains(subject, '{gwc_id}')` where `gwc_id` is the lead's GWC ID
- `select`: `["id", "subject", "from", "receivedDateTime", "body", "bodyPreview"]`
- `top`: 25
- `orderby`: `receivedDateTime asc`

**Important**: This query returns ALL emails in the inbox matching the GWC ID — including
the original HubSpot notification. The `build_thread_payload()` function handles filtering
it out when building the analysis payload.

Store the result as a list of email dicts. If no emails are returned, record
`thread_count = 0` for this lead.

### Step 3 — Build thread payload

```python
from scan_cc_emails import build_thread_payload

thread_payload = build_thread_payload(
    emails=fetched_emails,   # list from Step 2 (may include original HubSpot email)
    lead=lead,
    store=store,
)
# thread_payload["thread"] will have the original HubSpot email with is_original_hubspot=True
# The analysis prompt skips those automatically
```

### Step 4 — Analyse the thread with Claude AI

```python
from analyze_thread import build_analysis_prompt, parse_analysis_result

prompt = build_analysis_prompt(thread_payload)
```

**Call Claude inline** — since you ARE Claude running this skill, analyse the thread
directly using your own reasoning. Read the `prompt` text carefully and follow its
instructions to produce the JSON response.

Do not spawn a subagent. Just read the thread_payload["thread"] and the prompt,
reason about the status, and produce the JSON output yourself.

After generating the JSON response:

```python
analysis = parse_analysis_result(your_json_response_as_string, lead["current_status"])
```

The parser applies safety guards:
- Prevents backward status moves
- Defaults to no-change if JSON is malformed
- Validates deal_outcome as WON or LOSS only

### Step 5 — Apply status update to CSV

```python
from analyze_thread import build_status_update
from scan_cc_emails import detect_first_gwc_sender
from datetime import datetime, timezone

now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
updates = build_status_update(analysis, lead, now_iso)

# Capture the working rep (the actual @gwclogistics.com thread sender).
# Only write detected_working_rep_* once — don't overwrite if already set.
if not lead.get("detected_working_rep_email", "").strip():
    det_email, det_name = detect_first_gwc_sender(thread_payload, store)
    if det_email:
        updates["detected_working_rep_email"] = det_email
        updates["detected_working_rep_name"]  = det_name

# If bd_poc_email is still blank, backfill it from the detected sender so the
# dashboard always has a working-rep value to display.
if not lead.get("bd_poc_email", "").strip() and updates.get("detected_working_rep_email"):
    updates["bd_poc_email"] = updates["detected_working_rep_email"]
    updates["bd_poc_name"]  = updates.get("detected_working_rep_name",
                                          lead.get("detected_working_rep_name", ""))

store.update_lead_field(lead["gwc_id"], updates)
```

### Step 6 — Log the analysis in activity log

```python
import json

activity_type = "STATUS_CHANGE" if analysis["status_changed"] else "EMAIL_RECEIVED"
store.log_activity(
    gwc_id=lead["gwc_id"],
    activity_type=activity_type,
    detail={
        "previous_status":     lead["current_status"],
        "recommended_status":  analysis["recommended_status"],
        "status_changed":      analysis["status_changed"],
        "confidence":          analysis["confidence"],
        "reasoning":           analysis["reasoning"],
        "key_evidence":        analysis["key_evidence"],
        "thread_emails_found": thread_payload["thread_count"],
        "dark_lead":           analysis.get("dark_lead", False),
    },
    performed_by="SYSTEM",
)
```

### Step 7 — Dark lead detection

After processing all leads, run the dark lead check:

```python
from dark_lead_detector import check_dark_leads, build_dark_lead_summary

# Build a map of gwc_id → number of non-original CC emails found
thread_counts = {}
for lead in active_leads:
    gwc_id = lead["gwc_id"]
    # Count emails excluding the original HubSpot notification
    cc_only = [e for e in fetched_email_map.get(gwc_id, [])
               if "BULK" not in e.get("subject","").upper()]
    thread_counts[gwc_id] = len(cc_only)

dark_leads = check_dark_leads(active_leads, thread_counts)
print(build_dark_lead_summary(dark_leads))
```

For each dark lead, log in activity:
```python
store.log_activity(
    gwc_id=dl["gwc_id"],
    activity_type="ESCALATION",
    detail={
        "action":        "dark_lead_flagged",
        "days_silent":   dl["days_silent"],
        "current_status": dl["current_status"],
        "rep_email":     dl["assigned_rep_email"],
        "reason":        dl["reason"],
    },
    performed_by="SYSTEM",
)
```

### Step 8 — Print summary

```
✅ Lead Status Tracker Complete
──────────────────────────────────────────
Leads scanned:      5
Status changes:     2
  GWC-111111 : NO_ACTION  → ENGAGED   (high confidence)
  GWC-222222 : ENGAGED    → QUOTED    (high confidence)
No change:          3
  GWC-333333 : QUOTED     (no new emails found)
  GWC-444444 : FOLLOW_UP  (no new emails)
  GWC-555555 : NO_ACTION  (rep hasn't responded yet)

Dark leads flagged: 1
  GWC-444444 — FOLLOW_UP, 7 days silent — Rep: rep@gwclogistics.com

leads_maturity.csv updated ✓
Activity logged ✓
```

## Status transition reference

> See `references/freight_domain_knowledge.md` §1 for the full signal list and §5 for
> unqualified lead patterns (partnership requests, job seekers, personal shippers).

| From → To | Evidence to look for |
|---|---|
| NO_ACTION → ENGAGED | Any email FROM a @gwclogistics.com address TO the customer |
| ENGAGED → QUOTED | Rep email containing: quotation, pricing, rates, proposal, freight charges, "please find attached", rate sheet, costing |
| QUOTED → FOLLOW_UP | Any reply FROM the customer AFTER a quote email (silence ≠ FOLLOW_UP) |
| FOLLOW_UP → WON_LOSS | Customer says "confirmed", "proceed", "we accept", "book it" (WON) OR "not interested", "cancel", "found another", "not proceeding" (LOSS) |

## Critical rules

1. **Status only moves forward.** `parse_analysis_result()` enforces this as a safety guard.
2. **Low confidence = no change.** If Claude is unsure, keep the current status.
3. **Empty threads = no change.** If no CC'd emails beyond the HubSpot original, leave status as is.
4. **Dark leads ≠ status change.** Dark lead detection is a flag for reporting, not a status transition.
5. **All activity is logged.** Even "no change" decisions should be logged as EMAIL_RECEIVED.

## Script reference

| Script | Key functions |
|--------|--------------|
| `scripts/scan_cc_emails.py` | `get_active_leads(store)`, `build_thread_payload(emails, lead, store)`, `classify_email_role(email, lead, store)`, `is_dark_lead(lead, latest_date)` |
| `scripts/analyze_thread.py` | `build_analysis_prompt(thread_payload)`, `parse_analysis_result(response, current_status)`, `build_status_update(analysis, lead, now_iso)` |
| `scripts/dark_lead_detector.py` | `check_dark_leads(active_leads, thread_counts)`, `build_dark_lead_summary(dark_leads)` |
| `../lead-ingestion/scripts/csv_store.py` | `CSVStore(data_dir)`, `.update_lead_field()`, `.log_activity()` |
