---
name: setup-pipeline
description: >
  GWC Lead Maturity Automation — pipeline reset skill.
  Wipes leads_maturity.csv, lead_activity_log.csv, and the pending_writes.jsonl
  queue back to empty, then truncates the matching Databricks tables so local
  CSVs and Databricks stay in sync. Use before a fresh test run or after a
  data-integrity failure. country_rep_mapping.csv and the Databricks
  country_rep_mapping table are NEVER touched.
  Trigger when the user says: "reset the pipeline", "clear the data",
  "start fresh", "wipe the CSVs", "empty the leads", "setup the pipeline",
  "prepare for a fresh run", or any variation of wanting a clean slate.
---

# Setup Pipeline Skill

Resets the pipeline to a clean state — both locally (CSVs + queue) and in
Databricks (`leads_maturity` + `lead_activity_log` tables).

> ⚠️ This is destructive — all lead data and activity history will be lost
> from both local CSVs and Databricks. `country_rep_mapping` is never touched.

## Prerequisites & paths

```
DATA_DIR  = WORKSPACE/data/
SCRIPTS   = WORKSPACE/skills/setup-pipeline/scripts/
  reset_pipeline.py — reset logic
```

Replace `WORKSPACE` with the actual path to the user's selected folder.

## Step-by-step execution

### Step 1 — Run the reset script (clears CSVs + queue)

```python
import sys
WORKSPACE = "<actual workspace path>"
sys.path.insert(0, f"{WORKSPACE}/skills/setup-pipeline/scripts")
from reset_pipeline import reset_pipeline

result = reset_pipeline(WORKSPACE)
print(result["message"])
```

The script:
- Clears `leads_maturity.csv` to header-only
- Clears `lead_activity_log.csv` to header-only
- Wipes `data/pending_writes.jsonl` (prevents stale queued writes)
- Returns `result["databricks_sql"]` — a list of SQL statements for Step 2

### Step 2 — Truncate Databricks tables

Execute each SQL statement from `result["databricks_sql"]` via the
Databricks MCP connector (`mcp__62f760ee-bfcc-4f93-bec8-cdf2d76870ad`):

```python
# result["databricks_sql"] contains:
# [
#   "DELETE FROM claude_prototyping.marketing.leads_maturity",
#   "DELETE FROM claude_prototyping.marketing.lead_activity_log",
# ]
```

Call `execute_sql()` for each statement in order. Confirm `num_affected_rows`
is returned for each (any number ≥ 0 is a success).

### Step 3 — Confirm and report

After both steps succeed, report:

```
✅ Pipeline Reset Complete
──────────────────────────────────────────────
leads_maturity.csv        → cleared (header only)
lead_activity_log.csv     → cleared (header only)
pending_writes.jsonl      → cleared
Databricks leads_maturity → truncated
Databricks lead_activity_log → truncated
country_rep_mapping       → untouched (local + Databricks)

Ready for a fresh pipeline run.
Run Phase 1 (lead-ingestion) to start ingesting emails from the mailbox.
```

## Script reference

| Script | Key export |
|---|---|
| `reset_pipeline.py` | `reset_pipeline(workspace_path) → dict` |

`dict` keys: `success`, `leads_cleared`, `activity_cleared`, `queue_cleared`,
`databricks_sql` (list of SQL to execute), `message`.
