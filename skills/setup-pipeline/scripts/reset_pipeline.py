"""
reset_pipeline.py
-----------------
Resets leads_maturity.csv and lead_activity_log.csv to header-only files,
clears the pending_writes.jsonl queue, and generates SQL TRUNCATE statements
for Claude to execute against Databricks.

country_rep_mapping.csv is NEVER touched.

Usage:
    from reset_pipeline import reset_pipeline
    result = reset_pipeline("/path/to/workspace")
    print(result["message"])
    # Then Claude executes result["databricks_sql"] via execute_sql() MCP calls.
"""

import sys
from pathlib import Path

# ── Import canonical column lists from db_store (single source of truth) ──────
# This avoids the reset script having its own stale column definitions.
_scripts = Path(__file__).parent.parent.parent / "lead-ingestion" / "scripts"
sys.path.insert(0, str(_scripts))
from db_store import LEADS_COLUMNS, ACTIVITY_COLUMNS  # noqa: E402

import csv  # noqa: E402 (after sys.path patch)

# Databricks tables to truncate on reset
DB_LEADS    = "claude_prototyping.marketing.leads_maturity"
DB_ACTIVITY = "claude_prototyping.marketing.lead_activity_log"


def _write_header_only(path: Path, columns: list[str]) -> int:
    """Write a CSV with only the header row. Returns the previous row count."""
    prev_count = 0
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            prev_count = sum(1 for _ in reader)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()

    return prev_count


def reset_pipeline(workspace_path: str) -> dict:
    """
    Reset local CSVs to header-only, clear the write queue, and return
    Databricks TRUNCATE SQL statements for Claude to execute.

    Args:
        workspace_path: Absolute path to the workspace folder
                        (the folder containing data/ and skills/).

    Returns:
        dict with keys:
          - success (bool)
          - leads_cleared (int)     — rows removed from leads_maturity.csv
          - activity_cleared (int)  — rows removed from lead_activity_log.csv
          - queue_cleared (bool)    — whether pending_writes.jsonl was wiped
          - databricks_sql (list)   — SQL statements Claude must execute to
                                      also truncate the Databricks tables
          - message (str)
    """
    data_dir = Path(workspace_path) / "data"

    if not data_dir.exists():
        return {
            "success": False,
            "leads_cleared": 0,
            "activity_cleared": 0,
            "queue_cleared": False,
            "databricks_sql": [],
            "message": f"ERROR: data directory not found at {data_dir}",
        }

    leads_path    = data_dir / "leads_maturity.csv"
    activity_path = data_dir / "lead_activity_log.csv"
    mapping_path  = data_dir / "country_rep_mapping.csv"
    queue_path    = data_dir / "pending_writes.jsonl"

    # 1. Clear local CSVs
    leads_cleared    = _write_header_only(leads_path, LEADS_COLUMNS)
    activity_cleared = _write_header_only(activity_path, ACTIVITY_COLUMNS)

    # 2. Clear pending write queue (avoids stale DB writes after reset)
    queue_cleared = False
    if queue_path.exists():
        queue_path.write_text("", encoding="utf-8")
        queue_cleared = True

    # 3. Databricks TRUNCATE SQL (Claude executes these via execute_sql() MCP)
    databricks_sql = [
        f"DELETE FROM {DB_LEADS}",
        f"DELETE FROM {DB_ACTIVITY}",
    ]

    msg = (
        f"Pipeline Reset — Local CSVs Cleared\n"
        f"{'─' * 44}\n"
        f"leads_maturity.csv        → cleared ({leads_cleared} rows removed)\n"
        f"lead_activity_log.csv     → cleared ({activity_cleared} rows removed)\n"
        f"pending_writes.jsonl      → {'cleared' if queue_cleared else 'not found (ok)'}\n"
        f"country_rep_mapping.csv   → untouched "
        f"({'exists' if mapping_path.exists() else 'not found'})\n\n"
        f"⚠️  DATABRICKS TABLES NOT YET CLEARED.\n"
        f"Claude must now execute these 2 SQL statements via execute_sql():\n"
        f"  1. DELETE FROM {DB_LEADS}\n"
        f"  2. DELETE FROM {DB_ACTIVITY}\n\n"
        f"Ready for a fresh pipeline run after Databricks is cleared.\n"
        f"Run Phase 1 (lead-ingestion) to start ingesting emails from the mailbox."
    )

    return {
        "success": True,
        "leads_cleared": leads_cleared,
        "activity_cleared": activity_cleared,
        "queue_cleared": queue_cleared,
        "databricks_sql": databricks_sql,
        "message": msg,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python reset_pipeline.py <workspace_path>")
        sys.exit(1)
    result = reset_pipeline(sys.argv[1])
    print(result["message"])
    sys.exit(0 if result["success"] else 1)
