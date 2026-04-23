"""
migrate_csv_to_db.py
--------------------
One-time utility: reads existing local CSVs and generates SQL INSERT statements
to seed the Databricks tables with all historical data.

Usage (Claude runs this, then executes each SQL statement):

    python3 migrate_csv_to_db.py /path/to/data

Outputs:
    migrate_leads.sql       — INSERT statements for leads_maturity
    migrate_activity.sql    — INSERT statements for lead_activity_log
    migrate_mapping.sql     — INSERT statements for country_rep_mapping
    migrate_summary.txt     — row counts and any warnings

Claude then calls execute_sql() for each statement via the Databricks MCP tool.
Use migrate_leads.sql first, then migrate_activity.sql, then migrate_mapping.sql.

SAFETY: Uses INSERT OR REPLACE equivalent (MERGE by PK) so re-running is safe.
"""

import csv
import json
import sys
from pathlib import Path
from datetime import datetime

# ── Targets ───────────────────────────────────────────────────────────────────
DB_LEADS    = "claude_prototyping.marketing.leads_maturity"
DB_ACTIVITY = "claude_prototyping.marketing.lead_activity_log"
DB_MAPPING  = "claude_prototyping.marketing.country_rep_mapping"

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

MAPPING_COLUMNS = [
    "id", "country_code", "country_name", "rep_email",
    "rep_name", "is_primary", "active",
]


# ── SQL helpers ───────────────────────────────────────────────────────────────

def _esc(v) -> str:
    if v is None or str(v).strip() == "":
        return "NULL"
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        print(f"  WARNING: {path} not found — skipping")
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _merge_by_gwc_id(table: str, columns: list[str], row: dict,
                     immutable: set = None) -> str:
    """Generate a MERGE INTO statement for a single row keyed on gwc_id."""
    if immutable is None:
        immutable = {"gwc_id", "id", "created_at"}

    src_parts   = [f"{_esc(row.get(c, ''))} AS {c}" for c in columns]
    update_parts = [f"target.{c} = source.{c}" for c in columns if c not in immutable]
    ins_cols    = ", ".join(columns)
    ins_vals    = ", ".join(f"source.{c}" for c in columns)

    return (
        f"MERGE INTO {table} AS target\n"
        f"USING (SELECT {', '.join(src_parts)}) AS source\n"
        f"ON target.gwc_id = source.gwc_id\n"
        f"WHEN MATCHED THEN UPDATE SET {', '.join(update_parts)}\n"
        f"WHEN NOT MATCHED THEN INSERT ({ins_cols}) VALUES ({ins_vals})"
    )


def _insert_activity(row: dict) -> str:
    """Generate a plain INSERT for the activity log (append-only, no PK conflict)."""
    cols = ACTIVITY_COLUMNS
    vals = ", ".join(_esc(row.get(c, "")) for c in cols)
    return f"INSERT INTO {DB_ACTIVITY} ({', '.join(cols)}) VALUES ({vals})"


def _merge_mapping(row: dict) -> str:
    """MERGE country_rep_mapping by (country_code, rep_email) composite key."""
    cols = MAPPING_COLUMNS
    src_parts    = [f"{_esc(row.get(c, ''))} AS {c}" for c in cols]
    update_parts = [f"target.{c} = source.{c}" for c in cols if c not in ("id", "country_code", "rep_email")]
    ins_cols     = ", ".join(cols)
    ins_vals     = ", ".join(f"source.{c}" for c in cols)
    return (
        f"MERGE INTO {DB_MAPPING} AS target\n"
        f"USING (SELECT {', '.join(src_parts)}) AS source\n"
        f"ON target.country_code = source.country_code AND target.rep_email = source.rep_email\n"
        f"WHEN MATCHED THEN UPDATE SET {', '.join(update_parts)}\n"
        f"WHEN NOT MATCHED THEN INSERT ({ins_cols}) VALUES ({ins_vals})"
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def migrate(data_dir: str):
    data = Path(data_dir)
    out  = data.parent / "skills" / "lead-ingestion" / "scripts"  # save alongside scripts
    # Fallback: save next to data dir
    if not out.exists():
        out = data

    warnings = []
    summary  = []

    # ── leads_maturity ────────────────────────────────────────────────────────
    leads_rows = _read_csv(data / "leads_maturity.csv")
    leads_sql  = []
    for row in leads_rows:
        try:
            leads_sql.append(_merge_by_gwc_id(DB_LEADS, LEADS_COLUMNS, row))
        except Exception as e:
            warnings.append(f"leads row gwc_id={row.get('gwc_id','?')}: {e}")

    (out / "migrate_leads.sql").write_text(
        "-- GWC leads_maturity migration\n-- Generated: "
        + datetime.utcnow().isoformat() + "Z\n\n"
        + ";\n\n".join(leads_sql) + ";\n",
        encoding="utf-8"
    )
    summary.append(f"leads_maturity:    {len(leads_sql)} MERGE statements")
    print(f"  leads_maturity: {len(leads_sql)} rows")

    # ── lead_activity_log ─────────────────────────────────────────────────────
    activity_rows = _read_csv(data / "lead_activity_log.csv")
    activity_sql  = []
    for row in activity_rows:
        try:
            activity_sql.append(_insert_activity(row))
        except Exception as e:
            warnings.append(f"activity row id={row.get('id','?')}: {e}")

    (out / "migrate_activity.sql").write_text(
        "-- GWC lead_activity_log migration\n-- Generated: "
        + datetime.utcnow().isoformat() + "Z\n\n"
        + ";\n\n".join(activity_sql) + ";\n",
        encoding="utf-8"
    )
    summary.append(f"lead_activity_log: {len(activity_sql)} INSERT statements")
    print(f"  lead_activity_log: {len(activity_sql)} rows")

    # ── country_rep_mapping ───────────────────────────────────────────────────
    mapping_rows = _read_csv(data / "country_rep_mapping.csv")
    mapping_sql  = []
    for row in mapping_rows:
        try:
            mapping_sql.append(_merge_mapping(row))
        except Exception as e:
            warnings.append(f"mapping row id={row.get('id','?')}: {e}")

    (out / "migrate_mapping.sql").write_text(
        "-- GWC country_rep_mapping migration\n-- Generated: "
        + datetime.utcnow().isoformat() + "Z\n\n"
        + ";\n\n".join(mapping_sql) + ";\n",
        encoding="utf-8"
    )
    summary.append(f"country_rep_mapping: {len(mapping_sql)} MERGE statements")
    print(f"  country_rep_mapping: {len(mapping_sql)} rows")

    # ── summary ───────────────────────────────────────────────────────────────
    summary_text = "\n".join(summary)
    if warnings:
        summary_text += "\n\nWARNINGS:\n" + "\n".join(f"  - {w}" for w in warnings)
    (out / "migrate_summary.txt").write_text(summary_text + "\n", encoding="utf-8")

    print(f"\nDone. SQL files written to: {out}")
    print("Next: have Claude execute each .sql file via execute_sql() MCP calls.")
    if warnings:
        print(f"\n{len(warnings)} warning(s) — see migrate_summary.txt")


if __name__ == "__main__":
    data_dir = sys.argv[1] if len(sys.argv) > 1 else "data"
    print(f"Migrating from: {data_dir}")
    migrate(data_dir)
