---
name: lead-dashboard
description: >
  GWC Lead Maturity Automation — analytics dashboard skill. Reads leads_maturity.csv
  and lead_activity_log.csv, computes all pipeline metrics, and generates a standalone
  GWC-branded HTML dashboard. Features 8 tabs: Pipeline Overview, No Response (inaction
  by country), Engagement (response histogram + cumulative %), Quoting & Follow-Up
  (cadence charts), Won/Loss (outcome donut), Rep Performance (leaderboard + SLA chart),
  Data Quality (field completeness + MOT heatmap), and Notes Intelligence (AI score
  distribution, freight keyword coverage, Extensia training samples, enriched by
  freight_domain_knowledge.md). Interactive Chart.js
  visualisations — no server needed, opens in any browser.
  Trigger on: "generate the dashboard", "show me the dashboard", "build the analytics",
  "create the lead report dashboard", "open the dashboard", "rebuild the dashboard",
  "update dashboard", "show pipeline charts", "show me performance visually", or any
  variation of wanting a visual overview of the pipeline.
---

# Lead Dashboard Skill

Generates a self-contained HTML analytics dashboard for the GWC lead pipeline and a JSON file showing the output of dashboard_builder.py.

## Prerequisites & paths

```
DATA_DIR  = WORKSPACE/data/
SCRIPTS   = WORKSPACE/skills/lead-dashboard/scripts/
  dashboard_builder.py — CSV aggregator (all 7-tab metrics)
  dashboard_html.py    — standalone HTML generator (Chart.js, 7 tabs, date filter)

SHARED_SCRIPTS = WORKSPACE/skills/lead-ingestion/scripts/
  csv_store.py         — CSV read/write (shared)

OUTPUT = WORKSPACE/leads_dashboard.html   ← open this in browser
```

Replace `WORKSPACE` with the actual resolved path to the user's selected folder.

---

## Step-by-step execution

### Step 1 — Resolve workspace path

```python
import sys
from pathlib import Path

# Resolve dynamically — never hardcode session paths
WORKSPACE = str(Path(store.leads_path).parent.parent)
```

### Step 2 — Generate the dashboard

```python
sys.path.insert(0, f"{WORKSPACE}/skills/lead-ingestion/scripts")
sys.path.insert(0, f"{WORKSPACE}/skills/lead-dashboard/scripts")

from csv_store import CSVStore
from dashboard_html import generate_dashboard

store = CSVStore(f"{WORKSPACE}/data")
html_path, json_path = generate_dashboard(store)
print(f"Dashboard HTML : {html_path}")
print(f"Dashboard JSON : {json_path}")
```

`generate_dashboard()` returns a **tuple `(html_path, json_path)`**:
- `html_path` — the self-contained browser dashboard (`leads_dashboard.html`)
- `json_path` — a pretty-printed JSON dump of every metric, bucket, and enriched
  lead row produced by `build_dashboard_data()` (`leads_dashboard_data.json`)

The JSON file is written alongside the HTML and is the authoritative trace of exactly
what data was fed into the dashboard at generation time — useful for auditing, debugging,
or piping into other tools.

### Step 3 — Confirm and link both files

After the script runs, confirm both files were created and provide `computer://` links
for each:

```
computer:///path/to/workspace/leads_dashboard.html
computer:///path/to/workspace/leads_dashboard_data.json
```

### Step 4 — Log

```python
from datetime import datetime, timezone
store.log_activity(
    gwc_id="SYSTEM",
    activity_type="DASHBOARD_GENERATED",
    detail={
        "html_output":  html_path,
        "json_output":  json_path,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    },
    performed_by="SYSTEM",
)
```

---

## Dashboard tabs

| Tab | Key visualisations |
|---|---|
| **1 — Pipeline Overview** | Key KPI cards, pipeline funnel bar, mode-of-freight donut, 60-day arrivals trend |
| **2 — No Response** | Inaction by destination country (count + avg age), lead age histogram, no-response detail table |
| **3 — Engagement** | Day 1–15 response histogram, cumulative % line with 50%/80% reference lines, response speed by MOT grouped bars |
| **4 — Quoting & Follow-Up** | Lead age at quote, engagement→quote gap, follow-up age distribution, cadence reminder burn-down by week |
| **5 — Won / Loss** | Outcome donut (Won / Lost / Active / Rejected), close-age stacked bar, deal detail table |
| **6 — Rep Performance** | Volume stacked bar, avg response vs SLA line, sortable leaderboard table |
| **7 — Data Quality** | Field completeness progress bars, top missing-field combinations, MOT × field fill-rate heatmap |


## Date filter

All charts re-compute from the embedded JSON when the user changes the filter:
- **All time** (default)
- **Last 7 / 30 / 90 days** (from `email_received_at`)
- **Custom** (date range picker)

## Script reference

| Script | Key export |
|---|---|
| `dashboard_builder.py` | `build_dashboard_data(store) → dict` |
| `dashboard_html.py`