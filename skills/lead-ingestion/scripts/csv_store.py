"""
csv_store.py
------------
Shim — re-exports DBStore as CSVStore for backward compatibility.

All logic has moved to db_store.py, which writes to local CSVs AND queues
every write to data/pending_writes.jsonl for Databricks sync.

Do NOT import anything else from this file; use db_store directly if needed.
"""
from db_store import DBStore as CSVStore  # noqa: F401 — public re-export

__all__ = ["CSVStore"]
