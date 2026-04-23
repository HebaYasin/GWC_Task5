"""
scan_cc_emails.py
-----------------
Utilities for finding and fetching CC'd emails in the shared mailbox
that belong to a specific GWC lead (identified by GWC ID in subject).

Strategy:
  - The shared mailbox Sales.rfq@gwclogistics.com receives:
      1. Original HubSpot lead emails (subject: "New Freight Opportunity - GWC-XXXX")
      2. CC'd conversation emails (any email where subject contains the GWC-XXXX ID)
  - We search the inbox for all emails whose subject contains the GWC ID
  - We exclude the original HubSpot notification (already logged as EMAIL_RECEIVED)
  - The remaining emails are the conversation thread between rep and customer

Note on subject format: HubSpot emails use "New Freight Opportunity - GWC-XXXX" (v2 format).
The [BULK] prefix (v1 format) is no longer used but detection remains backward-compatible.

Usage (called from SKILL.md instructions, not imported directly):
    from scan_cc_emails import get_active_leads, build_thread_payload, classify_email_role

    active = get_active_leads(store)                      # leads to scan
    thread = build_thread_payload(emails, lead, store)    # structured for AI analysis
"""

import re
from datetime import datetime, timezone, timedelta
from typing import Optional


# ── Constants ─────────────────────────────────────────────────────────────────

SHARED_MAILBOX = "Sales.rfq@gwclogistics.com"

# Statuses that are still "active" and should be scanned for CC emails
ACTIVE_STATUSES = {"NO_ACTION", "ENGAGED", "QUOTED", "FOLLOW_UP"}

# Statuses that are terminal — no need to scan
TERMINAL_STATUSES = {"REJECTED", "WON_LOSS", "GAP_ANALYSIS"}

# Days of silence that trigger a "dark lead" flag
DARK_LEAD_DAYS = 5

# Keywords for status detection (used in analyze_thread.py, listed here for reference)
QUOTE_KEYWORDS = [
    "quotation", "quote", "pricing", "rate", "rates", "proposal",
    "freight charges", "our offer", "please find attached", "rfq",
    "rate sheet", "cost breakdown", "costing",
]
FOLLOWUP_KEYWORDS = [
    "please confirm", "awaiting your confirmation", "follow up", "follow-up",
    "checking in", "any update", "kindly revert", "please advise",
    "update on", "status of", "still interested",
]
DEAL_WON_KEYWORDS = [
    "confirmed", "proceed", "go ahead", "book", "booked", "we accept",
    "deal confirmed", "shipment confirmed", "please proceed", "won",
    "awarded", "let's proceed", "lets proceed",
]
DEAL_LOST_KEYWORDS = [
    "not interested", "cancelled", "cancel", "no longer", "we have chosen",
    "we went with", "declined", "rejected", "closed", "lost", "not proceeding",
    "unfortunately", "found another", "decided against",
]


# ── Active lead retrieval ─────────────────────────────────────────────────────

def get_active_leads(store) -> list[dict]:
    """
    Return all leads that are in an active status (need CC email scanning).
    Excludes REJECTED, WON_LOSS, GAP_ANALYSIS.
    """
    rows = store._read_csv(store.leads_path)
    return [r for r in rows if r.get("current_status", "") in ACTIVE_STATUSES]


# ── Email role classification ─────────────────────────────────────────────────

def classify_email_role(email: dict, lead: dict, store) -> str:
    """
    Classify who sent this email: 'rep', 'customer', or 'system'.

    Logic:
    - If sender email matches a GWC rep in country_rep_mapping → 'rep'
    - If sender email is @gwclogistics.com → 'rep' (internal)
    - If sender email matches the shared mailbox → 'system'
    - Otherwise → 'customer'
    """
    sender = (email.get("from", {}).get("emailAddress", {}).get("address") or "").lower()

    if not sender:
        return "unknown"
    if sender == SHARED_MAILBOX.lower():
        return "system"
    if sender.endswith("@gwclogistics.com"):
        return "rep"

    # Check if it's the assigned rep
    assigned_rep = (lead.get("assigned_rep_email") or "").lower()
    if assigned_rep and sender == assigned_rep:
        return "rep"

    return "customer"


# ── GWC ID extraction from subject ───────────────────────────────────────────

def extract_gwc_id_from_subject(subject: str) -> Optional[str]:
    """Extract GWC-XXXXXX from any subject line."""
    m = re.search(r"(GWC-\d+)", subject or "")
    return m.group(1) if m else None


# ── Thread payload builder (for AI analysis) ──────────────────────────────────

def build_thread_payload(emails: list[dict], lead: dict, store) -> dict:
    """
    Build a structured payload of a CC'd email thread for Claude to analyse.

    Returns a dict:
    {
      "gwc_id":        str,
      "lead_summary":  {...},      # key lead fields
      "thread":        [           # chronological list of emails
        {
          "index":      int,
          "role":       "rep"|"customer"|"system",
          "sender":     str,
          "subject":    str,
          "date":       str,
          "body":       str,       # first 2000 chars of plain text body
          "is_original_hubspot": bool,
        },
        ...
      ],
      "thread_count":  int,
      "date_range":    {"first": str, "last": str},
    }
    """
    # Sort chronologically
    def parse_dt(s):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    sorted_emails = sorted(emails, key=lambda e: parse_dt(e.get("receivedDateTime", "")))

    thread = []
    for i, email in enumerate(sorted_emails):
        subject = email.get("subject", "")
        body_raw = email.get("body", {}).get("content", "") or email.get("bodyPreview", "")
        body_text = _strip_html(body_raw)[:2000]  # cap at 2000 chars per email

        # Identify original HubSpot notification email by subject pattern.
        # Current format: "New Freight Opportunity - GWC-XXXX"
        # Legacy format:  "[BULK] New Freight Opportunity for GWC-XXXX"
        # Both are detected by "new freight opportunity" substring (case-insensitive).
        is_original = "new freight opportunity" in subject.lower()

        thread.append({
            "index":    i + 1,
            "role":     classify_email_role(email, lead, store),
            "sender":   email.get("from", {}).get("emailAddress", {}).get("address", ""),
            "subject":  subject,
            "date":     email.get("receivedDateTime", ""),
            "body":     body_text,
            "is_original_hubspot": is_original,
        })

    first_date = sorted_emails[0].get("receivedDateTime", "") if sorted_emails else ""
    last_date  = sorted_emails[-1].get("receivedDateTime", "") if sorted_emails else ""

    return {
        "gwc_id": lead.get("gwc_id", ""),
        "lead_summary": {
            "contact_name":    lead.get("contact_name", ""),
            "company_name":    lead.get("company_name", ""),
            "from_country":    lead.get("from_country", ""),
            "to_country":      lead.get("to_country", ""),
            "mode_of_freight": lead.get("mode_of_freight", ""),
            "classification":  lead.get("classification", ""),
            "current_status":  lead.get("current_status", ""),
            "assigned_rep":    lead.get("assigned_rep_email", ""),
        },
        "thread":       thread,
        "thread_count": len(thread),
        "date_range":   {"first": first_date, "last": last_date},
    }


# ── Working rep detector ─────────────────────────────────────────────────────

def detect_first_gwc_sender(thread_payload: dict, store) -> tuple:
    """
    Return (email, name) of the first @gwclogistics.com sender in the thread
    who is NOT the shared mailbox itself and NOT the HubSpot original email.

    This is the "detected working rep" — the person who actually sent the first
    reply to the customer. Used as a fallback when bd_poc_email is blank.

    Only applies when the lead transitions to ENGAGED (or is already ENGAGED+).
    Looks up the sender's name from country_rep_mapping.csv if available.

    Returns ("", "") if no qualifying GWC sender is found in the thread.
    """
    for msg in thread_payload.get("thread", []):
        if msg.get("role") != "rep":
            continue
        if msg.get("is_original_hubspot"):
            continue
        sender = (msg.get("sender") or "").strip().lower()
        if not sender or sender == SHARED_MAILBOX.lower():
            continue
        if not sender.endswith("@gwclogistics.com"):
            continue

        # Look up name in rep mapping
        try:
            rows = store._read_csv(store.mapping_path)
            for row in rows:
                if (row.get("rep_email") or "").strip().lower() == sender:
                    return sender, (row.get("rep_name") or "").strip()
        except Exception:
            pass

        # Sender is a GWC address not in the mapping — derive a display name
        local = sender.split("@")[0]
        display = " ".join(p.capitalize() for p in re.split(r"[._\-]", local))
        return sender, display

    return "", ""


# ── Dark lead detection ───────────────────────────────────────────────────────

def is_dark_lead(lead: dict, latest_email_date: Optional[str]) -> bool:
    """
    Return True if a lead in ENGAGED+ status has had no CC'd email activity
    for DARK_LEAD_DAYS or more.

    A lead is "dark" if:
    - Its status is ENGAGED, QUOTED, or FOLLOW_UP
    - The latest CC'd email (or email_received_at if no CC emails) was
      DARK_LEAD_DAYS+ days ago
    """
    status = lead.get("current_status", "")
    if status not in {"ENGAGED", "QUOTED", "FOLLOW_UP"}:
        return False

    def parse_dt(s):
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    ref_date = parse_dt(latest_email_date) or parse_dt(lead.get("email_received_at", ""))
    if not ref_date:
        return False

    now = datetime.now(timezone.utc)
    return (now - ref_date).days >= DARK_LEAD_DAYS


# ── HTML stripper ─────────────────────────────────────────────────────────────

def _strip_html(html: str) -> str:
    """Strip HTML tags and normalise whitespace."""
    if not html:
        return ""
    import re
    clean = re.sub(r"<[^>]+>", " ", html)
    clean = re.sub(r"&nbsp;", " ", clean)
    clean = re.sub(r"&amp;", "&", clean)
    clean = re.sub(r"&lt;", "<", clean)
    clean = re.sub(r"&gt;", ">", clean)
    clean = re.sub(r"&#\d+;", "", clean)
    clean = re.sub(r"\s{3,}", "\n\n", clean)
    return clean.strip()


# ── Days since last activity ──────────────────────────────────────────────────

def days_since(iso_timestamp: str) -> Optional[int]:
    """Return number of days since a given ISO 8601 timestamp, or None."""
    if not iso_timestamp:
        return None
    try:
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None


# ── Pre-April-8 orphan detection ──────────────────────────────────────────────

# Subject pattern used by lead-ingestion to ingest HubSpot emails.
# Emails matching this pattern are handled by Phase 1 — we never treat them as orphans.
INGESTION_SUBJECT_PATTERN = re.compile(
    r"new\s+freight\s+opportunity",
    re.IGNORECASE,
)

# The pipeline start date — leads received BEFORE this date were never ingested.
PIPELINE_START_DATE = "2026-04-08"


def is_orphan_email(email: dict, known_gwc_ids: set) -> tuple[bool, str]:
    """
    Detect emails that reference a GWC ID that is NOT in the tracker,
    and whose subject does NOT match the lead-ingestion intake pattern.

    These are "pre-pipeline" conversations: the original HubSpot lead
    arrived before April 8 and was never ingested, but reps have been
    actively emailing under its GWC ID.

    Returns:
        (True, gwc_id)  — orphan confirmed; gwc_id extracted
        (False, "")     — not an orphan
    """
    subject = email.get("subject", "") or ""
    body    = (email.get("body", {}).get("content", "")
               or email.get("bodyPreview", "")
               or "")

    # Skip emails that match the ingestion pattern — Phase 1 handles those.
    if INGESTION_SUBJECT_PATTERN.search(subject):
        return False, ""

    # Try subject first, then body
    gwc_id = extract_gwc_id_from_subject(subject)
    if not gwc_id:
        gwc_id = extract_gwc_id_from_subject(body)  # reuses same regex
    if not gwc_id:
        return False, ""

    # Only orphan if the GWC ID is genuinely absent from the tracker
    if gwc_id in known_gwc_ids:
        return False, ""

    return True, gwc_id


def build_orphan_stub(gwc_id: str, emails: list[dict], store) -> dict:
    """
    Build a minimal lead stub for an orphan GWC ID so it can be written
    into leads_maturity.csv before thread analysis runs.

    We can't reconstruct HubSpot fields (the original email is pre-pipeline),
    so we populate only what we can infer from the conversation emails.

    The stub is intentionally flagged:
      - classification = "PRE_PIPELINE"
      - current_status = "NO_ACTION"  (will be immediately updated by AI analysis)
      - notes          = records the backfill context

    Args:
        gwc_id:  The GWC ID extracted from the orphan email
        emails:  All emails found for this GWC ID in the mailbox
        store:   CSVStore instance (used to infer rep from @gwclogistics.com senders)

    Returns:
        dict suitable for store.upsert_lead()
    """
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Sort chronologically to find earliest email date
    def _parse_dt(s):
        try:
            return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    sorted_emails = sorted(emails, key=lambda e: _parse_dt(e.get("receivedDateTime", "")))
    earliest = sorted_emails[0] if sorted_emails else {}
    earliest_date = earliest.get("receivedDateTime", now_iso)

    # Try to infer rep from any @gwclogistics.com sender in the thread
    assigned_rep_email = ""
    assigned_rep_name  = ""
    for e in sorted_emails:
        sender = (e.get("from", {}).get("emailAddress", {}) or {})
        addr   = (sender.get("address") or "").lower()
        name   = sender.get("name", "")
        if addr.endswith("@gwclogistics.com") and addr != SHARED_MAILBOX.lower():
            assigned_rep_email = addr
            assigned_rep_name  = name
            break

    # Try to infer destination country from subjects / bodies for rep mapping
    # (best-effort; leave blank if not detectable)
    to_country = ""
    for e in sorted_emails:
        body_raw = (e.get("body", {}).get("content", "")
                    or e.get("bodyPreview", "")
                    or "")
        # Simple heuristic: look for "to: <country>" or "destination: <country>" pattern
        m = re.search(
            r"(?:to\s*country|destination)[:\s]+([A-Za-z\s]{3,30})",
            body_raw, re.IGNORECASE
        )
        if m:
            to_country = m.group(1).strip()[:50]
            break

    return {
        "gwc_id":            gwc_id,
        "email_message_id":  earliest.get("id", ""),
        "classification":    "PRE_PIPELINE",          # flag: backfilled, not ingested
        "current_status":    "NO_ACTION",             # will be updated immediately by AI
        "assigned_rep_email": assigned_rep_email,
        "assigned_rep_name":  assigned_rep_name,
        "to_country":         to_country,
        "email_received_at":  earliest_date,          # earliest email = proxy for lead arrival
        "hubspot_create_date": "",                    # unknown — predates pipeline
        "notes": (
            f"[BACKFILLED {now_iso[:10]}] Pre-pipeline lead. "
            f"Original HubSpot email received before {PIPELINE_START_DATE} "
            f"(pipeline start). Status assigned by AI analysis of email thread. "
            f"Structural fields (origin, product, weight etc.) unknown — "
            f"retrieve from HubSpot if required."
        ),
        "missing_fields": "[]",
        "status_history":  "[]",
        "created_at":      now_iso,
    }