# GWC Lead Maturity Automation

**Owner:** Heba Yasin — hebah.yasin@gwclogistics.com  
**Last updated:** April 2026  

---

## What this system does

GWC Lead Maturity Automation is a fully automated freight lead pipeline built on top of Claude (AI) and Microsoft 365. It watches a shared Outlook mailbox for HubSpot freight opportunity emails, qualifies and classifies each lead, assigns it to the right sales rep via Teams, tracks the conversation thread through a defined status pipeline, sends follow-up reminders when reps go quiet, and produces weekly performance reports and a monthly data quality audit — all without manual data entry.

```
HubSpot email → Shared mailbox → Classify → Route to rep → Track via CC emails
     → Follow-up reminders → Weekly report → Monthly gap analysis → Dashboard
```

---

## System architecture

```
Testing-leads-tracking - Copy/
│
├── data/
│   ├── leads_maturity.csv          ← Master lead table (one row per GWC ID)
│   ├── lead_activity_log.csv       ← Append-only audit trail of every action
│   └── country_rep_mapping.csv     ← Maps destination country → sales rep
│
├── skills/
│   ├── lead-pipeline-orchestrator/ ← Run all phases in one go
│   ├── lead-ingestion/             ← Phase 1: read mailbox → classify → store
│   ├── lead-routing/               ← Phase 2: assign rep → Teams DM notification
│   ├── lead-status-tracker/        ← Phase 3: scan CC email threads → update status
│   ├── lead-follow-up-cadence/     ← Phase 4: send timed reminders to reps
│   ├── lead-reporting/             ← Phase 5a: weekly pipeline summary to manager
│   ├── lead-gap-analysis/          ← Phase 5b: monthly gap & Extensia quality audit
│   ├── lead-dashboard/             ← Generate interactive HTML analytics dashboard
│   └── setup-pipeline/             ← Reset CSVs for a fresh run (testing only)
│
├── leads_dashboard.html            ← Generated analytics dashboard (open in browser)
├── leads_dashboard_data.json       ← Raw data powering the dashboard
├── CLAUDE.md                       ← AI agent instructions (do not delete)
└── README.md                       ← This file
```

---

## Lead data model

Each row in `leads_maturity.csv` represents one freight lead identified by its **GWC ID** (e.g. `GWC-757373418719`).

| Field | Description |
|---|---|
| `gwc_id` | Primary key — extracted from HubSpot email subject |
| `current_status` | Pipeline stage (see status pipeline below) |
| `classification` | QUALIFIED / PARTIALLY_QUALIFIED / REJECTED / PRE_PIPELINE |
| `company_name` | Customer company |
| `contact_name` | Primary contact |
| `from_country` / `to_country` | Origin and destination |
| `mode_of_freight` | Air / Sea / Overland |
| `product` | Commodity being shipped |
| `weight_kg` / `volume_m3` | Shipment size |
| `incoterms` | Trade term (EXW, FOB, DDP, etc.) |
| `assigned_rep_email` / `assigned_rep_name` | Sales rep responsible |
| `first_response_at` | When the rep first replied to the customer |
| `quote_sent_at` | When a price was sent |
| `follow_up_started_at` | When the customer replied to the quote |
| `deal_outcome` | WON / LOST (once closed) |
| `notes_quality_score` | AI score 1–5 for Extensia submission quality |
| `extensia_feedback` | AI explanation of the notes score |
| `status_history` | JSON array of all status transitions |

---

## Lead status pipeline

Status moves **forward only** — it can never go backwards.

```
NO_ACTION → ENGAGED → QUOTED → FOLLOW_UP → WON_LOSS
                                                    └→ GAP_ANALYSIS (terminal review)
REJECTED  (terminal — never routed or reminded)
```

| Status | Meaning |
|---|---|
| `NO_ACTION` | Lead ingested, no rep assigned yet |
| `ENGAGED` | Rep has replied to the customer (any reply counts) |
| `QUOTED` | Rep has sent a price or proposal |
| `FOLLOW_UP` | Customer replied after receiving the quote |
| `WON_LOSS` | Deal closed as won or lost |
| `REJECTED` | Not a valid freight lead (missing GWC ID or non-freight email) |
| `GAP_ANALYSIS` | Flagged for SME review after gap analysis; no further automated action |
| `PRE_PIPELINE` | Orphan lead backfilled from pre-system email threads |

---

## Phase-by-phase guide

### Phase 1 — Lead Ingestion
**What it does:** Reads the `Sales.rfq@gwclogistics.com` shared mailbox for HubSpot freight opportunity emails (`[BULK] New Freight Opportunity for GWC-XXXXXXXXX`), parses each email's structured fields, classifies the lead (QUALIFIED / PARTIALLY_QUALIFIED / REJECTED), and writes it to `leads_maturity.csv`.

**How to trigger:** Say *"check for new leads"*, *"scan the mailbox"*, or *"run Phase 1"*.

**Key rule:** Classification is deterministic (no AI). A lead is QUALIFIED only if ALL mandatory fields for its mode of freight are present.

---

### Phase 2 — Lead Routing
**What it does:** Looks up each unrouted (`NO_ACTION`, no rep assigned) lead in `country_rep_mapping.csv`, finds the right sales rep for the destination country, and sends a GWC-branded Adaptive Card via Teams 1:1 DM. Updates the lead's status to `ENGAGED` and records the rep assignment.

**How to trigger:** Say *"route new leads"*, *"assign reps"*, or *"run Phase 2"*.

**Key rule:** Unroutable leads (country not in mapping) are escalated to the manager via Teams DM. REJECTED leads are silently skipped. Already-routed leads are skipped.

---

### Phase 3 — Status Tracking
**What it does:** For every active lead (ENGAGED / QUOTED / FOLLOW_UP / NO_ACTION), queries the shared mailbox for CC'd email threads containing that lead's GWC ID. Claude reads the thread and determines whether the lead's status has advanced (e.g. ENGAGED → QUOTED). Also flags "dark leads" where the rep has stopped CCing the mailbox for 5+ days.

**How to trigger:** Say *"update lead statuses"*, *"scan email threads"*, or *"run Phase 3"*.

**Key rule:** Claude does the AI analysis inline — there is no external model call. Status can only move forward.

**PRE_PIPELINE leads:** Orphan leads with active threads but no HubSpot ingestion record are detected here and backfilled as stubs with `classification = PRE_PIPELINE`.

---

### Phase 4 — Follow-Up Cadence
**What it does:** Checks every ENGAGED / QUOTED / FOLLOW_UP lead against its cadence thresholds. If a lead has exceeded a threshold and no reminder has been sent for that threshold day yet, it sends a GWC-branded Teams DM to the assigned rep. Day 28 FOLLOW_UP leads trigger an escalation DM to the manager as well.

**Cadence schedule:**

| Status | Reminder days |
|---|---|
| ENGAGED | Day 3, 7, 14 — nudge rep to send a quote |
| QUOTED | Day 2, 5, 10 — nudge rep to chase the customer |
| FOLLOW_UP | Days 1–7 daily, then Day 10, 14, 21, 28 (Day 28 = escalation to manager) |

**How to trigger:** Say *"send follow-up reminders"*, *"run the cadence"*, or *"run Phase 4"*.

**Key rule:** Each threshold fires exactly once per lead per day — deduplicated via the activity log. Use *"dry run"* to preview without sending.

---

### Phase 5a — Weekly Report
**What it does:** Computes a weekly pipeline summary (funnel counts, new leads, rep performance, dark/stale alerts, mode of freight breakdown) and sends a GWC-branded Adaptive Card to the manager via Teams 1:1 DM.

**How to trigger:** Say *"send the weekly report"*, *"generate the lead report"*, or *"email Heba the weekly summary"*.

**Cadence:** Run every Monday morning (or on demand).

---

### Phase 5b — Monthly Gap Analysis
**What it does:** Scans all active leads for structural pipeline problems (unroutable, stale, dark, missing fields, aged 30+ days, high rejection rate) AND scores every lead's Extensia-supplied Notes field for submission quality (1–5 scale). Sends a GWC-branded gap analysis report to the manager via Teams DM. Scores and feedback are written back to `leads_maturity.csv`.

**How to trigger:** Say *"run the gap analysis"*, *"check Extensia quality"*, or *"score the notes"*.

**Cadence:** Run once per month.

**Notes scoring rubric (AI-scored by Claude, not Extensia):**

| Score | Meaning |
|---|---|
| 1 | Empty, "nil", "N/A", or system error text — no useful content |
| 2 | Minimal — single keyword ("customs needed", "ETD next week") |
| 3 | Partial — one useful detail but missing specifics |
| 4 | Good — enough for a rep to start a productive first conversation |
| 5 | Excellent — ETD, packaging, customs, specific requirements all present |

---

### Dashboard — Interactive Analytics
**What it does:** Generates a self-contained HTML file (`leads_dashboard.html`) with 8 interactive tabs powered by Chart.js. All charts support date-range filtering. No server required — open in any browser.

**How to trigger:** Say *"generate the dashboard"*, *"show me the analytics"*, or *"rebuild the dashboard"*.

**Dashboard tabs:**

| Tab | Content |
|---|---|
| 1 — Pipeline Overview | KPI cards, funnel bar chart, mode-of-freight donut, 60-day arrivals trend |
| 2 — No Response | NO_ACTION leads by destination country, overdue counts, lead age histogram |
| 3 — Engagement | Rep response-speed histogram (Day 1–15+), cumulative %, breakdown by MOT |
| 4 — Quoting & Follow-Up | Quote age distribution, engagement→quote gap, cadence burn-down by week |
| 5 — Won / Loss | Outcome donut, close-age stacked bar, deal detail table |
| 6 — Rep Performance | Leaderboard, response time vs SLA, volume bar chart |
| 7 — Data Quality | Field completeness bars, MOT × field heatmap, missing-field gap patterns |
| 8 — Notes Intelligence | AI score distribution, keyword coverage, Extensia training samples |

> **Note:** Tab 2 (No Response) is empty when the dashboard is run on Quip-matched leads only, because all Quip-matched leads were routed in Phase 2 and are therefore `ENGAGED` or higher — none remain as `NO_ACTION`. Run the dashboard on all leads to populate Tab 2.

---

## Connectors required

| Connector | Purpose |
|---|---|
| `outlook-composio` | Read emails from `Sales.rfq@gwclogistics.com` (read-only) |
| `teams-composio` | Send Adaptive Card DMs to reps and manager |
| `quip` | Cross-reference leads against the Quip tracking sheet |

> **Important:** Outlook is used **only for reading**. All outbound notifications go via Teams DMs. Never use `OUTLOOK_SEND_EMAIL` for pipeline notifications.

---

## Key contacts & constants

| Item | Value |
|---|---|
| Shared mailbox | `Sales.rfq@gwclogistics.com` |
| Manager (report recipient) | `hebah.yasin@gwclogistics.com` |
| HubSpot sender | `jakub.skopec@gwclogistics.com` |
| GWC ID format | `GWC-` followed by 12 digits |
| HubSpot email subject pattern | `[BULK] New Freight Opportunity for GWC-XXXXXXXXX` |
| Dark lead threshold | 5 days of no CC email activity |
| Rejection rate alert threshold | > 30% of total leads |

---

## Extensia integration notes

Extensia is an external call centre (generalists, not freight specialists) that handles the initial customer intake call and submits structured lead data via HubSpot. The quality of their submissions directly determines how quickly reps can quote.

**Common gaps found in April 2026 first run:**
- `incoterms`, `packages`, and `dimension_lwh` were missing on **100% of leads** — these are not in Extensia's current intake script.
- Average field completeness: **43.5%** (target: 80%+).
- Average notes quality: **2.12 / 5** — majority of notes are either empty or contain only a single keyword.
- 19 of 32 Quip-matched leads scored ≤ 2 and are flagged for Extensia training.

**Recommended actions:**
1. Add incoterms, packages, and dimension fields to the Extensia intake form.
2. Brief agents to capture: ETD, customs requirement (yes/no), service type (door-to-door vs port-to-port).
3. Use the Notes Intelligence tab in the dashboard (Tab 8) to identify best and worst examples for training material.

---

## Country rep mapping

Maintained in `data/country_rep_mapping.csv`. Leads whose destination country is not in this file are flagged as **unroutable** and escalated to the manager.

**Countries currently unroutable (as of April 2026):** Kazakhstan, UK, Djibouti, Kuwait, Estonia, Morocco, Libya, Kyrgyzstan, Algeria, Ireland, Tanzania, USA, India, Pakistan.

To add a new country, add a row to `country_rep_mapping.csv` with the country name and rep email, then re-run Phase 2 — any unrouted leads for that country will be picked up automatically.

---

## Running the pipeline

**Full pipeline (all phases):**
> *"Run the full pipeline"* or *"process all leads"*

**Individual phases:**
> *"Check for new leads"* → Phase 1  
> *"Route leads"* → Phase 2  
> *"Update lead statuses"* → Phase 3  
> *"Send follow-up reminders"* → Phase 4  
> *"Send the weekly report"* → Phase 5a  
> *"Run the gap analysis"* → Phase 5b  
> *"Generate the dashboard"* → Dashboard  

**Quip-scoped run (only Quip-matched leads):**
> Append *"only with leads matched in Quip"* to any phase request.

**Reset for a fresh test run:**
> *"Reset the pipeline"* or *"set up the pipeline"* — wipes `leads_maturity.csv` and `lead_activity_log.csv` back to headers only. `country_rep_mapping.csv` is never touched.

---

## Output files

| File | Description |
|---|---|
| `data/leads_maturity.csv` | Master lead table — updated by every pipeline phase |
| `data/lead_activity_log.csv` | Audit log — every action ever taken, append-only |
| `leads_dashboard.html` | Interactive analytics dashboard — open in any browser |
| `leads_dashboard_data.json` | Raw JSON data powering the dashboard |

---

## Troubleshooting

| Symptom | Check |
|---|---|
| Lead not appearing in reports | Is it in `leads_maturity.csv`? Check `gwc_id` and `current_status` |
| Rep never received a DM | Check `lead_activity_log.csv` for `REP_NOTIFIED` entry for that `gwc_id` |
| Lead stuck in NO_ACTION | Is the country in `country_rep_mapping.csv`? Run Phase 2 |
| Reminder not sent | Check activity log for existing `FOLLOW_UP_REMINDER` at that threshold day — deduplication may have blocked it |
| Status went backwards | This should never happen — check `parse_analysis_result()` forward-only guard in Phase 3 |
| Dashboard Tab 2 empty | Expected if running on Quip-matched scope only — all Quip leads are ENGAGED or higher |
| Gap analysis card build error | Convert `most_missing_fields` tuples to strings before calling `build_gap_card()` |
