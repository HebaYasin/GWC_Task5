"""
parse_lead_email.py
-------------------
Deterministic regex/string parser for HubSpot → GWC lead emails.

Expected email body format:
    New Freight Opportunity has arrived

    Name: [contact name]
    Company: [company name]
    GWC ID: GWC-[numeric ID]
    Phone: [phone]
    WhatsApp: [whatsapp]

    Shipment Details
    From Country: [origin]
    To Country: [destination]
    Origin Country (Alt): [alt origin or N/A]
    Destination Country (Alt): [alt destination or N/A]
    Mode of Freight: [Ocean LCL / Ocean FCL / Air / Land]
    Product: [commodity description]
    Amount (KG): [weight]
    Shipping Requirements: [special requirements or N/A]

    Additional Info
    Notes: [free text notes]
    Create Date: [MM/DD/YYYY]
    Date Turned into Opportunity: [date or N/A]

Usage:
    from parse_lead_email import parse_lead_email
    fields = parse_lead_email(subject, body)
"""

import html as _html_module
import re
from datetime import datetime
from typing import Optional


# ── HTML stripping ────────────────────────────────────────────────────────────

# Field labels that must start on their own line for the regex anchors to work.
# Used to insert newlines before each label even if HTML collapsing joined lines.
_FIELD_LABELS = [
    "Name", "Company", "GWC ID", "Phone", "WhatsApp",
    "From Country", "To Country", "Origin Country (Alt)", "Destination Country (Alt)",
    "Mode of Freight", "Product", "Amount (KG)", "Shipping Requirements",
    "Notes", "Create Date", "Date Turned into Opportunity",
]

def strip_html(html: str) -> str:
    """
    Convert an HTML email body to plain text that parse_lead_email() can work with.

    Strategy:
      1. Replace block-level / line-break tags with newlines so table rows and
         div/p elements each end up on their own line.
      2. Strip all remaining HTML tags.
      3. Ensure each known field label sits at the start of its own line so the
         MULTILINE ^ anchors in _extract() can match.
      4. Collapse excessive blank lines and leading/trailing whitespace.
    """
    # Step 1: decode ALL HTML entities first (handles &nbsp; &#39; &#xA0; etc.)
    html = _html_module.unescape(html)

    # Step 2: block tags → newline
    block_pattern = re.compile(
        r'<(?:br\s*/?)>|</(?:tr|td|th|p|div|li|h[1-6])>',
        re.IGNORECASE,
    )
    text = block_pattern.sub('\n', html)

    # Step 3: strip remaining tags
    text = re.sub(r'<[^>]+>', ' ', text)

    # Step 4: clean up any residual named entities missed by unescape (safety net)
    text = (text
            .replace('&nbsp;', ' ')
            .replace('&amp;', '&')
            .replace('&lt;', '<')
            .replace('&gt;', '>')
            .replace('&quot;', '"')
            .replace('&#39;', "'"))

    # Step 5: ensure each known field label starts on a new line
    for label in _FIELD_LABELS:
        text = re.sub(
            r'(?<!\n)(' + re.escape(label) + r'\s*:)',
            r'\n\1',
            text,
        )

    # Step 6: collapse runs of blank lines and strip
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _is_html(text: str) -> bool:
    """Return True if the string looks like an HTML document or fragment."""
    return bool(re.search(r'<(?:html|body|table|tr|td|br|div|p)\b', text, re.IGNORECASE))


# ── Helpers ──────────────────────────────────────────────────────────────────

def _extract(pattern: str, text: str, flags=re.IGNORECASE) -> Optional[str]:
    """Extract first capture group from text; return None if no match."""
    m = re.search(pattern, text, flags)
    if not m:
        return None
    val = m.group(1).strip()
    # Treat placeholder values as empty
    if val.lower() in ("n/a", "na", "-", "none", ""):
        return None
    return val


def _normalise_mot(raw: Optional[str]) -> Optional[str]:
    """Normalise Mode of Freight to canonical form: Air / Sea / Overland."""
    if not raw:
        return None
    r = raw.lower()
    if "air" in r:
        return "Air"
    if "ocean" in r or "sea" in r or "lcl" in r or "fcl" in r or "bbk" in r or "roro" in r:
        return "Sea"
    if "land" in r or "overland" in r or "ground" in r or "ltl" in r or "ftl" in r or "truck" in r:
        return "Overland"
    return raw.title()


def _normalise_container_mode(raw_mot_text: Optional[str]) -> Optional[str]:
    """
    Derive container mode from the raw Mode of Freight field text.
    E.g. "Ocean LCL" → "LCL", "Ocean FCL" → "FCL", "Air" → "Loose Cargo"
    """
    if not raw_mot_text:
        return None
    r = raw_mot_text.lower()
    if "lcl" in r:
        return "LCL"
    if "fcl" in r:
        return "FCL"
    if "bbk" in r:
        return "BBK"
    if "roro" in r:
        return "RORO"
    if "ltl" in r:
        return "LTL"
    if "ftl" in r:
        return "FTL"
    if "air" in r:
        return "Loose Cargo"
    if "land" in r or "overland" in r:
        return "FTL"
    return None


def _parse_weight(raw: Optional[str]) -> Optional[float]:
    """Extract numeric weight from strings like '500 KG', '1,200', '3.5'."""
    if not raw:
        return None
    # Remove commas, strip non-numeric except dot
    cleaned = re.sub(r"[^\d.]", "", raw.replace(",", ""))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _parse_date(raw: Optional[str]) -> Optional[str]:
    """Parse MM/DD/YYYY → ISO 8601 date string YYYY-MM-DD."""
    if not raw:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ── GWC ID extraction ─────────────────────────────────────────────────────────

def extract_gwc_id(subject: str, body: str) -> Optional[str]:
    """
    Extract GWC ID from email subject line (primary) or body (fallback).
    Pattern: GWC-<digits>
    """
    pattern = r"(GWC-\d+)"
    return _extract(pattern, subject) or _extract(pattern, body)


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_lead_email(subject: str, body: str, message_id: str = "", received_at: str = "") -> dict:
    """
    Parse a HubSpot lead notification email and return a flat dict of all
    fields matching the leads_maturity CSV schema.

    Args:
        subject:    Email subject line
        body:       Plain-text email body
        message_id: Outlook message ID (optional)

    Returns:
        dict with all leads_maturity fields populated where parseable.
        Missing/empty fields are set to "" (not None) to match CSV rules.
    """
    # Strip HTML if the body is an HTML document/fragment (Outlook often returns HTML)
    if _is_html(body):
        body = strip_html(body)

    # Normalise whitespace in body to simplify regex matching
    body_norm = re.sub(r"\r\n", "\n", body)

    # ── Contact & identity ────────────────────────────────────────────────────
    gwc_id      = extract_gwc_id(subject, body_norm) or ""
    contact_name = _extract(r"^Name:\s*(.+)$", body_norm, re.MULTILINE) or ""
    company_name = _extract(r"^Company:\s*(.+)$", body_norm, re.MULTILINE) or ""
    phone        = _extract(r"^Phone:\s*(.+)$", body_norm, re.MULTILINE) or ""
    whatsapp     = _extract(r"^WhatsApp:\s*(.+)$", body_norm, re.MULTILINE) or ""

    # ── Shipment details ──────────────────────────────────────────────────────
    from_country      = _extract(r"^From Country:\s*(.+)$", body_norm, re.MULTILINE) or ""
    to_country        = _extract(r"^To Country:\s*(.+)$", body_norm, re.MULTILINE) or ""
    origin_alt        = _extract(r"^Origin Country\s*\(Alt\):\s*(.+)$", body_norm, re.MULTILINE) or ""
    destination_alt   = _extract(r"^Destination Country\s*\(Alt\):\s*(.+)$", body_norm, re.MULTILINE) or ""
    raw_mot           = _extract(r"^Mode of Freight:\s*(.+)$", body_norm, re.MULTILINE)
    mode_of_freight   = _normalise_mot(raw_mot) or ""
    container_mode    = _normalise_container_mode(raw_mot) or ""
    product           = _extract(r"^Product:\s*(.+)$", body_norm, re.MULTILINE) or ""
    raw_weight        = _extract(r"^Amount\s*\(KG\):\s*(.+)$", body_norm, re.MULTILINE)
    weight_kg         = _parse_weight(raw_weight) or ""
    shipping_req      = _extract(r"^Shipping Requirements:\s*(.+)$", body_norm, re.MULTILINE) or ""

    # ── Additional info ───────────────────────────────────────────────────────
    notes             = _extract(r"^Notes:\s*(.+)$", body_norm, re.MULTILINE) or ""
    raw_create_date   = _extract(r"^Create Date:\s*(.+)$", body_norm, re.MULTILINE)
    hubspot_create_date = _parse_date(raw_create_date) or ""

    # ── Fields the HubSpot template doesn't carry yet (left blank) ────────────
    # These need to be filled either by the sales rep or via form enrichment:
    # container_type, perishable, temperature_details, stackable, dg_class,
    # msds, incoterms, volume_m3, packages, chargeable_weight, dimension_lwh

    now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        # ── Identity
        "gwc_id":                gwc_id,
        "email_message_id":      message_id,
        "contact_name":          contact_name,
        "company_name":          company_name,
        "phone":                 phone,
        "whatsapp":              whatsapp,
        # ── Shipment
        "from_country":          from_country,
        "to_country":            to_country,
        "origin_country_alt":    origin_alt,
        "destination_country_alt": destination_alt,
        "mode_of_freight":       mode_of_freight,
        "container_mode":        container_mode,
        "container_type":        "",   # not in HubSpot template
        "product":               product,
        "perishable":            "",   # not in HubSpot template
        "temperature_details":   "",
        "stackable":             "",
        "dg_class":              "",
        "msds":                  "",
        "incoterms":             "",   # not in HubSpot template
        "weight_kg":             weight_kg,
        "volume_m3":             "",   # not in HubSpot template
        "packages":              "",   # not in HubSpot template
        "chargeable_weight":     "",
        "dimension_lwh":         "",
        "shipping_requirements": shipping_req,
        "notes":                 notes,
        # ── Timestamps
        "hubspot_create_date":   hubspot_create_date,
        "email_received_at":     received_at or now_iso,
        # ── Filled by downstream steps
        "classification":        "",
        "missing_fields":        "",
        "current_status":        "",
        "status_history":        "",
        "assigned_rep_email":    "",
        "assigned_rep_name":     "",
        "assigned_country":      "",
        "first_response_at":     "",
        "quote_sent_at":         "",
        "follow_up_started_at":  "",
        "deal_confirmed_at":     "",
        "deal_outcome":          "",
        "lead_age_days":         "",
        "reminder_history":      "",
        "created_at":            now_iso,
        "updated_at":            now_iso,
        "last_email_scan_at":    now_iso,
        # Populated by gap analysis skill
        "notes_quality_score":   "",
        "extensia_feedback":     "",
    }


# ── Quick test harness ────────────────────────────────────────────────────────

if __name__ == "__main__":
    sample_subject = "[BULK] New Freight Opportunity for GWC-741228136654"
    sample_body = """
New Freight Opportunity has arrived

Name: Ahmed Al-Rashid
Company: Gulf Trade Co.
GWC ID: GWC-741228136654
Phone: +974-5555-1234
WhatsApp: +974-5555-1234

Shipment Details
From Country: China
To Country: Qatar
Origin Country (Alt): N/A
Destination Country (Alt): N/A
Mode of Freight: Ocean LCL
Product: Electronics - Laptops and Accessories
Amount (KG): 1500
Shipping Requirements: N/A

Additional Info
Notes: Urgent shipment needed by end of month
Create Date: 04/09/2026
Date Turned into Opportunity: N/A
"""
    import json
    result = parse_lead_email(sample_subject, sample_body, "MSG-TEST-001")
    print(json.dumps(result, indent=2))
