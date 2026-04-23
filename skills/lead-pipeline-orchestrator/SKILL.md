---
name: lead-pipeline-orchestrator
description: >
  GWC Lead Maturity Automation — Master pipeline orchestrator skill.
  Runs all phases of the lead automation pipeline in the correct order:
  Phase 1 (ingest) → Phase 2 (route) → Phase 3 (status tracker) → Phase 4 (cadence).
  Phases 2 and 4 notify via Microsoft Teams 1:1 DMs (teams-composio connector).
  Phases 5a and 5b send reports to the manager via Teams 1:1 DM.
  Supports full pipeline runs, partial runs, single-phase runs, and reporting/dashboard on demand.
  Trigger when the user says: "run the full pipeline", "run everything", "process all leads",
  "run all phases", "start the pipeline", "automate everything", "run the automation",
  "what's new today", "process and route leads", "catch up on all leads",
  or any variation of wanting to run multiple pipeline phases together.
  Also trigger if the user says "run phase X through Y", "run phases 1 and 2", etc.
  For a single phase in isolation use the individual skill — but when two or more phases
  are requested together, always use this orchestrator.
---

# Lead Pipeline Orchestrator

Coordinates all GWC lead automation phases in the correct order, stopping after each
phase to report results and (where configured) waiting for confirmation before continuing.

---

## Pipeline architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  CORE PIPELINE (run in sequence, stop-and-report between phases) │
│                                                                   │
│  Phase 1 → [Quip?] → Phase 2 → Phase 3 → Phase 4               │
│  Ingest    optional   Route     Status    Cadence                │
│            enrichment                                            │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  REPORTING (independent — run anytime, no phase dependency)      │
│                                                                   │
│  Phase 5a (weekly report)   Phase 5b (gap analysis)   Dashboard  │
└─────────────────────────────────────────────────────────────────┘
```

> **[Quip?]** = `lead-quip-enrichment` skill (optional).
> Run it when Quip is in use so that `quip_country` and `bd_poc_email` are
> available before Phase 2 routing. Skip entirely if Quip has been retired.

**Critical rule**: Never chain phases automatically without stopping to report.
The only exception: if the user explicitly says "run phases X and Y together" or
"run without stopping", you may continue. Otherwise always pause and summarise.

---

## Step 0 — Determine run mode

Before running anything, determine what the user actually wants:

| User intent | Run mode |
|---|---|
| "run the full pipeline" / "run everything" / "process all leads" | `FULL` — phases 1→4 |
| "check for new leads and route them" / "run phases 1 and 2" | `PARTIAL` — phases 1→2 |
| "update statuses and send reminders" / "run phases 3 and 4" | `PARTIAL` — phases 3→4 |
| "send the weekly report" / "run the gap analysis" | `REPORT` — phase 5a or 5b |
| "show me the dashboard" | `DASHBOARD` — dashboard only |
| "run phase X only" | `SINGLE` — one phase |

Announce the mode before starting:
```
🚀 Starting GWC Lead Pipeline — [MODE]
Running: [list of phases]
──────────────────────────────────────
```

---

## Step 1 — Resolve WORKSPACE path

```python
import sys, os
from pathlib import Path

# WORKSPACE is the folder containing data/ and skills/
# Resolve it from the location of this script
WORKSPACE = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, f"{WORKSPACE}/skills/lead-ingestion/scripts")
from csv_store import CSVStore
store = CSVStore(f"{WORKSPACE}/data")
print(f"Workspace: {WORKSPACE}")
print(f"Leads in DB: {len(store.get_all_gwc_ids())}")
```

---

## Phase 1 execution — Lead Ingestion

**Read and follow**: `skills/lead-ingestion/SKILL.md`

After Phase 1 completes, report:
```
✅ Phase 1 — Ingestion Complete
──────────────────────────────────────
Emails scanned:      [N]
New leads added:     [N]  (QUALIFIED: X | PARTIAL: Y | REJECTED: Z)
Already in DB:       [N]
```

**Gate check**: If `new leads added == 0`, say:
> "No new leads found. Pipeline stopping after Phase 1 — nothing to route."
> Then stop (unless the user asked to continue regardless).

**Proceed?** In FULL mode, prompt for optional Quip enrichment (see below), then continue to Phase 2.
In other modes, stop here unless explicitly chaining.

---

## Optional — Quip Enrichment (between Phase 1 and Phase 2)

**Read and follow**: `skills/lead-quip-enrichment/SKILL.md`

> **Skip this step entirely if Quip is no longer in use.** Proceed directly to Phase 2.

When Quip is active, run this after Phase 1 and before Phase 2 so that
`quip_country` and `bd_poc_email` are populated for routing decisions.

In FULL mode, ask the user (or check context):
> "Run Quip enrichment before routing? (Quip is currently active — recommended yes.
>  Skip if Quip has been retired.)"

**⚡ Deferred-flush pattern — when Phase 1 + Quip run together:**
1. Tell Phase 1 to **skip its Databricks flush** (leave `pending_writes.jsonl` intact)
2. Quip enrichment Step 4 flushes Phase 1 writes + Quip updates in a combined operation:
   - Phase 1 upserts (individual MERGEs, batched ×8 in parallel)
   - Quip updates → **1 batch MERGE** via `generate_batch_update_sql()` (all leads, one call)
   - Activity rows → **≤4 batched INSERTs** via `generate_batch_insert_activity_sql()`
3. This reduces ~160 individual Databricks calls to ~20 for a typical 50-lead ingestion run.

After Quip enrichment completes, report:
```
✅ Quip Enrichment Complete
──────────────────────────────────────
Leads checked:       [N]
  Found in Quip:     [N]
  Not in Quip:       [N]
  BD POC unresolved: [N]  ← add these names to country_rep_mapping.csv

Databricks flush:    [N] upserts + 1 Quip MERGE + [N] activity batch(es)
```

⚠️ **Order dependency**: If Phase 2 runs before Quip enrichment, routing will use
`to_country` instead of `quip_country`. Both are valid, but Quip country is authoritative
when the organisation is using Quip.

---

## Phase 2 execution — Lead Routing

**Read and follow**: `skills/lead-routing/SKILL.md`

After Phase 2 completes, report:
```
✅ Phase 2 — Routing Complete
──────────────────────────────────────
Leads processed:     [N]
  Routed to reps:    [N]
  Unroutable:        [N]  (manager notified)
  Skipped (REJECTED):[N]
```

**Gate check**: If `routed == 0`, say:
> "No leads were routed (all were either already assigned or unroutable). Continuing..."

**Proceed?** In FULL mode, continue to Phase 3.

---

## Phase 3 execution — Status Tracker

**Read and follow**: `skills/lead-status-tracker/SKILL.md`
**Also read**: `skills/lead-status-tracker/references/freight_domain_knowledge.md`

After Phase 3 completes, report:
```
✅ Phase 3 — Status Tracker Complete
──────────────────────────────────────
Active leads scanned: [N]
Status changes:       [N]
  [list changes: GWC-XXXX: OLD → NEW]
Dark leads flagged:   [N]
  [list dark leads if any]
No change:            [N]
```

**Proceed?** In FULL mode, continue to Phase 4.

---

## Phase 4 execution — Follow-up Cadence

**Read and follow**: `skills/lead-follow-up-cadence/SKILL.md`

After Phase 4 completes, report:
```
✅ Phase 4 — Cadence Complete
──────────────────────────────────────
Reminders due:    [N]
  Sent:           [N]
  Escalations:    [N]  (manager CC'd)
  Failed:         [N]
```

---

## Phase 5a execution — Weekly Report

**Read and follow**: `skills/lead-reporting/SKILL.md`

Independent of the core pipeline. Run when the user asks for a "weekly report" or "pipeline summary".

---

## Phase 5b execution — Gap Analysis

**Read and follow**: `skills/lead-gap-analysis/SKILL.md`

Independent of the core pipeline. Run when the user asks for "gap analysis", "Extensia quality", or "monthly report".
Includes inline AI scoring of Notes fields — Claude scores each entry's `notes` field 1–5.

---

## Dashboard execution

**Read and follow**: `skills/lead-dashboard/SKILL.md`

Independent of the core pipeline. Run when the user asks for the "dashboard" or a visual overview.

The dashboard has **7 tabs**:
1. **Pipeline Overview** — funnel, arrivals trend, mode donut
2. **No Response** — inaction by destination country, overdue leads
3. **Engagement** — response speed histogram, cumulative %, MOT breakdown
4. **Quoting & Follow-Up** — quote age, engagement→quote gap, cadence burn-down, follow-up aging
5. **Won / Loss** — outcome donut, close-age distribution, deal detail table
6. **Rep Performance** — leaderboard, volume bar, avg response vs SLA
7. **Data Quality** — field completeness bars, gap patterns, MOT × field heatmap

---

## Final summary (FULL or PARTIAL mode only)

After all requested phases complete, print:

```
═══════════════════════════════════════════════════════
🏁  GWC Lead Pipeline — Run Complete
═══════════════════════════════════════════════════════
Phases completed:    [list]
Total new leads:     [N from Phase 1]
Leads routed:        [N from Phase 2]
Status changes:      [N from Phase 3]
Reminders sent:      [N from Phase 4]
Escalations:         [N]
Dark leads:          [N]

⚠️  Items requiring attention:
  [list unroutable leads, dark leads, escalated leads]

Next suggested actions:
  • Run Phase 5a for weekly pipeline summary email
  • Run Phase 5b for Extensia submission quality analysis
  • Run dashboard to view visual analytics
═══════════════════════════════════════════════════════
```

---

## Error handling

If any phase fails or errors mid-run:
1. Print the error clearly with the phase name
2. Log it as `PIPELINE_ERROR` in the activity log if possible:
   ```python
   store.log_activity(
       gwc_id="SYSTEM",
       activity_type="PIPELINE_ERROR",
       detail={"phase": "Phase N", "error": str(e)},
       performed_by="SYSTEM"
   )
   ```
3. Ask the user: "Phase N failed — do you want to continue with Phase N+1 or stop here?"
4. Never silently swallow errors. Surface them so the user can act.

---

## Quick-reference: skill paths

| Phase | SKILL.md location |
|---|---|
| Phase 1 | `skills/lead-ingestion/SKILL.md` |
| Quip (optional) | `skills/lead-quip-enrichment/SKILL.md` |
| Phase 2 | `skills/lead-routing/SKILL.md` |
| Phase 3 | `skills/lead-status-tracker/SKILL.md` |
| Phase 4 | `skills/lead-follow-up-cadence/SKILL.md` |
| Phase 5a | `skills/lead-reporting/SKILL.md` |
| Phase 5b | `skills/lead-gap-analysis/SKILL.md` |
| Dashboard | `skills/lead-dashboard/SKILL.md` |
| Orchestrator | `skills/lead-pipeline-orchestrator/SKILL.md` ← you are here |

All paths are relative to WORKSPACE (the selected folder).
