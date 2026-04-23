---
name: lead-follow-up-cadence
description: >
  GWC Lead Maturity Automation — Phase 4 follow-up cadence skill.
  Scans leads_maturity.csv for ENGAGED, QUOTED, and FOLLOW_UP leads that have
  exceeded cadence thresholds and sends GWC-branded reminder emails to the
  assigned sales rep via Outlook. Deduplicates reminders so each threshold fires
  exactly once per lead. Trigger when the user says: "send follow-up reminders",
  "run the cadence", "check who needs a reminder", "chase reps on stale leads",
  "run Phase 4", "what leads need follow-up today", or any variation of
  proactively nudging reps about leads that have gone quiet.
---

# Lead Follow-Up Cadence Skill

Checks all active leads against the follow-up cadence schedule and sends
GWC-branded reminder emails to assigned sales reps via Outlook.

## Cadence schedule

### NO_ACTION (rep notified but has not yet replied to the customer)

| Status | Days elapsed | Reminder type | Action urged |
|---|---|---|---|
| NO_ACTION | Day 1–14 (daily) | `NO_REPLY_REMINDER` | Send first reply to the customer |

- Only fires when `assigned_rep_email` is set (lead has been routed by Phase 2)
- Stops automatically once Phase 3 transitions the lead to ENGAGED
- Day counting uses the `routed_at` timestamp written by Phase 2
- Deduplication uses `NO_REPLY_REMINDER` activity type — does **not** interfere with `QUOTE_REMINDER` entries after promotion to ENGAGED

### ENGAGED and QUOTED (unchanged)

| Status | Days elapsed | Reminder type | Action urged |
|---|---|---|---|
| ENGAGED | Day 3, 7, 14 | `QUOTE_REMINDER` | Send a quotation |
| QUOTED | Day 2, 5, 10 | `CHASER_REMINDER` | Follow up with customer |

### FOLLOW_UP (new weekly cadence — v2 spec Section 6)

| Week | Day thresholds | Frequency | Reminder type |
|---|---|---|---|
| Week 1 (days 1–7) | 1, 2, 3, 4, 5, 6, 7 | Daily | `CLOSE_REMINDER` |
| Week 2 (days 8–14) | 10, 14 | Twice per week | `CLOSE_REMINDER` |
| Week 3 (days 15–21) | 21 | Once per week | `CLOSE_REMINDER` |
| Week 4 (days 22–28) | 28 | Final closure | `ESCALATION_REMINDER` → manager notified |

After **Day 28** with no resolution: escalation email sent to `hebah.yasin@gwclogistics.com`
for manual manager review.

- **Day counting** starts from when the lead entered that status (uses `routed_at` for NO_ACTION, `first_response_at` for ENGAGED, `quote_sent_at` for QUOTED, `follow_up_started_at` for FOLLOW_UP, with `status_history` JSON as fallback)
- **Deduplication**: each `(gwc_id, threshold_day)` fires **once only per reminder type** — checked against activity log (`NO_REPLY_REMINDER` for NO_ACTION; `FOLLOW_UP_REMINDER`/`ESCALATION_REMINDER` for others)
- **REJECTED / WON_LOSS / GAP_ANALYSIS** leads are always skipped; **NO_ACTION leads are eligible only when `assigned_rep_email` is set**
- Leads with no `assigned_rep_email` are skipped (can't send a reminder)

## Prerequisites & paths

```
WORKSPACE = /sessions/gifted-affectionate-lovelace/mnt/marketing-campaign-leads-tracking automation
DATA_DIR  = WORKSPACE/data/
SCRIPTS   = WORKSPACE/skills/lead-follow-up-cadence/scripts/
  cadence_rules.py               — thresholds, age calculator, eligibility checker
  followup_teams_templates.py    — GWC-branded Adaptive Card reminders per status
  send_followup_reminders.py     — dry_run(), get_reminder_tasks(), log_reminder(), build_summary()

SHARED_SCRIPTS = WORKSPACE/skills/lead-ingestion/scripts/
  csv_store.py                 — CSV read/write (shared)
```

## Connector

**teams-composio** — used to send 1:1 Teams DMs to the assigned rep.
Escalation (Day 28 FOLLOW_UP) sends a separate DM to the manager as well.

---

## Step-by-step execution

### Step 1 — Import and find due reminders
Replace `<workspace>` with the actual path to the workspace folder (the one selected by the user)
```python
import sys
sys.path.insert(0, f"{WORKSPACE}/skills/lead-ingestion/scripts")
sys.path.insert(0, f"{WORKSPACE}/skills/lead-follow-up-cadence/scripts")

from csv_store import CSVStore
from send_followup_reminders import get_reminder_tasks, log_reminder, build_summary, dry_run

store = CSVStore(f"{WORKSPACE}/data")
tasks = get_reminder_tasks(store)
print(f"Reminders due: {len(tasks)}")
for t in tasks:
    print(f"  {t['gwc_id']} | {t['status']} | Day {t['threshold_day']} ({t['days_elapsed']} days) → {t['rep_email']}")
```

If `len(tasks) == 0`, print "No follow-up reminders due today." and stop.

**To preview without sending**, call `dry_run(store)` and print the result instead.

### Step 2 — Build and send each reminder via Teams DM

For each task in `tasks`:

```python
from followup_teams_templates import build_reminder_card, card_to_attachment, build_escalation_manager_card

title, card = build_reminder_card(task)
attachment  = card_to_attachment(card)

# 2a — Create or retrieve 1:1 chat with the rep
chat    = MICROSOFT_TEAMS_TEAMS_CREATE_CHAT(
    chatType="oneOnOne",
    members=[task["rep_email"]]
)
chat_id = chat["id"]

# 2b — Post the Adaptive Card DM to the rep
MICROSOFT_TEAMS_TEAMS_POST_CHAT_MESSAGE(
    chat_id=chat_id,
    body={
        "contentType": "html",
        "content": f"<attachment id='{attachment['id']}'></attachment>"
    },
    attachments=[attachment]
)
```

**Escalation (Day 28 FOLLOW_UP only) — also DM the manager:**
```python
if task.get("is_escalation"):
    from cadence_rules import MANAGER_EMAIL
    mgr_title, mgr_card = build_escalation_manager_card(
        task["lead"], task["rep_name"], task["days_elapsed"]
    )
    mgr_attachment = card_to_attachment(mgr_card)
    mgr_chat = MICROSOFT_TEAMS_TEAMS_CREATE_CHAT(
        chatType="oneOnOne",
        members=[MANAGER_EMAIL]
    )
    MICROSOFT_TEAMS_TEAMS_POST_CHAT_MESSAGE(
        chat_id=mgr_chat["id"],
        body={
            "contentType": "html",
            "content": f"<attachment id='{mgr_attachment['id']}'></attachment>"
        },
        attachments=[mgr_attachment]
    )
```

### Step 3 — Log each reminder (always, even on failure)

After each `OUTLOOK_SEND_EMAIL` attempt:

```python
# On success:
log_reminder(store, task, sent_ok=True)

# On Outlook error:
log_reminder(store, task, sent_ok=False, error_msg="<error description>")
```

`log_reminder` writes a `FOLLOW_UP_REMINDER` row to `lead_activity_log.csv`.
This is what prevents duplicate sends on the next run.

### Step 4 — Print summary

```python
results = [
    {"task": task, "sent": True, "error": ""},   # one entry per task
    # ... etc
]
print(build_summary(tasks, results))
```

---

## Example output

```
✅ Follow-Up Cadence Complete
──────────────────────────────────────────────
Reminders due:   2
  Sent:          2
  Failed:        0

Reminder breakdown:
  ✓ GWC-737039407312 | ENGAGED | Day 3 | QUOTE_REMINDER → rafat.zourgan@gwclogistics.com
  ✓ GWC-123456789000 | QUOTED  | Day 5 | CHASER_REMINDER → sujith.sukumaran@gwclogistics.com

Activity logged ✓
```

---

## Critical rules

1. **Never send a reminder more than once per threshold** — `log_reminder()` gates this via the activity log; always call it even on failure
2. **Send via Teams 1:1 DM** using `teams-composio`; do not use Outlook
3. **Do NOT set status to FOLLOW_UP** from this skill — the Phase 3 status tracker owns status changes; this skill only sends emails
4. **Skip leads without a rep** — no `assigned_rep_email` → skip silently
5. **REJECTED / WON_LOSS / GAP_ANALYSIS** leads are never eligible; **NO_ACTION leads ARE eligible** when they have an `assigned_rep_email` (rep notified but not yet replied)
6. **Day 28 FOLLOW_UP = escalation** — check `task["is_escalation"]`; if True, also send a **separate Teams DM** to `hebah.yasin@gwclogistics.com` using `build_escalation_manager_card()`

## Script reference

| Script | Key exports |
|---|---|
| `cadence_rules.py` | `CADENCE_THRESHOLDS`, `FOLLOW_UP_ESCALATION_DAY`, `get_leads_needing_reminder(store)`, `days_since_status_entry(lead)`, `was_reminder_sent(gwc_id, threshold_day, rows)`, `get_follow_up_context(threshold_day)` |
| `followup_teams_templates.py` | `build_reminder_card(task)`, `build_engaged_reminder(...)`, `build_quoted_reminder(...)`, `build_followup_reminder(...)`, `build_escalation_manager_card(...)`, `card_to_attachment(card)` |
| `send_followup_reminders.py` | `get_reminder_tasks(store)`, `log_reminder(store, task, sent_ok, error_msg)`, `build_summary(tasks, results)`, `dry_run(store)` |
| `../lead-ingestion/scripts/csv_store.py` | `CSVStore(data_dir)`, `.log_activity()`, `._read_csv()` |
