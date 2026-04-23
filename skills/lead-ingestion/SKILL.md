---
name: lead-ingestion
description: >
  GWC Lead Maturity Automation — Phase 1 ingestion skill.
  Reads new HubSpot freight lead emails from the Sales.rfq@gwclogistics.com shared mailbox,
  parses structured fields from each email, classifies leads as QUALIFIED / PARTIALLY_QUALIFIED /
  REJECTED (deterministic — no AI), inserts records into the leads_maturity CSV, and logs activity.
  Trigger when the user says: "check for new leads", "scan lead mailbox", "ingest leads",
  "pull new freight emails", "process the mailbox", or any variation of wanting to read and
  process incoming freight opportunity emails. Also trigger if the user asks to run Phase 1
  of the lead automation pipeline.
---

# Lead Ingestion Skill

Reads HubSpot freight lead emails from `Sales.rfq@gwclogistics.com`, parses them,
classifies leads deterministically, and writes results to the CSV data store.

## Prerequisites & paths

```
DATA_DIR  = <workspace>/data/              # leads_maturity.csv, country_rep_mapping.csv, lead_activity_log.csv
SKILLS_DIR = <workspace>/skills/lead-ingestion/
SCRIPTS   = SKILLS_DIR/scripts/
  parse_lead_email.py   — regex parser for HubSpot email body
  classify_lead.py      — deterministic classifier (no LLM)
  csv_store.py          — CSV read/write helpers
```

Replace `<workspace>` with the actual path to the workspace folder (the one selected by the user).

## Connector

**outlook-composio** — used to query the shared mailbox.
The shared mailbox address is: `Sales.rfq@gwclogistics.com`

## Step-by-step execution

### Step 1 — Load existing GWC IDs (deduplication)

Run the Python script below to get all GWC IDs already stored in `leads_maturity.csv`:

```python
import sys
sys.path.insert(0, "<workspace>/skills/lead-ingestion/scripts")
from csv_store import CSVStore
store = CSVStore("<workspace>/data")
existing_ids = store.get_all_gwc_ids()
print(f"Found {len(existing_ids)} existing leads in DB")
```

### Step 2 — Fetch new emails from the shared mailbox

Use `OUTLOOK_QUERY_EMAILS` to query the shared mailbox (`Sales.rfq@gwclogistics.com`) for
emails with subject containing "New Freight Opportunity".

**Never use `OUTLOOK_SEARCH_MESSAGES`** — it fails with delegated mailbox permissions.
Always use `OUTLOOK_QUERY_EMAILS` with an OData `filter` parameter.

**Key parameters to use:**
- `user_id`: `"Sales.rfq@gwclogistics.com"`
- `filter`: `"contains(subject, 'New Freight Opportunity')"`
- `orderby`: `"receivedDateTime desc"`
- `top`: 50

The HubSpot email subject format is: `[BULK] New Freight Opportunity for GWC-[UNIQUE_ID]`

For each email retrieved, extract:
- `id` → the Outlook message ID
- `subject` → email subject line
- `body.content` → email body (prefer `body.contentType == "text"` for plain text; strip HTML tags if only HTML is available)
- `receivedDateTime` → when the email arrived

**Deduplication**: Before processing, extract the GWC ID from the subject line using the pattern
`GWC-\d+`. If that GWC ID is already in `existing_ids`, skip the email — it was already ingested.

### Step 3 — Parse each new email

For each new email, run:

```python
import sys
sys.path.insert(0, "<workspace>/skills/lead-ingestion/scripts")
from parse_lead_email import parse_lead_email

fields = parse_lead_email(
    subject=email["subject"],
    body=email["body"]["content"],
    message_id=email["id"],
)
```

`fields` is now a dict with all leads_maturity columns. Empty/missing values are set to `""`.

**HTML body handling**: If the email body is HTML, strip tags before passing to the parser:
```python
import re
def strip_html(html):
    clean = re.sub(r'<[^>]+>', '', html)
    return re.sub(r'\n{3,}', '\n\n', clean).strip()
```

### Step 4 — Classify the lead (deterministic, no AI)

```python
from classify_lead import classify_lead, apply_classification

result = classify_lead(fields)
fields = apply_classification(fields, result)
# fields now has: classification, missing_fields (JSON), current_status, status_history (JSON)
```

Classification outcomes:
- `QUALIFIED` → Mean Of Transport is present AND All mandatory fields for specified Mean Of Transport are present. `current_status = NO_ACTION`
- `PARTIALLY_QUALIFIED` →  Mean Of Transport is missing OR Some fields for specified Mean Of Transport missing `current_status = NO_ACTION`
- `REJECTED` → GWC ID missing `current_status = REJECTED`

### Step 5 — Insert into leads_maturity.csv

```python
from csv_store import CSVStore
store = CSVStore("<workspace>/data")
stored_row = store.upsert_lead(fields)
```

The store uses `gwc_id` as the unique key — it will insert a new row or update an existing one.

### Step 6 — Log activity in lead_activity_log.csv

```python
store.log_activity(
    gwc_id=fields["gwc_id"],
    activity_type="EMAIL_RECEIVED",
    detail={
        "classification": fields["classification"],
        "missing_fields": fields["missing_fields"],
        "from_country": fields["from_country"],
        "to_country": fields["to_country"],
        "mode_of_freight": fields["mode_of_freight"],
        "email_subject": email["subject"],
    },
    email_message_id=fields["email_message_id"],
    performed_by="SYSTEM",
)
```

### Step 6b — Flush to Databricks

```python
from db_sync import generate_sql_statements, clear_queue

queue_path = f"<workspace>/data/pending_writes.jsonl"
stmts = generate_sql_statements(queue_path)
print(f"Flushing {len(stmts)} statement(s) to Databricks...")
# Execute each stmt via mcp__62f760ee-bfcc-4f93-bec8-cdf2d76870ad__execute_sql
# Run in parallel batches of 8 for speed.
clear_queue(queue_path)
```

> **⚡ Deferred-flush rule**: If Quip enrichment (`lead-quip-enrichment`) will run
> immediately after Phase 1, **skip this flush and leave `pending_writes.jsonl` intact**.
> The Quip skill's Step 4 flushes Phase 1 + Quip writes together in a single combined
> operation, saving an entire Databricks round trip. Only flush here when Phase 1 is
> running in isolation (no Quip enrichment to follow).

---

### Step 7 — Report summary

After processing all emails, print a summary in this format:

```
✅ Lead Ingestion Complete
─────────────────────────────────────────
Emails scanned:     12
New (unprocessed):   5
Already in DB:       7

Classification breakdown:
  QUALIFIED:           2
  PARTIALLY_QUALIFIED: 2
  REJECTED:            1

New leads added to leads_maturity.csv ✓
Activity logged in lead_activity_log.csv ✓
```

Then state:
> "Ready for optional Quip enrichment — run `lead-quip-enrichment` to cross-check leads
>  against the Quip Digital Sales Leads sheet and populate `quip_country` / `bd_poc_email`
>  before routing. **Skip this step if Quip is no longer in use.**
>  Then run `lead-routing` to assign reps and send notifications."

## Important edge cases

1. **HTML-only bodies**: Pass the raw HTML `body.content` directly to `parse_lead_email()`.
   That function has its own `strip_html()` which injects newlines before known field labels
   BEFORE stripping tags — preserving the MULTILINE regex anchors (`^Name:`, `^From Country:` etc.).
   **Never pre-strip HTML yourself** before calling `parse_lead_email()`; doing so collapses
   all fields onto one line and causes every field to parse as blank.

2. **Multiple emails for same GWC ID**: The deduplication in Step 2 prevents double-ingestion.
   If a GWC ID is already in the DB, skip that email entirely.

3. **GWC ID not in subject**: Some forwarded or re-sent emails may have the GWC ID only in the body.
   The parser checks both subject and body — this is handled automatically.

4. **Empty emails / spam**: If parsing returns no GWC ID and no meaningful fields, classification
   will return REJECTED. Log it with `rejection_reason` in the activity detail.

5. **Shared mailbox access**: The outlook-composio connector is authenticated as the calling user.
   To access `Sales.rfq@gwclogistics.com`, use the `userId` or `mailbox` parameter to specify
   the shared mailbox address, not the personal mailbox. Consult the Outlook MCP tool schemas
   if the default account doesn't have access — the tool may require passing `userId=Sales.rfq@gwclogistics.com`.

6. **All leads including REJECTED must be stored**: Never skip logging. REJECTED leads are
   tracked for campaign quality metrics.

## Script reference

| Script | Purpose | Key functions |
|--------|---------|---------------|
| `scripts/parse_lead_email.py` | Parse raw email body to structured dict | `parse_lead_email(subject, body, message_id)` |
| `scripts/classify_lead.py` | Deterministic classification | `classify_lead(fields)`, `apply_classification(fields, result)` |
| `scripts/csv_store.py` | CSV read/write, deduplication, rep lookup | `CSVStore(data_dir)`, `.upsert_lead()`, `.log_activity()`, `.get_all_gwc_ids()` |

> **Quip cross-check** has moved to its own skill: `skills/lead-quip-enrichment/`.
> Run that skill after ingestion if Quip enrichment is needed before routing.

## Classification rules (summary)

| Condition | Classification | Status |
|-----------|---------------|--------|
| GWC ID missing | REJECTED | REJECTED |
| GWC ID present, all key fields empty | PARTIALLY_QUALIFIED | NO_ACTION |
| GWC ID + some required fields present | PARTIALLY_QUALIFIED | NO_ACTION |
| GWC ID + ALL mandatory fields  present | QUALIFIED | NO_ACTION |

**Mandatory fields checked** (from HubSpot template): `gwc_id`, `from_country`, `to_country`,
`mode_of_freight`, `product`, `weight_kg`.

**Extended mandatory fields** (not in HubSpot template — always missing at ingestion):
`incoterms`, `packages`, `dimension_lwh`, and MOT-specific fields.
→ This means most leads from the HubSpot template will be `PARTIALLY_QUALIFIED` at ingestion.
   The sales rep is notified to collect the missing extended fields from the customer.
   This is expected and correct behaviour.
