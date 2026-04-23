"""
quip_checker.py
---------------
Cross-checks an ingested lead against the "Digital Sales Leads" section of the
Regional Contingency RFQ Tracker Quip document.

Sheet identity
--------------
Document : "Regional Contingency RFQ Tracker"
URL      : https://gwc1.quip.com/XbavARpEgyTa/Regional-Contingency-RFQ-Tracker
Anchor   : #temp:C:CIV40540f74cfa1447b90293eb9d  (Digital Sales Leads section)
Thread ID: XbavARpEgyTa

HOW TO READ THIS SHEET
-----------------------
The Quip document contains TWO embedded spreadsheets:
  1. Continental RFQ Tracker  (rows 1–33 in get_sheet_structure output)
  2. Digital Sales Leads      (rows 34+ in get_sheet_structure output)

Use mcp__quip__get_sheet_structure with thread_id="XbavARpEgyTa" — NOT read_sheet,
which returns only the first embedded spreadsheet and would miss the Digital Sales section.
Pass the raw result to load_from_structure_data() which extracts only rows 34+.

Digital Sales Leads column layout (all columns are letters A–AF):
  Col A  : Sr. (sequential row number)
  Col C  : Client Name
  Col D  : GWC Record ID  ← primary key for cross-checking
  Col E  : Company Name
  Col F  : Source (Paid Campaign / Organic / etc.)
  Col G  : Country  ← routing country (GWC office: Qatar / UAE / KSA / Bahrain / Oman)
  Col H  : Email
  Col I  : Phone Number
  Col J  : Inquiry Received Date
  Col K  : Enquiry Details
  Col L  : From (origin location)
  Col M  : To (destination location)
  Col N  : Lead Status
  Col O  : GWC BD POC  ← BD point of contact name
  Col P  : Deal Status
  Col Q  : BD comments / update
  Col R+ : "Update as of DD Month" columns (chronological update history)

Key functions:
    load_from_structure_data(cell_lines)  → dict[gwc_id → row_dict]   (primary)
    load_from_mcp_data(rows)              → pd.DataFrame | None        (legacy fallback)
    check_lead_in_quip(gwc_id, data)      → dict with keys:
        found_in_quip    bool
        quip_country     str   — from col G "Country"
        bd_poc_name      str   — from col O "GWC BD POC" (resolved name)
        bd_poc_email     str   — email matched from country_rep_mapping.csv
        raw_updates      str   — pipe-joined "DD Mon: …" entries
        updates_summary  str   — inline AI summary (Claude fills this)
"""

import os
import re
import json
from pathlib import Path


# ── Summary prompt — read and execute this inline as Claude ──────────────────

QUIP_SUMMARY_PROMPT = """
You are summarising daily marketing/sales updates for freight lead {gwc_id}.
These updates were recorded by the marketing team in sequential daily entries:

{updates_text}

Write a concise 2-3 sentence summary of the lead's progress, current status,
and any key actions taken or pending. Be factual and specific.
Do not add information not present in the updates.
"""


# ── Constants ────────────────────────────────────────────────────────────────

QUIP_THREAD_ID   = "XbavARpEgyTa"

# Digital Sales Leads column letters (in get_sheet_structure output)
QUIP_COL_GWC_ID  = "D"   # GWC Record ID — primary key
QUIP_COL_COUNTRY = "G"   # Country — routing GWC office (Qatar/UAE/KSA/Bahrain/Oman)
QUIP_COL_BD_POC  = "O"   # GWC BD POC — working rep name

# The Digital Sales Leads section starts at this row in the flattened structure output.
# Rows 1–33 belong to the Continental RFQ Tracker (different schema).
QUIP_DIGITAL_SALES_START_ROW = 34

# Legacy DataFrame column names (kept for load_from_mcp_data backward-compat)
QUIP_GWC_ID_COL  = "GWC Record ID"
QUIP_COUNTRY_COL = "Country"
QUIP_BD_POC_COL  = "GWC BD POC"

# ── BD POC name → email resolver ─────────────────────────────────────────────

def _resolve_poc_email(poc_name: str, workspace_dir: str) -> tuple:
    """
    Fuzzy-match a BD POC name from the Quip sheet to an email in
    country_rep_mapping.csv.

    Matching strategy (in order):
      1. Exact normalised-name match (case-insensitive, whitespace-collapsed)
      2. All words in poc_name are contained (as whole words) in the rep name
      3. All words in poc_name appear as substrings of any word in the rep name

    Rules:
    - If poc_name is empty, "WIP", "N/A", "nan", or "none" → return ("", "")
      (caller must fall back to detected_working_rep_email from Phase 3 scan)
    - Names can be 1, 2, or 3 tokens (e.g. "Dina", "Rafat AlZourgan",
      "Farooque Abdulkarimabdul Vala").

    Returns (matched_rep_name: str, matched_rep_email: str).
    Returns (poc_name, "") if a name was given but no email match was found.
    """
    import csv as _csv

    skip_values = {"", "wip", "n/a", "nan", "none", "-", "tbd", "na"}
    if not poc_name or str(poc_name).strip().lower() in skip_values:
        return "", ""

    mapping_path = Path(workspace_dir) / "data" / "country_rep_mapping.csv"
    if not mapping_path.exists():
        return poc_name.strip(), ""

    try:
        with open(mapping_path, newline="", encoding="utf-8") as f:
            rows = list(_csv.DictReader(f))
    except Exception:
        return poc_name.strip(), ""

    def _norm(s):
        return re.sub(r"\s+", " ", str(s or "").strip().lower())

    poc_norm  = _norm(poc_name)
    poc_parts = poc_norm.split()

    # Pass 1 — exact full-name match
    for row in rows:
        if _norm(row.get("rep_name", "")) == poc_norm:
            return row["rep_name"].strip(), row.get("rep_email", "").strip()

    # Pass 2 — all poc_parts appear as whole words in rep_name
    for row in rows:
        rep_parts = _norm(row.get("rep_name", "")).split()
        if all(p in rep_parts for p in poc_parts):
            return row["rep_name"].strip(), row.get("rep_email", "").strip()

    # Pass 3 — all poc_parts appear as substrings within any rep word (handles
    # abbreviated or hyphenated names like "Abdulkarim" matching "Abdulkarimabdul")
    for row in rows:
        rep_parts = _norm(row.get("rep_name", "")).split()
        if all(any(p in rp for rp in rep_parts) for p in poc_parts):
            return row["rep_name"].strip(), row.get("rep_email", "").strip()

    # Name found in Quip but not resolved to a rep — return name as-is, no email
    return poc_name.strip(), ""


# ── Primary loader: parse mcp__quip__get_sheet_structure output ──────────────

def load_from_structure_data(structure_text: str) -> dict:
    """
    Parse the raw text output of mcp__quip__get_sheet_structure(thread_id="XbavARpEgyTa")
    and return a dict mapping gwc_id → row_dict for every Digital Sales Lead row.

    The Quip document contains two embedded spreadsheets rendered as one flat cell map:
      Rows  1–33 : Continental RFQ Tracker  (different schema — ignored)
      Rows 34+   : Digital Sales Leads      (col D = GWC ID, col G = Country, col O = BD POC)

    Returns:
        {
            "GWC-XXXXXXXXXX": {
                "gwc_id":      str,
                "country":     str,   # col G — routing GWC office
                "bd_poc":      str,   # col O — GWC BD POC name (may be "")
                "client":      str,   # col C
                "company":     str,   # col E
                "from_loc":    str,   # col L
                "to_loc":      str,   # col M
                "deal_status": str,   # col P
                "raw_updates": str,   # pipe-joined "DD Mon: …" from cols R+
            },
            ...
        }
    Returns {} if parsing fails or no rows found.
    """
    if not structure_text:
        return {}

    cell_map: dict[tuple, str] = {}
    _zwsp = "​"  # zero-width space placeholder used by Quip for empty cells

    for line in structure_text.splitlines():
        m = re.match(r'^([A-Z]{1,3})(\d+)\s+\|\s+(.*)', line.strip())
        if m:
            col, row_str, val = m.group(1), m.group(2), m.group(3).strip()
            if val and val != _zwsp:
                cell_map[(col, int(row_str))] = val

    if not cell_map:
        return {}

    # Identify "Update as of…" column letters for rows ≥ QUIP_DIGITAL_SALES_START_ROW
    # We scan the header row of the Digital Sales section (row 1 of that sheet maps to
    # row QUIP_DIGITAL_SALES_START_ROW - 1 in the flattened output, but in practice the
    # update column labels appear as cell values in rows near the start of the section).
    # Simpler: scan all column letters present in the DS section for "Update as of" values.
    update_col_map: dict[str, tuple] = {}  # col_letter → (month_idx, day) for sorting
    _months = ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"]

    def _update_sort_key(col_letter: str):
        val = cell_map.get((col_letter, QUIP_DIGITAL_SALES_START_ROW - 1), "")
        if not val:
            # also check row 1 (some sheets store headers there)
            val = cell_map.get((col_letter, 1), "")
        m2 = re.search(r"(\d{1,2})\s+(\w+)", val)
        if not m2:
            return (99, 99)
        day = int(m2.group(1))
        mon = next((i+1 for i, mn in enumerate(_months) if mn in m2.group(2).lower()), 0)
        return (mon, day)

    # Find all unique column letters that carry "Update as of" headers
    update_cols: list[str] = []
    for (col, row), val in cell_map.items():
        if re.search(r"Update\s+as\s+of\s+\d{1,2}\s+\w+", val, re.IGNORECASE):
            if col not in update_cols:
                update_cols.append(col)
    update_cols.sort(key=_update_sort_key)

    # Parse Digital Sales rows (QUIP_DIGITAL_SALES_START_ROW onwards)
    gwc_pattern = re.compile(r"GWC-\d+")
    result: dict[str, dict] = {}

    max_row = max(r for _, r in cell_map.keys())
    for row in range(QUIP_DIGITAL_SALES_START_ROW, max_row + 1):
        gwc_raw = cell_map.get((QUIP_COL_GWC_ID, row), "")
        gwc_ids = gwc_pattern.findall(gwc_raw)
        if not gwc_ids:
            continue

        gwc_id = gwc_ids[0]

        # Build pipe-delimited update history
        update_parts = []
        for uc in update_cols:
            uval = cell_map.get((uc, row), "")
            if uval:
                # Find the column header to use as a label
                hdr = cell_map.get((uc, QUIP_DIGITAL_SALES_START_ROW - 1), uc)
                lm = re.search(r"(\d{1,2}\s+\w+)", hdr)
                label = lm.group(1) if lm else uc
                update_parts.append(f"{label}: {uval}")

        result[gwc_id] = {
            "gwc_id":      gwc_id,
            "country":     cell_map.get((QUIP_COL_COUNTRY, row), ""),
            "bd_poc":      cell_map.get((QUIP_COL_BD_POC,  row), ""),
            "client":      cell_map.get(("C", row), ""),
            "company":     cell_map.get(("E", row), ""),
            "from_loc":    cell_map.get(("L", row), ""),
            "to_loc":      cell_map.get(("M", row), ""),
            "deal_status": cell_map.get(("P", row), ""),
            "raw_updates": " | ".join(update_parts),
        }

    print(f"[quip_checker] load_from_structure_data: parsed {len(result)} Digital Sales Leads rows")
    return result


# ── Legacy loader: mcp__quip__read_sheet payload (first embedded sheet only) ─

def load_from_mcp_data(rows: list) -> "pd.DataFrame | None":
    """
    Legacy: accept a list-of-dicts from mcp__quip__read_sheet and return a DataFrame.
    NOTE: read_sheet returns only the FIRST embedded spreadsheet in the document
    (the Continental RFQ Tracker), which does NOT contain the Digital Sales Leads.
    Prefer load_from_structure_data() for all new code.
    """
    if not rows:
        return None
    try:
        import pandas as pd
        df = pd.DataFrame(rows)
        df.columns = [str(c).strip() for c in df.columns]
        return df
    except Exception as e:
        print(f"[quip_checker] Warning: could not convert MCP rows to DataFrame: {e}")
        return None


# ── Core check ────────────────────────────────────────────────────────────────

def check_lead_in_quip(gwc_id: str, data, workspace_dir: str = "") -> dict:
    """
    Cross-check a GWC ID against the Digital Sales Leads section of thread XbavARpEgyTa.

    Args:
        gwc_id        : the GWC lead ID to look up (e.g. "GWC-741560809701")
        data          : one of:
                          • dict[gwc_id → row_dict]  from load_from_structure_data()  ← preferred
                          • pd.DataFrame              from load_from_mcp_data()        ← legacy
        workspace_dir : path to the workspace root, used to resolve BD POC emails

    Returns:
        {
            "found_in_quip":   bool,
            "quip_country":    str,   # col G "Country" — routing GWC office
            "bd_poc_name":     str,   # col O "GWC BD POC" — resolved rep name
            "bd_poc_email":    str,   # email from country_rep_mapping.csv (may be "")
            "raw_updates":     str,   # pipe-joined dated updates
            "updates_summary": str,   # inline AI summary (Claude fills this)
        }

    bd_poc_name / bd_poc_email are empty when the Quip cell is blank or "WIP".
    Phase 3 fills detected_working_rep_email as a fallback in that case.
    """
    _empty = {
        "found_in_quip": False, "quip_country": "",
        "bd_poc_name": "", "bd_poc_email": "",
        "raw_updates": "", "updates_summary": "",
    }

    if data is None:
        return _empty

    # ── Path A: new dict-based lookup (load_from_structure_data) ─────────────
    if isinstance(data, dict):
        row = data.get(gwc_id)
        if not row:
            return _empty

        quip_country = _norm_country(row.get("country", ""))
        bd_poc_raw   = row.get("bd_poc", "").strip()
        raw_updates  = row.get("raw_updates", "")

        bd_poc_name, bd_poc_email = _resolve_poc_email(bd_poc_raw, workspace_dir)

        updates_summary = ""
        if raw_updates:
            updates_text = "\n".join(f"- {u}" for u in raw_updates.split(" | "))
            _ = QUIP_SUMMARY_PROMPT.format(gwc_id=gwc_id, updates_text=updates_text)
            # Claude: fill updates_summary inline by reasoning over QUIP_SUMMARY_PROMPT above.
            updates_summary = "<CLAUDE: fill this in by reasoning over the prompt above>"

        return {
            "found_in_quip":   True,
            "quip_country":    quip_country,
            "bd_poc_name":     bd_poc_name,
            "bd_poc_email":    bd_poc_email,
            "raw_updates":     raw_updates,
            "updates_summary": updates_summary,
        }

    # ── Path B: legacy DataFrame lookup (load_from_mcp_data) ─────────────────
    try:
        import pandas as _pd
        if not isinstance(data, _pd.DataFrame):
            return _empty
    except ImportError:
        return _empty

    df = data
    id_col = next(
        (c for c in df.columns if re.search(r"GWC\s*Record\s*ID|GWC[\s_-]*ID", c, re.IGNORECASE)),
        None,
    )
    if id_col is None:
        print(f"[quip_checker] Warning: GWC Record ID column not found in DataFrame.")
        return _empty

    def _norm_id(val):
        return str(val or "").strip().upper().replace(" ", "")

    gwc_norm   = _norm_id(gwc_id)
    match_row  = None
    for _, row in df.iterrows():
        if not str(row.get(id_col, "") or "").strip():
            continue
        if _norm_id(row.get(id_col, "")) == gwc_norm:
            match_row = row
            break

    if match_row is None:
        return _empty

    # Country
    country_col = next(
        (c for c in df.columns if re.search(r'\bCountry\b|\bSupport\b', c, re.IGNORECASE)), None
    )
    quip_country = ""
    if country_col:
        raw_c = str(match_row.get(country_col, "") or "").strip()
        if raw_c.lower() not in ("nan", "none", "n/a", ""):
            quip_country = _norm_country(raw_c)

    # BD POC
    poc_col = next(
        (c for c in df.columns if re.search(r"BD\s*POC|GWC\s*BD", c, re.IGNORECASE)), None
    )
    bd_poc_raw = ""
    if poc_col:
        raw_poc = str(match_row.get(poc_col, "") or "").strip()
        if raw_poc.lower() not in ("nan", "none", "n/a", "", "wip", "-", "tbd"):
            bd_poc_raw = raw_poc

    bd_poc_name, bd_poc_email = _resolve_poc_email(bd_poc_raw, workspace_dir)

    # Updates
    update_cols = sorted(
        [c for c in df.columns if re.search(r"Update\s+as\s+of\s+\d{1,2}\s+\w+", c, re.IGNORECASE)],
        key=lambda c: _update_col_sort_key(c),
    )
    update_parts = []
    for col in update_cols:
        val = str(match_row.get(col, "") or "").strip()
        if val and val.lower() not in ("nan", "none", "n/a", ""):
            lm = re.search(r"(\d{1,2}\s+\w+)", col)
            label = lm.group(1) if lm else col
            update_parts.append(f"{label}: {val}")

    raw_updates = " | ".join(update_parts)
    updates_summary = ""
    if update_parts:
        updates_text = "\n".join(f"- {u}" for u in update_parts)
        _ = QUIP_SUMMARY_PROMPT.format(gwc_id=gwc_id, updates_text=updates_text)
        updates_summary = "<CLAUDE: fill this in by reasoning over the prompt above>"

    return {
        "found_in_quip":   True,
        "quip_country":    quip_country,
        "bd_poc_name":     bd_poc_name,
        "bd_poc_email":    bd_poc_email,
        "raw_updates":     raw_updates,
        "updates_summary": updates_summary,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _norm_country(raw: str) -> str:
    """Normalise country name to consistent title-case, fixing common abbreviations."""
    c = raw.strip().title()
    overrides = {"Ksa": "KSA", "Uae": "UAE"}
    return overrides.get(c, c)


def _update_col_sort_key(col: str) -> tuple:
    _months = ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"]
    m = re.search(r"(\d{1,2})\s+(\w+)", col)
    if not m:
        return (99, 99)
    day = int(m.group(1))
    mon = next((i+1 for i, mn in enumerate(_months) if mn in m.group(2).lower()), 0)
    return (mon, day)

