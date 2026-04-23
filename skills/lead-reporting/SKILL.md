---
name: lead-reporting
description: >
  GWC Lead Maturity Automation — Phase 5a weekly reporting skill.
  Reads leads_maturity.csv and lead_activity_log.csv, computes a weekly summary
  (funnel counts, rep performance, alerts for dark/stale leads, recent activity),
  and sends a GWC-branded HTML report to the manager as a 1:1 Teams DM.
  Trigger when the user says: "send the weekly report", "generate the lead report",
  "email Heba the weekly summary", "what's the pipeline status", "run Phase 5 reporting",
  "weekly lead summary", "lead performance report", or any variation of wanting a
  periodic summary of the pipeline.
---

# Lead Reporting Skill

Generates and emails a weekly GWC-branded lead pipeline report to the manager.

## Prerequisites & paths
Replace `<workspace>` with the actual path to the workspace folder (the one selected by the user)
```
DATA_DIR  = WORKSPACE/data/
SCRIPTS   = WORKSPACE/skills/lead-reporting/scripts/
  report_builder.py          — data aggregation (funnel, rep stats, dark/stale leads)
  report_teams_template.py   — GWC-branded Adaptive Card builder

SHARED_SCRIPTS = WORKSPACE/skills/lead-ingestion/scripts/
  csv_store.py             — CSV read/write (shared)
```

## Connector

**teams-composio** — used to send the report as a 1:1 Teams DM to `hebah.yasin@gwclogistics.com`.

---

## Step-by-step execution

### Step 1 — Build report data

```python
import sys
sys.path.insert(0, f"{WORKSPACE}/skills/lead-ingestion/scripts")
sys.path.insert(0, f"{WORKSPACE}/skills/lead-reporting/scripts")

from csv_store import CSVStore
from report_builder import build_report
from report_teams_template import build_report_card, card_to_attachment

store = CSVStore(f"{WORKSPACE}/data")
data  = build_report(store, period_days=7)

print(f"Total leads:    {data['meta']['total_leads']}")
print(f"New this week:  {data['new_this_period']}")
print(f"Active pipeline:{sum(data['status_counts'].get(s,0) for s in ('ENGAGED','QUOTED','FOLLOW_UP'))}")
print(f"Dark leads:     {len(data['dark_leads'])}")
print(f"Stale leads:    {len(data['stale_leads'])}")
```

### Step 2 — Build the Adaptive Card

```python
title, card = build_report_card(data)
print(f"Title: {title}")
```

### Step 3 — Send via Teams 1:1 DM to manager

```python
attachment = card_to_attachment(card)

# Create or retrieve 1:1 chat with the manager
chat = MICROSOFT_TEAMS_TEAMS_CREATE_CHAT(
    chatType="oneOnOne",
    members=["hebah.yasin@gwclogistics.com"]
)

MICROSOFT_TEAMS_TEAMS_POST_CHAT_MESSAGE(
    chat_id=chat["id"],
    body={
        "contentType": "html",
        "content": f"<attachment id='{attachment['id']}'></attachment>"
    },
    attachments=[attachment]
)
```

### Step 4 — Log and confirm

```python
store.log_activity(
    gwc_id="SYSTEM",
    activity_type="REPORT_SENT",
    detail={
        "report_type":     "WEEKLY",
        "period_days":     7,
        "total_leads":     data["meta"]["total_leads"],
        "new_this_period": data["new_this_period"],
        "dark_leads":      len(data["dark_leads"]),
        "stale_leads":     len(data["stale_leads"]),
        "sent_to":         "hebah.yasin@gwclogistics.com (Teams DM)",
    },
    performed_by="SYSTEM",
)
print("✅ Weekly report sent to hebah.yasin@gwclogistics.com via Teams DM")
```

---

## Report contents

| Section | What it shows |
|---|---|
| **Summary** | Total leads, new this week, active pipeline, won deals |
| **Pipeline Funnel** | Bar chart: lead counts per status |
| **Alerts** | Dark leads (5+ days silent) and stale leads (beyond threshold) |
| **Performance by Rep** | Per-rep totals: engaged / quoted / follow-up / won |
| **Leads by Mode** | Air / Sea / Overland split |
| **Recent Activity** | Last 10 events from the activity log this week |

## Script reference

| Script | Key exports |
|---|---|
| `report_builder.py` | `build_report(store, period_days=7) → dict` |
| `report_teams_template.py` | `build_report_card(data) → (title, card)`, `card_to_attachment(card)` |
| `../lead-ingestion/scripts/csv_store.py` | `CSVStore(data_dir)`, `.log_activity()` |
