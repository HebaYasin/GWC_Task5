# GWC Lead Maturity Automation — Agent Instructions

This workspace runs the **GWC Lead Maturity Automation** system for GWC Logistics.
It ingests HubSpot freight lead emails from a shared Outlook mailbox, classifies and routes
them to sales reps, tracks email-thread progress through a defined status pipeline,
sends follow-up reminders, and surfaces analytics through a dashboard and reports.

**Never recreate logic that already exists in the skills' scripts.** Always import and call
the existing Python scripts. If a script exists, use it and do not re-create it.

any output you produce should follow GWC branding guidelines.

when running for the first time and csv files are empty, parse all emails in the mailbox.
---

## Skill Selection Guide — Read This First

Use the table below to pick the right skill for every user request.
When two or more pipeline phases are involved, **always use the orchestrator**.

| User says… | Skill to invoke |
|---|---|
| "run the full pipeline" / "run everything" / "process all leads" / "automate everything" / "what's new today" | **`lead-pipeline-orchestrator`** |
| "run phases 1 and 2" / "ingest and route" / "check for new leads and route them" | **`lead-pipeline-orchestrator`** |
| "run phases 3 and 4" / "update statuses and send reminders" | **`lead-pipeline-orchestrator`** |
| "check for new leads" / "scan the mailbox" / "ingest leads" / "run Phase 1" | **`lead-ingestion`** |
| "run Quip enrichment" / "cross-check with Quip" / "enrich from Quip" | **`lead-quip-enrichment`** |
| "route leads" / "assign reps" / "notify the sales team" / "run Phase 2" | **`lead-routing`** |
| "update lead statuses" / "scan email threads" / "find dark leads" / "run Phase 3" | **`lead-status-tracker`** |
| "send follow-up reminders" / "run the cadence" / "nudge reps" / "run Phase 4" | **`lead-follow-up-cadence`** |
| "send the weekly report" / "weekly pipeline summary" / "email Heba the report" | **`lead-reporting`** |
| "run the gap analysis" / "check Extensia quality" / "score the notes" / "monthly report" | **`lead-gap-analysis`** |
| "generate the dashboard" / "show pipeline analytics" / "show me charts" | **`lead-dashboard`** |

### Orchestrator vs Individual Skills
- **Orchestrator** (`lead-pipeline-orchestrator`): use whenever the user wants **two or more phases**, or says anything like "full pipeline", "run everything", "process and route", "catch up on leads". The orchestrator reads and executes each individual SKILL.md in the correct order.
- **Individual skills**: use only when the user explicitly wants **one specific phase** in isolation.

---

## Workspace layout

```
WORKSPACE = <current selected folder>  ← resolve dynamically; do NOT hardcode session paths

data/
  leads_maturity.csv        ← master lead table (gwc_id = primary key)
  country_rep_mapping.csv   ← country → sales rep assignments
  lead_activity_log.csv     ← append-only event log

skills/
  lead-pipeline-orchestrator/ ← ENTRY POINT: runs all phases in order
  lead-ingestion/             ← Phase 1: parse HubSpot emails → classify → CSV
  lead-quip-enrichment/       ← Optional: cross-check leads against Quip sheet (run between Phase 1 & 2)
  lead-routing/               ← Phase 2: assign reps → send Teams DM notification
  lead-status-tracker/        ← Phase 3: scan CC threads → AI status transitions
  lead-follow-up-cadence/     ← Phase 4: send timed reminder Teams DMs to reps
  lead-reporting/             ← Phase 5a: weekly summary Teams DM to manager
  lead-gap-analysis/          ← Phase 5b: monthly pipeline gap + Extensia quality → Teams DM to manager
  lead-dashboard/             ← Analytics: generate leads_dashboard.html

leads_dashboard.html        ← generated dashboard (regenerate on demand)
```

---

## Shared script — always import this first

```python
import sys
# WORKSPACE => current selected folder
sys.path.insert(0, f"{WORKSPACE}/skills/lead-ingestion/scripts")
from csv_store import CSVStore   # csv_store.py is now a shim → imports DBStore
store = CSVStore(f"{WORKSPACE}/data")
```

`CSVStore` (backed by `DBStore` in `db_store.py`) is the single interface for all
reads and writes. Never open CSV files directly with `open()` or `pandas`. Key methods:
- `store.get_lead(gwc_id)` → dict or None
- `store.upsert_lead(fields)` → insert or update by gwc_id
- `store.update_lead_field(gwc_id, updates_dict)`
- `store.log_activity(gwc_id, activity_type, detail, performed_by="SYSTEM")`
- `store.lookup_reps(country_name, primary_only=False)`
- `store._read_csv(path)` → list of dicts (use sparingly)

**Every write also appends to `data/pending_writes.jsonl`.
After each phase, flush that queue to Databricks — see section below.**

---

## Databricks sync — flush after every phase

Data is stored in **`claude_prototyping.marketing`** (Databricks Unity Catalog).
Local CSVs in `data/` are the session cache; Databricks is the durable store.

**MCP connector**: `mcp__62f760ee-bfcc-4f93-bec8-cdf2d76870ad` (already authenticated).

### Post-phase flush (run after EVERY pipeline phase)

```python
import sys
sys.path.insert(0, f"{WORKSPACE}/skills/lead-ingestion/scripts")
from db_sync import generate_sql_statements, clear_queue, preview_queue

queue_path = f"{WORKSPACE}/data/pending_writes.jsonl"

# 1. Generate SQL
stmts = generate_sql_statements(queue_path)
print(f"Flushing {len(stmts)} write(s) to Databricks...")
```

Then execute each statement via the Databricks MCP tool:
```
execute_sql(stmts[0])   # calls mcp__62f760ee-bfcc-4f93-bec8-cdf2d76870ad__execute_sql
execute_sql(stmts[1])
... (repeat for all statements)
```

Then clear the queue:
```python
clear_queue(queue_path)
print("Queue cleared.")
```

### Pre-phase seed from Databricks (optional — use when CSVs are stale/missing)

```python
from db_sync import write_mcp_result_to_csv, LEADS_COLUMNS, ACTIVITY_COLUMNS

# Claude calls execute_sql_read_only first, then pipes result here:
# result = execute_sql_read_only("SELECT * FROM claude_prototyping.marketing.leads_maturity")
write_mcp_result_to_csv(result, store.leads_path, LEADS_COLUMNS)

# result2 = execute_sql_read_only("SELECT * FROM claude_prototyping.marketing.lead_activity_log")
write_mcp_result_to_csv(result2, store.activity_path, ACTIVITY_COLUMNS)
```

### Databricks tables

| Table | Purpose |
|---|---|
| `claude_prototyping.marketing.leads_maturity` | Master lead table (gwc_id PK) |
| `claude_prototyping.marketing.lead_activity_log` | Append-only event log |
| `claude_prototyping.marketing.country_rep_mapping` | Read-only rep routing table |

### Queue inspection

```python
entries = preview_queue(queue_path)   # inspect without executing
print(f"{len(entries)} pending write(s)")
```

---

## Connectors

**Reading lead emails** uses the `outlook-composio` MCP connector:
- **Read emails**: `OUTLOOK_QUERY_EMAILS` with `user_id="Sales.rfq@gwclogistics.com"`
  and an OData `filter` such as `contains(subject, 'GWC-XXXXXXXXX')`.
  **Never** use `OUTLOOK_SEARCH_MESSAGES` — it fails with delegated permissions.
- Outlook is used **only for reading**. Never use `OUTLOOK_SEND_EMAIL` for any pipeline notification.

**All outbound notifications** use the `teams-composio` MCP connector via 1:1 DMs:
- **Create or retrieve a 1:1 chat**: `MICROSOFT_TEAMS_TEAMS_CREATE_CHAT` with `chatType="oneOnOne"` and `members=[recipient_email]`
- **Send an Adaptive Card DM**: `MICROSOFT_TEAMS_TEAMS_POST_CHAT_MESSAGE` with the card as an attachment

**Cross-checking with Quip** uses the `quip` MCP connector *(optional — only when Quip is active)*:
- Used exclusively by the `lead-quip-enrichment` skill (runs between Phase 1 and Phase 2)
- Load the Quip sheet once per session and cache it in memory; do not re-fetch per lead

| Phase | Who receives the Teams DM |
|---|---|
| Phase 2 — Routing | Assigned rep (or manager if unroutable) |
| Phase 4 — Cadence | Assigned rep; manager also DM'd on Day 28 escalation |
| Phase 5a — Weekly report | Manager (`hebah.yasin@gwclogistics.com`) |
| Phase 5b — Gap analysis | Manager (`hebah.yasin@gwclogistics.com`) |

**Sending an Adaptive Card — standard pattern:**
```python
from <skill>/scripts/<teams_template> import build_<type>_card, card_to_attachment

title, card = build_<type>_card(...)
attachment  = card_to_attachment(card)

chat = MICROSOFT_TEAMS_TEAMS_CREATE_CHAT(
    chatType="oneOnOne",
    members=[recipient_email]
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

---

## Key constants

| Constant | Value |
|---|---|
| Shared mailbox | `Sales.rfq@gwclogistics.com` |
| Manager email | `hebah.yasin@gwclogistics.com` |
| GWC ID format | `GWC-\d+` (extracted from email subject line) |
| HubSpot sender | `jakub.skopec@gwclogistics.com` |
| HubSpot subject pattern | `[BULK] New Freight Opportunity for GWC-XXXXXXXXX` |

---

## Lead status pipeline

Status moves **forward only** — never backwards.

```
NO_ACTION → ENGAGED → QUOTED → FOLLOW_UP → WON_LOSS
                                                     ↓
                                               GAP_ANALYSIS  (terminal review state)
REJECTED  (terminal — never routed)
```

| Status | Meaning |
|---|---|
| `NO_ACTION` | Lead arrived; rep may be assigned but has not yet sent any email to the customer |
| `ENGAGED` | Rep has sent at least one email to the customer (set by Phase 3, never by Phase 2) |
| `QUOTED` | Rep sent a price / quotation / proposal |
| `FOLLOW_UP` | Customer replied after receiving the quote |
| `WON_LOSS` | Deal confirmed won or lost |
| `REJECTED` | GWC ID missing or not a freight email — logged, never routed |
| `GAP_ANALYSIS` | Terminal review state after human SME reviews gap analysis output |

Dark lead threshold: **5 days** of no CC email activity on an ENGAGED/QUOTED/FOLLOW_UP lead.

---

## Skills — when to call each one

Read the SKILL.md inside each skill folder before executing. Every SKILL.md contains
the exact step-by-step instructions and script imports to use.

### Orchestrator — `lead-pipeline-orchestrator` 
Trigger on: "run the full pipeline", "process everything", "run all phases", "automate everything",
"what's new today", "run phases X and Y", or any request spanning two or more pipeline phases.

The orchestrator reads and delegates to each individual phase SKILL.md in order:
Phase 1 → Phase 2 → Phase 3 → Phase 4 (stop-and-report between each phase).
Phases 5a, 5b, and Dashboard are independent and can be requested alongside any run.

---

### Phase 1 — `lead-ingestion`
Trigger on: "check for new leads", "scan the mailbox", "ingest leads", "pull freight emails",
"process new opportunities", "run Phase 1", or any request to read and store incoming HubSpot leads.

What it does:
1. Query `Sales.rfq@gwclogistics.com` inbox via `OUTLOOK_QUERY_EMAILS` with filter `contains(subject, 'New Freight Opportunity')`
2. Strip HTML → `parse_lead_email(subject, plain_body, msg_id)` → structured fields dict
3. `classify_lead(fields)` → QUALIFIED / PARTIALLY_QUALIFIED / REJECTED (deterministic, no AI)
4. `apply_classification(fields, result)` → merge into fields
5. `store.upsert_lead(fields)` → write to CSV
6. `store.log_activity(...)` → log EMAIL_RECEIVED

**Never** call Phase 2 automatically after Phase 1. Always stop and report.

---

### Phase 2 — `lead-routing`
Trigger on: "route leads", "assign reps", "send lead notifications", "notify the sales team",
"who should handle these leads", "run Phase 2".

What it does:
1. `get_unrouted_leads(store)` → NO_ACTION leads with no assigned rep
2. `get_routing_decision(lead, store)` → find primary rep by destination country
3. Build GWC-branded Adaptive Card (`build_routing_card_qualified` / `_partial` / `_unroutable`) from `teams_templates.py`
4. `MICROSOFT_TEAMS_TEAMS_CREATE_CHAT` + `MICROSOFT_TEAMS_TEAMS_POST_CHAT_MESSAGE` → 1:1 DM to rep
5. `store.update_lead_field(...)` → set assigned rep fields + `routed_at` timestamp (status stays `NO_ACTION` — ENGAGED is set by Phase 3 when the rep sends their first email)
6. `store.log_activity(...)` → log REP_NOTIFIED or ESCALATION

Unroutable leads (no country match) → Teams DM to `hebah.yasin@gwclogistics.com`.
REJECTED leads → skip silently.
Already-routed leads (assigned_rep_email set) → skip silently.

---

### Phase 3 — `lead-status-tracker`
Trigger on: "update lead statuses", "scan for status changes", "track lead progress",
"check CC emails", "check email threads", "find dark leads", "run Phase 3".

What it does:
1. `get_active_leads(store)` → ENGAGED/QUOTED/FOLLOW_UP/NO_ACTION leads (excludes REJECTED/WON_LOSS/GAP_ANALYSIS)
2. For each lead, `OUTLOOK_QUERY_EMAILS` filtering by GWC ID in subject
3. `build_thread_payload(emails, lead, store)` → sorted thread, HTML stripped, HubSpot original flagged
4. **Inline AI analysis** (Claude reads the thread itself — no subagent): apply `build_analysis_prompt(thread_payload)`, reason about the status transition using `freight_domain_knowledge.md`, produce the JSON result
5. `parse_analysis_result(claude_response, current_status)` → apply forward-only guard
6. `build_status_update(analysis, lead, now_iso)` → compute timestamp fields
7. `store.update_lead_field(gwc_id, updates)`
8. `store.log_activity(...)` → log STATUS_CHANGE
9. `check_dark_leads(active_leads, thread_counts)` → flag 5+ day silent leads

**Important**: Claude IS the AI doing the analysis — do not try to call an external model.

---

### Phase 4 — `lead-follow-up-cadence`
Trigger on: "send follow-up reminders", "run the cadence", "chase reps on stale leads",
"who needs a reminder today", "run Phase 4", "follow up with reps".

Cadence schedule (fires once per threshold — deduplicated via activity log):
- NO_ACTION (rep assigned, no reply yet) → Day 1–14 (daily) → `NO_REPLY_REMINDER` "Reply to the customer"
- ENGAGED → Day 3, 7, 14 → "Send a quotation"
- QUOTED → Day 2, 5, 10 → "Chase the customer"
- FOLLOW_UP → **New weekly schedule**:
  - Week 1 (Days 1–7): Daily
  - Week 2 (Days 8–14): Day 10, 14
  - Week 3 (Days 15–21): Day 21
  - Week 4 (Days 22–28): Day 28 → ESCALATION (separate DM also sent to manager)

What it does:
1. `get_reminder_tasks(store)` → leads due a reminder today (includes `is_escalation` flag)
2. `build_reminder_card(task)` → GWC-branded Adaptive Card from `followup_teams_templates.py`
3. `MICROSOFT_TEAMS_TEAMS_CREATE_CHAT` + `MICROSOFT_TEAMS_TEAMS_POST_CHAT_MESSAGE` → 1:1 DM to rep
4. If `is_escalation == True`: also DM manager via `build_escalation_manager_card()`
5. `log_reminder(store, task, sent_ok)` → always log, even on failure (prevents duplicates)

Use `dry_run(store)` to preview without sending.
---

### Phase 5a — `lead-reporting` (weekly)
Trigger on: "send the weekly report", "generate the lead report", "weekly pipeline summary",
"email Heba the report", "run Phase 5 reporting".

What it does:
1. `build_report(store, period_days=7)` → aggregated metrics dict
2. `build_report_card(data)` → GWC-branded Adaptive Card from `report_teams_template.py`
3. `MICROSOFT_TEAMS_TEAMS_CREATE_CHAT` + `MICROSOFT_TEAMS_TEAMS_POST_CHAT_MESSAGE` → 1:1 DM to `hebah.yasin@gwclogistics.com`
4. `store.log_activity(gwc_id="SYSTEM", activity_type="REPORT_SENT", ...)`

---

### Phase 5b — `lead-gap-analysis` (monthly)
Trigger on: "run the gap analysis", "monthly gap report", "find pipeline problems",
"what's broken in the pipeline", "find stale leads", "check Extensia quality",
"score the notes", "run Phase 5 gap analysis".

What it does:
1. `detect_gaps(store)` → categorised gap dict (unroutable, stale, missing fields, dark, aged, rejection rate)
2. `analyze_extensia_quality(store)` → field completeness scores + notes_to_score list
3. Claude reads each lead's Notes field inline and scores 1–5 (this IS an AI call — no subagent)
4. `save_notes_scores(store, scored_leads)` → writes `notes_quality_score` + `extensia_feedback` to CSV
5. `build_gap_card(gaps, extensia)` → GWC-branded Adaptive Card from `gap_teams_template.py`
6. `MICROSOFT_TEAMS_TEAMS_CREATE_CHAT` + `MICROSOFT_TEAMS_TEAMS_POST_CHAT_MESSAGE` → 1:1 DM to `hebah.yasin@gwclogistics.com`
7. `store.log_activity(gwc_id="SYSTEM", activity_type="REPORT_SENT", ...)`
8. Optionally: `transition_to_gap_analysis(store, gwc_id)` after human SME review

---

### Dashboard — `lead-dashboard`
Trigger on: "generate the dashboard", "show me the dashboard", "build the analytics",
"update dashboard", "rebuild the dashboard", "show me pipeline performance visually",
or any request for a visual pipeline overview.

What it does:
1. `build_dashboard_data(store)` → full metrics dict (funnel, response histogram, quote analysis, rep stats)
2. `generate_dashboard(store)` → writes `leads_dashboard.html` to WORKSPACE root
3. Provide a `computer://` link to the file

---

## Correct pipeline order

When the user asks to "run the full pipeline" or "process everything":

```
Phase 1 (ingest) → [Quip enrichment — optional] → Phase 2 (route) → Phase 3 (status) → Phase 4 (cadence)
```

**Stop and report after each phase.** Never chain phases automatically without confirmation.
Exception: Phase 3 can run immediately after Phase 2 if explicitly asked.

The Quip enrichment step is optional. Run `lead-quip-enrichment` between Phase 1 and Phase 2
only when Quip is active. Skip it entirely when Quip has been retired.

If the user asks for reports or dashboard, those are independent — run them any time.

---

## Invariants — never violate these

1. **GWC ID in every subject line**: All emails sent to customers must include `GWC-XXXXXXXXX` in the subject.
2. **Always CC `Sales.rfq@gwclogistics.com`** on rep-to-customer emails (enforced in templates).
3. **Status moves forward only**: NO_ACTION → ENGAGED → QUOTED → FOLLOW_UP → WON_LOSS. Never backwards.
4. **Dark lead threshold = 5 days** of no detected CC email activity.
5. **Follow-up reminders fire once per threshold**: Deduplicated via `FOLLOW_UP_REMINDER` / `ESCALATION_REMINDER` activity log entries.
6. **All pipeline notifications go via Teams DMs** (`teams-composio`). Never use `OUTLOOK_SEND_EMAIL` for any outbound notification. Outlook (`outlook-composio`) is read-only for ingestion and status tracking only.
7. **REJECTED leads are never routed or reminded** — skip silently.
8. **CSV empty fields = `""` not `None` or `"NULL"`**. All timestamps in ISO 8601 UTC.
9. **`mode_of_freight` canonical values**: `Air`, `Sea`, `Overland` only.
10. **Never use `OUTLOOK_SEARCH_MESSAGES`** — use `OUTLOOK_QUERY_EMAILS` with OData filter instead.
11. **GAP_ANALYSIS and REJECTED are terminal** — Phase 3 never scans these leads.
12. **Notes quality scoring is inline AI** (Phase 5b only) — Claude scores the Notes field; do not subagent.
13. **Quip Country column = routing country** *(applies only when `lead-quip-enrichment` has been run)* — use `quip_country` (col G "Country" in the Digital Sales Leads section) over `to_country` for rep lookup when the lead has `in_quip_sheet = "YES"`. When Quip is retired and enrichment is skipped, route by `to_country` only.
14. **No "Unassigned" in dashboard** — every routed lead must resolve to a named primary rep. The dashboard must never show "Unassigned" as a rep row.
15. **Only one Quip sheet** *(applies only when Quip is active)* — always use `mcp__quip__get_sheet_structure` with thread `XbavARpEgyTa`; the Digital Sales Leads section starts at **row 34** (col D = GWC ID, col G = Country, col O = GWC BD POC). Never use `read_sheet` — it only returns the first embedded spreadsheet (Continental RFQ Tracker). This invariant is irrelevant once Quip is retired.

---

## Classification rules (deterministic — no AI)

| Classification | Condition |
|---|---|
| `REJECTED` | GWC ID missing OR email is not a freight inquiry |
| `PARTIALLY_QUALIFIED` | GWC ID present but ≥1 required field missing for the declared MOT |
| `QUALIFIED` | GWC ID + ALL mandatory fields present for the declared MOT |

---

## Follow-up cadence thresholds

| Status | Day thresholds | Reminder type |
|---|---|---|
| NO_ACTION (rep assigned) | 1–14 (daily) | NO_REPLY_REMINDER |
| ENGAGED | 3, 7, 14 | QUOTE_REMINDER |
| QUOTED | 2, 5, 10 | CHASER_REMINDER |
| FOLLOW_UP | 1,2,3,4,5,6,7, 10,14, 21, 28 | CLOSE_REMINDER (Day 28 = ESCALATION_REMINDER) |

---

## Quick diagnostics

If something is broken, check in this order:
1. Is the lead in `leads_maturity.csv`? → `store.get_lead("GWC-XXXXXXXXX")`
2. Is the activity log showing what happened? → `store._read_csv(store.activity_path)`
3. Is the rep assigned? → check `assigned_rep_email` field
4. Is the status correct? → check `current_status` and `status_history` JSON field
5. Did a reminder already fire for this threshold? → look for `FOLLOW_UP_REMINDER` or `ESCALATION_REMINDER` in activity log
6. Has the lead been reviewed by gap analysis? → check `notes_quality_score` and `extensia_feedback` fields

---

## Quip routing rules — applies only when Quip is active

> **These rules apply only when the `lead-quip-enrichment` skill is part of the workflow.**
> When Quip is retired, skip this section entirely and route all leads by `to_country` only.

| Rule | Detail |
|---|---|
| **Quip sheet** | Always and only connect to thread `XbavARpEgyTa` (`https://gwc1.quip.com/XbavARpEgyTa`). Use `mcp__quip__get_sheet_structure` — never `read_sheet`. |
| **Digital Sales Leads rows** | The document has two embedded spreadsheets. Digital Sales Leads start at **row 34** in `get_sheet_structure` output. Only rows ≥ 34 with a non-empty **col D** (`GWC Record ID`) are digital leads. Ignore rows 1–33 (Continental RFQ Tracker). |
| **Routing country** | Use column **G** (`Country`) — the GWC office closest to the origin/deal (e.g. "UAE", "Qatar"). This is ALWAYS the routing key, regardless of shipment destination country. |
| **BD POC** | Use column **O** (`GWC BD POC`) — the working rep name. Resolve to an email via `_resolve_poc_email()` in `quip_checker.py` against `country_rep_mapping.csv`. Store in `bd_poc_name` + `bd_poc_email`. |
| **quip_country wins** | When `quip_country` is set on a lead, it overrides `to_country` and `assigned_country` for rep lookup. Never route by destination country when `quip_country` is present. |
| **No Unassigned** | Every lead in the Quip scope must have `assigned_rep_email` set. If Phase 2 sets an UNROUTABLE status, the `quip_country` from the Country column still resolves to a valid rep. |
| **Primary rep = team lead** | Only `is_primary=TRUE` reps receive Teams DMs and appear in the dashboard. All `@gwclogistics.com` senders in CC email threads are tracked regardless of primary status. |
| **Track all, report to primary** | Phase 3 classifies any `@gwclogistics.com` sender as `role=rep` — covers all team members. Phase 4 reminders and dashboard rep performance report only primary reps. |

---

## Quip-scoped filtering — standard pattern

When the user asks to run any phase or generate any report/dashboard **"only with leads matched in Quip"**, identify the Quip-matched set from the activity log (written by Phase 2) and filter before processing:

```python
activity  = store._read_csv(store.activity_path)
quip_ids  = set(
    r['gwc_id'] for r in activity
    if r.get('activity_type') in ('REP_NOTIFIED', 'ESCALATION')
)
# quip_ids → the 32 leads successfully routed via Phase 2
```

**Why this works**: Phase 2 writes `REP_NOTIFIED` (routed to a rep) or `ESCALATION` (unroutable → escalated to manager) for every lead it processes. These are exactly the leads cross-referenced against the Quip sheet.

**Dashboard Quip-filter pattern** — wrap the store rather than modifying CSVs:

```python
class QuipFilteredStore(CSVStore):
    def __init__(self, base_store, quip_ids):
        self.__dict__ = base_store.__dict__.copy()
        self._quip_ids = quip_ids
        self._orig_read = base_store._read_csv

    def _read_csv(self, path):
        rows = self._orig_read(path)
        if path == self.leads_path:          # filter leads only; leave activity log full
            return [r for r in rows if r.get('gwc_id') in self._quip_ids]
        return rows

filtered_store = QuipFilteredStore(store, quip_ids)
html_path, json_path = generate_dashboard(filtered_store)
```

---

## Quip retirement checklist

When the organisation stops using Quip and wants to process all HubSpot emails without
any Quip cross-check, the following changes are required:

### Stop running
- `lead-quip-enrichment` skill — do not invoke it. The folder can remain for archive purposes.

### Skills to update

| Skill / File | What to change |
|---|---|
| `lead-routing/scripts/route_lead.py` | Remove the `quip_country` override block (~lines 128–152). Always route by `to_country`. |
| `lead-dashboard/scripts/dashboard_builder.py` | Remove `quip_ids` filter set and `QuipFilteredStore` usage. Remove the In/Not-in-Quip breakdown card (Tab 1). Replace all `quip_country` lookups with `to_country` or `assigned_country` (~5 locations). |
| `lead-pipeline-orchestrator/SKILL.md` | Remove the optional Quip enrichment step and its order-dependency warning. |
| `CLAUDE.md` (this file) | Remove the "Quip routing rules" section, "Quip-scoped filtering" section, invariants 13 and 15, and the Quip entry in the connectors section. Update the pipeline order diagram. |

### Not affected (zero Quip dependencies)
`lead-status-tracker`, `lead-follow-up-cadence`, `lead-reporting`, `lead-gap-analysis`
— confirmed no Quip references.

### Data prerequisites before retiring Quip
- `country_rep_mapping.csv` must have a complete rep entry for **every country** you expect
  to receive leads from. Currently, these BD POC names are in Quip but missing from the mapping:
  Mohammed Rizwan, Asra, Husam, Reema, Saad, Shihana, Umersha — add their emails first.
- For existing leads with `in_quip_sheet = "YES"`, their `assigned_rep_email` is already set
  from previous Quip-enriched runs — no re-routing needed.

---

## Known issues & proven workarounds

### 1. `gap_teams_template.py` — `most_missing_fields` must be strings, not tuples
`analyze_extensia_quality()` returns `most_missing_fields` as `list[tuple[str, int]]` (e.g. `[("incoterms", 32), ...]`).
`build_gap_card()` in `gap_teams_template.py` expects `list[str]`. Always convert before calling:

```python
top_missing_str = [f'{field} ({count})' for field, count in extensia['most_missing_fields'][:5]]
extensia['most_missing_fields'] = top_missing_str  # overwrite with strings
title, card = build_gap_card(gaps, extensia)
```

### 2. Dashboard Tab 2 ("No Response") is empty for Quip-scoped runs
Tab 2 filters on `current_status == "NO_ACTION"`. Since Phase 2 routing sets all processed leads to `ENGAGED`, **Quip-matched leads can never appear in Tab 2 by design**. This is correct behaviour. Tab 2 is only meaningful when running the dashboard over the full lead set (all 162+ leads).

### 3. `OUTLOOK_QUERY_EMAILS` — use `bodyPreview`, never `body`
Fetching the full `body` field for threads with many CC replies causes token overflow. Always request `bodyPreview` (first 255 chars) for Phase 3 thread analysis. The `build_thread_payload()` function already handles this.

### 4. Notes field contamination — UNROUTABLE error text
When Phase 2 cannot route a lead, the routing error message (e.g. `UNROUTABLE: No sales rep configured for country: 'Kazakhstan'`) is written into the `notes` field as a suffix. Phase 5b treats this as Extensia-provided content and scores it 1 (uninformative). This is a known data quality issue — the error text should ideally be stored in a separate `routing_notes` field. For now, scoring is correct: the lead's useful Extensia notes are absent.

### 5. PRE_PIPELINE (orphan) leads — backfilled in Phase 3
Leads that existed in the shared mailbox before the pipeline's go-live date (prior to April 8, 2026) have active email threads but no HubSpot ingestion record. Phase 3 detects these as orphans via `build_orphan_stub()` and backfills them with `classification = PRE_PIPELINE`. They are assigned a rep manually if identifiable from thread content, and their status is set via inline AI analysis. These leads appear in the dashboard and reports but will never have a complete `email_received_at` from HubSpot.

### 6. `generate_dashboard()` returns a tuple
`generate_dashboard(store)` returns `(html_path, json_path)` — both a browser-ready HTML file and a JSON data dump. Always unpack both:

```python
html_path, json_path = generate_dashboard(store)
```

### 7. Phase 4 cadence shows 0 tasks on first run
All leads routed in Phase 2 on the same day will have `days_elapsed = 0`. No thresholds (Day 3 / 7 / 14) will be met until those days have actually passed. Use `dry_run(store)` to preview. If testing is needed before thresholds are naturally hit, simulate tasks manually and send a consolidated test card to the manager rather than to real reps.

---

## Current pipeline state (as of April 2026 first run)

| Metric | Value |
|---|---|
| Total emails scanned (Phase 1) | 227 |
| Total leads ingested (unique GWC IDs) | 216 PARTIALLY_QUALIFIED + 1 REJECTED = 217 rows |
| Quip-matched leads (in_quip_sheet=YES) | 187 |
| Quip country breakdown | Qatar 60, UAE 56, KSA 46, Bahrain 16, Unknown 8, Oman 1 |
| REJECTED leads | 1 (11 emails with no GWC ID — all collapse to same row) |
| Phase 2 routed leads | 32 (Quip-scoped first run) |
| PRE_PIPELINE orphan leads backfilled | ~13 |
| Active pipeline (ENGAGED / QUOTED / FOLLOW_UP) | 160+ |
| Leads with notes scored (Phase 5b) | 32 (Quip scope) |
| Avg notes quality score | 2.12 / 5.00 |
| Avg field completeness (Quip scope) | 43.5% |
| Most missing fields (universal) | incoterms, packages, dimension_lwh |
| Known unroutable countries | Kazakhstan, UK, Djibouti, Kuwait, Estonia, Morocco, Libya, Kyrgyzstan, Algeria, Ireland, Tanzania, USA, India, Pakistan |
| BD POC emails missing from mapping | Mohammed Rizwan, Asra, Husam, Reema, Saad, Shihana, Umersha — add to `country_rep_mapping.csv` |
