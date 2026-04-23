---
name: lead-gap-analysis
description: >
  GWC Lead Maturity Automation — Phase 5b monthly gap analysis skill.
  Scans leads_maturity.csv for structural pipeline problems AND analyses Extensia
  submission quality (field completeness + Notes quality scoring).
  Generates a GWC-branded HTML gap analysis report and emails it to the manager.
  Trigger when the user says: "run the gap analysis", "monthly gap report",
  "find pipeline problems", "what's broken in the pipeline", "analyse lead gaps",
  "run Phase 5 gap analysis", "find stale leads", "check Extensia quality",
  "score the notes", "Extensia quality report", or any variation of wanting a
  deep diagnostic of the lead pipeline health or submission quality.
---

# Lead Gap Analysis Skill

Diagnoses structural problems in the GWC freight lead pipeline AND analyses
Extensia submission quality — emailing a monthly gap report to the manager.

> **Context**: Extensia is an external call centre (generalists). The goal is not
> to check Salesforce-readiness, but whether each submission gives a salesperson
> **enough to start a productive first conversation**. Score against "enough to start",
> not "enough to close". Final quality judgment is human — this skill prepares the data.

---

## Part A — Pipeline Gap Detection

### Gap categories detected

| Gap | Threshold | Description |
|---|---|---|
| **Unroutable** | Any | NO_ACTION leads with no assigned rep (country not mapped) |
| **Stale ENGAGED** | 14 days | Rep engaged but no quote sent |
| **Stale QUOTED** | 10 days | Quote sent but no customer reply |
| **Stale FOLLOW_UP** | 20 days | Customer replied but deal not closed |
| **Missing Fields** | Any | Active leads with incomplete data for quoting |
| **Dark Leads** | 5 days | Active leads with no email scan activity |
| **Long Age** | 30 days | Any active lead in pipeline for 30+ days |
| **High Rejection Rate** | > 30% | Rejection rate flag for data quality review |

---

## Part B — Extensia Submission Quality Analysis

### What gets scored

1. **Field completeness** — % of mode-specific mandatory fields present per lead
2. **Notes quality score** — AI-scored 1–5 by Claude reading the free-text `notes` field:
   - **1** — Empty, "nil", "N/A", or meaningless
   - **2** — Minimal (single keyword: "customs needed" / "ETD 07 may")
   - **3** — Partial — mentions one useful detail but misses specifics
   - **4** — Good — enough for a rep to start a productive conversation
   - **5** — Excellent — detailed timeline, packaging, ETD, specific requirements
3. **Most commonly missing fields** — ranked by frequency across all leads
4. **Poor-notes sample leads** — leads scoring ≤ 2 flagged for Extensia training

### Notes scoring instruction (Claude scores inline — no subagent)

When `notes_to_score` list is returned, Claude reads each entry's `notes` field
and assigns a score 1–5 using the rubric above. This is the **one approved AI call**
in the gap analysis skill — everything else is deterministic Python.

---

## Prerequisites & paths
Replace `<workspace>` with the actual path to the workspace folder (the one selected by the user)
```

DATA_DIR  = WORKSPACE/data/
SCRIPTS   = WORKSPACE/skills/lead-gap-analysis/scripts/
  gap_detector.py          — detect_gaps(), analyze_extensia_quality(),
                             save_notes_scores(), transition_to_gap_analysis(),
                             build_gap_summary()
  gap_teams_template.py    — GWC-branded Adaptive Card gap report builder

SHARED_SCRIPTS = WORKSPACE/skills/lead-ingestion/scripts/
  csv_store.py             — CSV read/write (shared)
```

## Connector

**teams-composio** — sends the report as a 1:1 Teams DM to `hebah.yasin@gwclogistics.com`.

---

## Step-by-step execution

### Step 1 — Detect pipeline gaps

```python
import sys
WORKSPACE = "<current workspace path>"
sys.path.insert(0, f"{WORKSPACE}/skills/lead-ingestion/scripts")
sys.path.insert(0, f"{WORKSPACE}/skills/lead-gap-analysis/scripts")

from csv_store import CSVStore
from gap_detector import detect_gaps, build_gap_summary, analyze_extensia_quality, save_notes_scores
from gap_teams_template import build_gap_card, card_to_attachment

store = CSVStore(f"{WORKSPACE}/data")
gaps  = detect_gaps(store)
print(build_gap_summary(gaps))
```

### Step 2 — Run Extensia quality analysis

```python
extensia = analyze_extensia_quality(store)
# extensia["notes_to_score"] → list of leads needing Notes quality scoring
print(f"Leads to score: {len(extensia['notes_to_score'])}")
print(f"Avg field completeness: {extensia['avg_completeness_pct']}%")
print(f"Top missing fields: {extensia['most_missing_fields'][:5]}")
```

### Step 3 — Score Notes fields (Claude AI — inline)

For each entry in `extensia["notes_to_score"]` where `already_scored == False`:
- Read the `notes` field
- Apply the 1–5 rubric above
- Produce a brief `extensia_feedback` string explaining the score

Collect all scores into a list:

```python
scored = []
for entry in extensia["notes_to_score"]:
    if entry["already_scored"]:
        continue
    # Claude reads entry["notes"] and scores it
    score = <your_score_1_to_5>
    feedback = "<brief reason for the score>"
    scored.append({
        "gwc_id":             entry["gwc_id"],
        "notes_quality_score": score,
        "extensia_feedback":   feedback,
    })

save_notes_scores(store, scored)
print(f"Scored {len(scored)} leads.")
```

### Step 4 — Re-fetch extensia data (with scores now saved)

```python
extensia = analyze_extensia_quality(store)
poor_notes = extensia["poor_notes_samples"]
print(f"Poor-notes leads (score ≤ 2): {len(poor_notes)}")
```

### Step 5 — Build the Adaptive Card report

```python
title, card = build_gap_card(gaps, extensia)
print(f"Title: {title}")
```

### Step 6 — Send via Teams 1:1 DM to manager

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

### Step 7 — Log and confirm

```python
store.log_activity(
    gwc_id="SYSTEM",
    activity_type="REPORT_SENT",
    detail={
        "report_type":          "GAP_ANALYSIS",
        "total_gaps":           gaps["total_gaps"],
        "total_leads":          gaps["total_leads"],
        "extensia_leads_scored": len(scored),
        "avg_completeness_pct": extensia["avg_completeness_pct"],
        "poor_notes_count":     len(poor_notes),
        "sent_to":              "hebah.yasin@gwclogistics.com (Teams DM)",
    },
    performed_by="SYSTEM",
)
print(f"✅ Gap analysis report sent via Teams DM — {gaps['total_gaps']} gap item(s) found")
```

### Step 8 — (Optional) Transition reviewed leads to GAP_ANALYSIS status

After a human SME has reviewed the output, individual leads can be transitioned:

```python
from gap_detector import transition_to_gap_analysis
transition_to_gap_analysis(store, "GWC-XXXXXXXXX", reason="Reviewed in April 2026 gap analysis")
```

Note: `GAP_ANALYSIS` is a terminal review state. It cannot override `REJECTED`.
The Phase 3 status tracker excludes `GAP_ANALYSIS` leads from its active scan.

---

## Script reference

| Script | Key exports |
|---|---|
| `gap_detector.py` | `detect_gaps(store)`, `build_gap_summary(gaps)`, `analyze_extensia_quality(store, date_from, date_to)`, `save_notes_scores(store, scored_leads)`, `transition_to_gap_analysis(store, gwc_id, reason)` |
| `gap_teams_template.py` | `build_gap_card(gaps, extensia) → (title, card)`, `card_to_attachment(card)` |
| `../lead-ingestion/scripts/csv_store.py` | `CSVStore(data_dir)`, `.log_activity()`, `.update_lead_field()` |
