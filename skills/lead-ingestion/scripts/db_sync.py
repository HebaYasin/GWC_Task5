"""
db_sync.py
----------
Utilities for flushing the DBStore write queue to Databricks.

Claude calls these after each pipeline phase:

    import sys
    sys.path.insert(0, f"{WORKSPACE}/skills/lead-ingestion/scripts")
    from db_sync import generate_sql_statements, clear_queue, preview_queue

    queue_path = f"{WORKSPACE}/data/pending_writes.jsonl"
    stmts = generate_sql_statements(queue_path)
    print(f"{len(stmts)} statement(s) to flush")

Then for each stmt, Claude calls:
    execute_sql(stmt)          # via mcp__62f760ee-bfcc-4f93-bec8-cdf2d76870ad

Then:
    clear_queue(queue_path)

For loading data FROM Databricks into local CSVs (optional pre-phase seed):
    from db_sync import write_mcp_result_to_csv
"""

import json
from pathlib import Path

# ── Databricks target tables ───────────────────────────────────────────────────
DB_LEADS    = "claude_prototyping.marketing.leads_maturity"
DB_ACTIVITY = "claude_prototyping.marketing.lead_activity_log"

LEADS_COLUMNS = [
    "id", "gwc_id", "email_message_id", "contact_name", "company_name",
    "phone", "whatsapp", "from_country", "to_country", "origin_country_alt",
    "destination_country_alt", "mode_of_freight", "container_mode",
    "container_type", "product", "perishable", "temperature_details",
    "stackable", "dg_class", "msds", "incoterms", "weight_kg", "volume_m3",
    "packages", "chargeable_weight", "dimension_lwh", "shipping_requirements",
    "notes", "classification", "missing_fields", "current_status",
    "status_history", "assigned_rep_email", "assigned_rep_name",
    "assigned_country", "hubspot_create_date", "email_received_at",
    "first_response_at", "quote_sent_at", "follow_up_started_at",
    "deal_confirmed_at", "deal_outcome", "lead_age_days", "reminder_history",
    "created_at", "updated_at", "last_email_scan_at",
    "notes_quality_score", "extensia_feedback",
    "in_quip_sheet", "quip_country", "quip_updates_raw", "quip_updates_summary",
    "bd_poc_name", "bd_poc_email",
    "detected_working_rep_email", "detected_working_rep_name",
    "routed_at",
]

ACTIVITY_COLUMNS = [
    "id", "gwc_id", "activity_type", "activity_detail",
    "email_message_id", "performed_by", "created_at",
]

# Columns that must never be overwritten on MATCH in a MERGE
_LEADS_IMMUTABLE = {"gwc_id", "id", "created_at"}


# ── SQL helpers ───────────────────────────────────────────────────────────────

def _esc(v) -> str:
    """Escape a Python value as a SQL string literal or NULL."""
    if v is None or v == "" or v == "NULL":
        return "NULL"
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _gen_upsert_lead_sql(row: dict) -> str:
    """
    Generate a MERGE INTO for leads_maturity.
    Upserts by gwc_id: UPDATE on match, INSERT on no-match.
    """
    cols = LEADS_COLUMNS

    # Build the inline source SELECT
    src_parts = [f"{_esc(row.get(c, ''))} AS {c}" for c in cols]
    src_select = ", ".join(src_parts)

    # UPDATE clause — skip immutable columns
    update_parts = [
        f"target.{c} = source.{c}"
        for c in cols if c not in _LEADS_IMMUTABLE
    ]
    update_clause = ", ".join(update_parts)

    # INSERT clause
    ins_cols = ", ".join(cols)
    ins_vals = ", ".join(f"source.{c}" for c in cols)

    return (
        f"MERGE INTO {DB_LEADS} AS target\n"
        f"USING (SELECT {src_select}) AS source\n"
        f"ON target.gwc_id = source.gwc_id\n"
        f"WHEN MATCHED THEN UPDATE SET {update_clause}\n"
        f"WHEN NOT MATCHED THEN INSERT ({ins_cols}) VALUES ({ins_vals})"
    )


def _gen_update_lead_sql(gwc_id: str, updates: dict) -> str:
    """
    Generate an UPDATE for leads_maturity for changed fields only.
    Faster than a full MERGE when only a few columns changed.
    """
    set_parts = [
        f"{col} = {_esc(val)}"
        for col, val in updates.items()
        if col not in _LEADS_IMMUTABLE
    ]
    if not set_parts:
        return ""  # nothing to update
    set_clause = ", ".join(set_parts)
    return (
        f"UPDATE {DB_LEADS} "
        f"SET {set_clause} "
        f"WHERE gwc_id = {_esc(gwc_id)}"
    )


def _gen_insert_activity_sql(row: dict) -> str:
    """Generate an INSERT for lead_activity_log (single row)."""
    cols = ACTIVITY_COLUMNS
    col_list = ", ".join(cols)
    val_list = ", ".join(_esc(row.get(c, "")) for c in cols)
    return f"INSERT INTO {DB_ACTIVITY} ({col_list}) VALUES ({val_list})"


def generate_batch_update_sql(gwc_id_updates: list, table: str = None) -> str:
    """
    Generate a SINGLE MERGE INTO that covers many update-only rows at once.

    Use this for Quip enrichment (or any phase that updates a small fixed set
    of columns across many leads) to replace N individual UPDATE round trips
    with a single Databricks call.

    Args:
        gwc_id_updates: list of (gwc_id, {col: val, ...}) tuples
        table: defaults to DB_LEADS

    Returns:
        A single MERGE SQL string ready for execute_sql().

    Example:
        rows = [
            ("GWC-123", {"in_quip_sheet": "YES", "quip_country": "Qatar", ...}),
            ("GWC-456", {"in_quip_sheet": "NO",  "quip_country": None,    ...}),
        ]
        sql = generate_batch_update_sql(rows)
        execute_sql(sql)   # single round trip for all rows
    """
    if table is None:
        table = DB_LEADS
    if not gwc_id_updates:
        return ""

    # Union of all column names referenced (skip immutables)
    all_cols = set()
    for _, updates in gwc_id_updates:
        all_cols.update(k for k in updates if k not in _LEADS_IMMUTABLE)
    all_cols = sorted(all_cols)

    # Build USING clause with UNION ALL (one SELECT per row)
    union_rows = []
    for gwc_id, updates in gwc_id_updates:
        parts = [f"{_esc(gwc_id)} AS gwc_id"]
        for col in all_cols:
            parts.append(f"{_esc(updates.get(col))} AS {col}")
        union_rows.append("SELECT " + ", ".join(parts))

    using_clause = "\n  UNION ALL ".join(union_rows)
    update_clause = ", ".join(f"target.{c} = source.{c}" for c in all_cols)

    return (
        f"MERGE INTO {table} AS target\n"
        f"USING (\n  {using_clause}\n) AS source\n"
        f"ON target.gwc_id = source.gwc_id\n"
        f"WHEN MATCHED THEN UPDATE SET {update_clause}"
    )


def generate_batch_insert_activity_sql(rows: list, batch_size: int = 20) -> list:
    """
    Batch many activity log rows into multi-value INSERT statements.

    Replaces N individual INSERT calls with ceil(N/batch_size) calls.

    Args:
        rows: list of activity dicts (same shape as _gen_insert_activity_sql input)
        batch_size: rows per INSERT statement (default 20)

    Returns:
        list of SQL strings, each covering up to batch_size rows.

    Example (58 rows → 3 statements instead of 58):
        stmts = generate_batch_insert_activity_sql(activity_rows)
        for stmt in stmts:
            execute_sql(stmt)
    """
    cols = ACTIVITY_COLUMNS
    col_list = ", ".join(cols)
    statements = []
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        value_rows = [
            "(" + ", ".join(_esc(r.get(c, "")) for c in cols) + ")"
            for r in batch
        ]
        values_clause = ",\n  ".join(value_rows)
        statements.append(
            f"INSERT INTO {DB_ACTIVITY} ({col_list}) VALUES\n  {values_clause}"
        )
    return statements


# ── Public API ────────────────────────────────────────────────────────────────

def generate_sql_statements(queue_path: str) -> list:
    """
    Read pending_writes.jsonl and return an optimised list of SQL statements.

    Optimisations vs naïve 1-entry-1-statement approach:
      • update_lead entries that share identical column sets are batched into a
        single MERGE with UNION ALL (e.g. 58 Quip updates → 1 statement).
      • insert_activity entries are batched into multi-row INSERTs (groups of 20).
      • upsert_lead entries remain individual MERGEs (each row has unique fields).

    Ordering guarantee: upsert_leads first, then updates, then activities —
    matching the dependency order (insert before update, update before log).
    """
    path = Path(queue_path)
    if not path.exists():
        return []

    upserts   = []   # (data_dict,)
    updates   = []   # (gwc_id, updates_dict)
    activities = []  # data_dict

    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[db_sync] WARNING: skipping malformed queue entry line {lineno}: {e}")
                continue

            op = entry.get("op")
            if op == "upsert_lead":
                upserts.append(entry["data"])
            elif op == "update_lead":
                updates.append((entry["gwc_id"], entry["data"]))
            elif op == "insert_activity":
                activities.append(entry["data"])
            else:
                print(f"[db_sync] WARNING: unknown op '{op}' at line {lineno}, skipping")

    statements = []

    # 1. Individual upserts (each lead has unique field values)
    for data in upserts:
        statements.append(_gen_upsert_lead_sql(data))

    # 2. Batch update_lead by column-set so rows with the same cols merge into
    #    one MERGE statement (Quip enrichment always touches the same 5 cols).
    if updates:
        # Group by frozenset of column names
        from collections import defaultdict
        col_groups: dict = defaultdict(list)
        for gwc_id, upd in updates:
            key = frozenset(k for k in upd if k not in _LEADS_IMMUTABLE)
            col_groups[key].append((gwc_id, upd))

        for _, group in col_groups.items():
            if len(group) == 1:
                gwc_id, upd = group[0]
                sql = _gen_update_lead_sql(gwc_id, upd)
                if sql:
                    statements.append(sql)
            else:
                sql = generate_batch_update_sql(group)
                if sql:
                    statements.append(sql)

    # 3. Batch activity inserts (20 rows per INSERT)
    statements.extend(generate_batch_insert_activity_sql(activities))

    total_entries = len(upserts) + len(updates) + len(activities)
    print(
        f"[db_sync] {total_entries} queue entries → {len(statements)} SQL statement(s) "
        f"({len(upserts)} upserts, {len(col_groups) if updates else 0} update batch(es), "
        f"{len(generate_batch_insert_activity_sql(activities))} activity batch(es))"
    )
    return statements


def clear_queue(queue_path: str):
    """Truncate pending_writes.jsonl after a successful flush."""
    Path(queue_path).write_text("", encoding="utf-8")
    print(f"[db_sync] Queue cleared: {queue_path}")


def preview_queue(queue_path: str) -> list[dict]:
    """Return parsed queue entries for inspection without executing anything."""
    path = Path(queue_path)
    if not path.exists():
        return []
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def write_mcp_result_to_csv(mcp_result: dict, output_path: str, columns: list[str]):
    """
    Parse the JSON response from execute_sql_read_only() and write it as a CSV.

    Use this to pre-seed local CSVs from Databricks before a pipeline phase:

        result = execute_sql_read_only("SELECT * FROM claude_prototyping.marketing.leads_maturity")
        write_mcp_result_to_csv(result, store.leads_path, LEADS_COLUMNS)

    mcp_result shape expected:
        {
          "result": {
            "data_array": [
              {"values": [{"string_value": "..."} | {"null_value": "NULL_VALUE"}, ...]}
            ]
          },
          "manifest": {
            "schema": {"columns": [{"name": "col"}, ...]}
          }
        }
    """
    import csv

    # Extract column names from the manifest
    manifest_cols = [
        c["name"]
        for c in mcp_result.get("manifest", {}).get("schema", {}).get("columns", [])
    ]

    rows = []
    for row_obj in mcp_result.get("result", {}).get("data_array", []):
        values = row_obj.get("values", [])
        row = {}
        for i, col in enumerate(manifest_cols):
            if i < len(values):
                cell = values[i]
                if "string_value" in cell:
                    row[col] = cell["string_value"]
                else:
                    row[col] = ""  # null_value → blank
            else:
                row[col] = ""
        rows.append(row)

    # Write CSV using the canonical column order
    out = Path(output_path)
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"[db_sync] Wrote {len(rows)} rows → {out}")
    return rows


# ── CLI convenience ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python db_sync.py <queue_path> [preview|count]")
        sys.exit(1)

    qp = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "count"

    if mode == "preview":
        entries = preview_queue(qp)
        for e in entries:
            print(json.dumps(e, indent=2))
    else:
        stmts = generate_sql_statements(qp)
        print(f"{len(stmts)} SQL statement(s) pending")
        for i, s in enumerate(stmts, 1):
            print(f"\n── [{i}] ──\n{s[:300]}{'...' if len(s) > 300 else ''}")
