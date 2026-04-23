---
name: lead-quip-enrichment
description: >
  GWC Lead Maturity Automation — Optional Quip enrichment skill.
  Cross-checks ingested leads against the "Digital Sales Leads" section of the
  Regional Contingency RFQ Tracker Quip document (thread XbavARpEgyTa).
  Sets quip_country, bd_poc_name, bd_poc_email, in_quip_sheet, quip_updates_raw,
  and quip_updates_summary on each lead.
  Run AFTER Phase 1 (lead-ingestion) and BEFORE Phase 2 (lead-routing) so that
  quip_country is available for routing decisions.
  Trigger when the user says: "run Quip enrichment", "cross-check with Quip",
  "enrich leads from Quip", "sync Quip data", or when the orchestrator prompts
  for optional Quip enrichment between Phase 1 and Phase 2.
  OPTIONAL: skip entirely if the organisation is no longer using Quip.
---

# Lead Quip Enrichment Skill

Cross-checks every lead in `leads_maturity.csv` that has `in_quip_sheet = ""`
against the Digital Sales Leads section of the Quip tracker. Writes six Quip-
derived fields back to the CSV and logs a `QUIP_ENRICHED` activity per lead found.

> **This skill is optional.** If Quip is no longer in use, skip it entirely.
> Phase 2 routing will fall back to `to_country` for rep assignment.

---

## Prerequisites & paths

```
DATA_DIR   = <workspace>/data/
SCRIPTS    = <workspace>/skills/lead-quip-enrichment/scripts/
  quip_checker.py   — sheet parser + lead cross-check (moved from lead-ingestion)
  csv_store.py      — imported from lead-ingestion/scripts (shared store)
```

Replace `<workspace>` with the actual selected workspace folder path.

## Connector

**quip** — `mcp__quip__get_sheet_structure`

---

## Step-by-step execution

### Step 1 — Load the CSV store and identify leads to enrich

```python
import sys
sys.path.insert(0, "<workspace>/skills/lead-ingestion/scripts")
from csv_store import CSVStore
store = CSVStore("<workspace>/data")

all_leads = store._read_csv(store.leads_path)
to_enrich = [l for l in all_leads if l.get("in_quip_sheet", "") == ""]
print(f"[Quip Enrichment] {len(to_enrich)} leads pending Quip cross-check")
```

If `to_enrich` is empty, print:
> "All leads already have Quip cross-check results. Nothing to do."
Then stop.

---

### Step 2 — Load the Quip sheet once (single MCP call)

**Always use `mcp__quip__get_sheet_structure`** — NOT `mcp__quip__read_sheet`.
The Quip document (`XbavARpEgyTa`) contains two embedded spreadsheets;
`read_sheet` returns only the first (Continental RFQ Tracker) and misses Digital Sales Leads.

Call `mcp__quip__get_sheet_structure` with:
- `thread_id`: `"XbavARpEgyTa"`

Capture the raw text output of the tool response, then:

```python
sys.path.insert(0, "<workspace>/skills/lead-quip-enrichment/scripts")
from quip_checker import load_from_structure_data

# structure_text = raw string output from mcp__quip__get_sheet_structure
quip_data = load_from_structure_data(structure_text)
if quip_data:
    print(f"[Quip] Loaded {len(quip_data)} Digital Sales Leads from Quip")
else:
    print("[Quip] Warning: Quip sheet unavailable or empty. Marking all pending leads as in_quip_sheet=NO.")
```

> **If the MCP call fails**: set `quip_data = {}` and continue.
> All pending leads will receive `in_quip_sheet = "NO"`. Do NOT retry per lead.

---

### Step 3 — Cross-check each lead (in-memory, no further MCP calls)

```python
from quip_checker import check_lead_in_quip

found_count = 0
not_found_count = 0

for lead in to_enrich:
    gwc_id = lead["gwc_id"]
    quip_result = check_lead_in_quip(gwc_id, quip_data, workspace_dir="<workspace>")

    updates = {
        "in_quip_sheet":        "YES" if quip_result["found_in_quip"] else "NO",
        "quip_updates_raw":     quip_result["raw_updates"],
        "quip_updates_summary": quip_result["updates_summary"],
        "quip_country":         quip_result.get("quip_country", ""),
        "bd_poc_name":          quip_result.get("bd_poc_name", ""),
        "bd_poc_email":         quip_result.get("bd_poc_email", ""),
    }
    store.update_lead_field(gwc_id, updates)

    if quip_result["found_in_quip"]:
        found_count += 1
        # If quip_updates_raw is non-empty, fill quip_updates_summary inline:
        # Substitute the lead's updates into QUIP_SUMMARY_PROMPT from quip_checker.py
        # and write a concise 2-3 sentence summary back to the CSV.
        if quip_result["raw_updates"]:
            # Claude: generate the summary inline here
            pass
    else:
        not_found_count += 1

    store.log_activity(
        gwc_id=gwc_id,
        activity_type="QUIP_ENRICHED",
        detail={
            "found_in_quip":  quip_result["found_in_quip"],
            "quip_country":   quip_result.get("quip_country", ""),
            "bd_poc_name":    quip_result.get("bd_poc_name", ""),
            "has_updates":    bool(quip_result["raw_updates"]),
        },
        performed_by="SYSTEM",
    )
```

---

### Step 4 — Flush to Databricks (combined, single round trip per type)

**Critical performance rule — do NOT generate individual UPDATE statements per lead.**
Collect all enrichment results in memory during Step 3, then flush in bulk here.
This reduces what would be 100+ Databricks round trips to ≤5.

```python
import sys
sys.path.insert(0, f"{WORKSPACE}/skills/lead-ingestion/scripts")
from db_sync import (
    generate_sql_statements, generate_batch_update_sql,
    generate_batch_insert_activity_sql, clear_queue
)

queue_path = f"{WORKSPACE}/data/pending_writes.jsonl"

# ── A. Flush any deferred Phase 1 writes (if Phase 1 did not flush yet) ───────
phase1_stmts = generate_sql_statements(queue_path)
if phase1_stmts:
    print(f"Flushing {len(phase1_stmts)} deferred Phase 1 write(s)...")
    # Execute each stmt via execute_sql() — parallel batches of 8 are fine
    clear_queue(queue_path)

# ── B. Quip lead updates — ONE MERGE covers all enriched leads ─────────────────
# `quip_updates` = list of (gwc_id, updates_dict) built during Step 3's loop:
#   quip_updates.append((lead["gwc_id"], {
#       "in_quip_sheet": ..., "quip_country": ...,
#       "bd_poc_name":   ..., "bd_poc_email":  ..., "updated_at": now_iso,
#   }))
batch_update_sql = generate_batch_update_sql(quip_updates)
print(f"Executing 1 batch MERGE for {len(quip_updates)} Quip-enriched leads...")
# execute_sql(batch_update_sql)

# ── C. Activity log — batched multi-row INSERTs (~3 statements for 58 rows) ────
# `activity_rows` = list of dicts built during Step 3's store.log_activity calls,
#   captured by temporarily subclassing or post-reading the activity CSV delta.
batch_activity_stmts = generate_batch_insert_activity_sql(activity_rows)
print(f"Executing {len(batch_activity_stmts)} batched activity INSERT(s) "
      f"for {len(activity_rows)} QUIP_ENRICHED events...")
# for stmt in batch_activity_stmts:
#     execute_sql(stmt)

print(f"✅ Databricks flush complete — {len(quip_updates)} leads updated, "
      f"{len(activity_rows)} activities logged in "
      f"{1 + len(batch_activity_stmts)} total SQL statements.")
```

> **How to collect `quip_updates` and `activity_rows`**: build them in the Step 3 loop
> alongside the `store.update_lead_field()` and `store.log_activity()` calls. No extra
> reads needed — you already have the data in memory.

---

### Step 5 — Report summary

```
✅ Quip Enrichment Complete
─────────────────────────────────────────
Leads checked:        [N]
  Found in Quip:      [N]
  Not in Quip:        [N]

quip_country set:     [N]  (routing will use Quip country for these leads)
bd_poc resolved:      [N]  (BD POC email matched in country_rep_mapping.csv)
BD POC unresolved:    [N]  (name found in Quip, no email match — add to country_rep_mapping.csv)

Databricks flush:     1 MERGE + [N] activity batch(es)
```

If any BD POC names were unresolved (name found but no email), list them:
```
⚠️  Unresolved BD POC names (add to country_rep_mapping.csv):
  - [poc_name] (GWC-XXXXXXXXXX)
```

Then say:
> "Ready for Phase 2 — run `lead-routing` to assign reps and send notifications.
>  Leads with `quip_country` set will be routed by Quip country; others by `to_country`."

---

## Script reference

| Script | Location | Key functions |
|--------|----------|---------------|
| `quip_checker.py` | `skills/lead-quip-enrichment/scripts/` | `load_from_structure_data(text)` · `check_lead_in_quip(gwc_id, data, workspace_dir)` |
| `csv_store.py` | `skills/lead-ingestion/scripts/` (shared) | `CSVStore(data_dir)` · `.update_lead_field()` · `.log_activity()` |

---

## Quip sheet layout (thread `XbavARpEgyTa`)

| Rows | Sheet | Key columns used |
|------|-------|-----------------|
| 1–33 | Continental RFQ Tracker | **Ignored** — different schema |
| 34+  | Digital Sales Leads | **D** = GWC Record ID · **G** = Country (routing) · **O** = GWC BD POC · **R+** = updates |

`load_from_structure_data()` automatically filters to rows ≥ 34.

---

## Fields written to leads_maturity.csv

| Field | Source | Notes |
|-------|--------|-------|
| `in_quip_sheet` | Quip lookup result | `"YES"` or `"NO"` |
| `quip_country` | Quip col G "Country" | Routing override for Phase 2 |
| `bd_poc_name` | Quip col O "GWC BD POC" | Rep name from Quip |
| `bd_poc_email` | Resolved via `country_rep_mapping.csv` | May be `""` if name not in mapping |
| `quip_updates_raw` | Quip cols R+ (pipe-joined) | Raw update history |
| `quip_updates_summary` | Inline AI summary | Claude-generated summary of updates |

---

## When Quip is retired

When the organisation stops using Quip:
1. **Stop running this skill** — do not invoke it.
2. `in_quip_sheet`, `quip_country`, `quip_updates_raw`, `quip_updates_summary`,
   `bd_poc_name`, `bd_poc_email` columns in `leads_maturity.csv` become archive-only.
3. Phase 2 (`lead-routing`) will route purely by `to_country` — no changes needed
   as long as `quip_country` is empty for new leads.
4. See `CLAUDE.md` → "Quip retirement checklist" for the full list of files to update.
