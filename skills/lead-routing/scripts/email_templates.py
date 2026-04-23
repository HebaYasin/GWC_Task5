"""
email_templates.py
------------------
GWC-branded HTML email templates for lead routing notifications.

Two templates:
  1. QUALIFIED lead   → "Please quote this lead"
  2. PARTIALLY_QUALIFIED lead → "Please collect missing fields"

Both templates enforce the CC and GWC ID tracking rules from the spec.

Usage:
    from email_templates import build_routing_email
    subject, html_body = build_routing_email(lead, rep_name, classification)
"""

from datetime import datetime

# ── GWC brand constants (from gwc-branding SKILL.md) ─────────────────────────
GWC_GREEN  = "#3FAE2A"
GWC_BLUE   = "#00ABC7"
GWC_DARK   = "#555555"
GWC_LIGHT  = "#F5F5F5"
GWC_BORDER = "#B5B5B5"
CURRENT_YEAR = datetime.utcnow().year

# Human-readable field labels for missing_fields list
FIELD_LABELS = {
    "incoterms":         "Incoterms",
    "volume_m3":         "Volume (m³)",
    "packages":          "Number of Packages",
    "chargeable_weight": "Chargeable Weight (kg)",
    "dimension_lwh":     "Dimensions (L × W × H)",
    "stackable":         "Stackable (Y/N)",
    "container_type":    "Container Type",
    "perishable":        "Perishable (Y/N)",
    "temperature_details": "Temperature Requirements",
    "dg_class":          "Dangerous Goods Class (Y/N)",
    "msds":              "MSDS / Safety Data Sheet",
    "from_country":      "Origin Country",
    "to_country":        "Destination Country",
    "mode_of_freight":   "Mode of Freight",
    "product":           "Commodity / Product Description",
    "weight_kg":         "Weight (kg)",
    "pickup_location":   "Pickup Location",
}

SHARED_MAILBOX = "Sales.rfq@gwclogistics.com"

# Fields required for ALL shipments regardless of MOT (mirrors classify_lead.py)
UNIVERSAL_REQUIRED_FIELDS = {"incoterms", "packages", "dimension_lwh"}


def _field_label(field_key: str) -> str:
    return FIELD_LABELS.get(field_key, field_key.replace("_", " ").title())


def _html_base(content: str, title: str) -> str:
    """Wrap content in GWC-branded HTML email shell."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <link rel="stylesheet" href="https://use.typekit.net/wtc0foh.css">
</head>
<body style="margin:0;padding:0;background-color:{GWC_LIGHT};font-family:'proxima-nova','Proxima Nova',Arial,Helvetica,sans-serif;font-size:15px;color:{GWC_DARK};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:{GWC_LIGHT};">
    <tr><td align="center" style="padding:24px 12px;">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:6px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">

        <!-- Header bar -->
        <tr>
          <td style="background-color:{GWC_GREEN};padding:20px 28px;">
            <span style="color:#ffffff;font-size:20px;font-weight:700;letter-spacing:0.5px;">GWC Logistics</span>
            <span style="color:rgba(255,255,255,0.75);font-size:12px;display:block;margin-top:2px;">DELIVERING LOGISTICS INNOVATION</span>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:28px 28px 12px;">
            {content}
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:16px 28px 24px;border-top:1px solid {GWC_BORDER};margin-top:20px;">
            <p style="font-size:11px;color:{GWC_BORDER};margin:0;">
              © 2004–{CURRENT_YEAR} GWC. All Rights Reserved. &nbsp;|&nbsp;
              <a href="https://www.gwclogistics.com/privacy-policy/" style="color:{GWC_BLUE};text-decoration:none;">Privacy Policy</a>
            </p>
            <p style="font-size:11px;color:{GWC_BORDER};margin:6px 0 0;">
              This is an automated message from the GWC Lead Management System. Do not reply to this email.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _lead_details_table(lead: dict) -> str:
    """Render a GWC-branded table of lead shipment details."""
    rows = [
        ("GWC Lead ID",       lead.get("gwc_id", "")),
        ("Contact Name",      lead.get("contact_name", "")),
        ("Company",           lead.get("company_name", "")),
        ("Phone",             lead.get("phone", "")),
        ("WhatsApp",          lead.get("whatsapp", "")),
        ("Origin Country",    lead.get("from_country", "")),
        ("Destination",       lead.get("to_country", "")),
        ("Mode of Freight",   lead.get("mode_of_freight", "")),
        ("Container Mode",    lead.get("container_mode", "")),
        ("Commodity",         lead.get("product", "")),
        ("Weight (kg)",       lead.get("weight_kg", "")),
        ("Notes",             lead.get("notes", "")),
        ("HubSpot Create Date", lead.get("hubspot_create_date", "")),
    ]
    trs = ""
    for i, (label, value) in enumerate(rows):
        if not str(value).strip():
            continue
        bg = "#ffffff" if i % 2 == 0 else GWC_LIGHT
        trs += f"""
        <tr style="background-color:{bg};">
          <td style="padding:8px 12px;font-weight:600;color:{GWC_DARK};width:45%;border-bottom:1px solid {GWC_BORDER};">{label}</td>
          <td style="padding:8px 12px;color:{GWC_DARK};border-bottom:1px solid {GWC_BORDER};">{value}</td>
        </tr>"""
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="border:1px solid {GWC_BORDER};border-radius:4px;border-collapse:collapse;margin:16px 0;">
      {trs}
    </table>"""


def build_routing_email_qualified(lead: dict, rep_name: str) -> tuple[str, str]:
    """
    Build routing email for a QUALIFIED lead.
    Returns (subject, html_body).
    """
    gwc_id = lead.get("gwc_id", "")
    to_country = lead.get("to_country", "")

    subject = f"🚢 New Qualified Lead — {gwc_id} | {to_country}"

    content = f"""
    <h2 style="margin:0 0 6px;font-size:22px;font-weight:700;color:{GWC_GREEN};">New Qualified Lead Assigned to You</h2>
    <p style="margin:0 0 18px;font-size:13px;color:{GWC_BLUE};font-weight:600;text-transform:uppercase;letter-spacing:0.5px;">Action Required: Please prepare and send a quotation</p>

    <p style="margin:0 0 14px;">Dear {rep_name},</p>
    <p style="margin:0 0 14px;">A new freight lead has been received and assigned to you. All required shipment details have been provided — please send a quotation to the customer as soon as possible.</p>

    {_lead_details_table(lead)}

    <div style="background-color:#E8F5E6;border-left:4px solid {GWC_GREEN};padding:14px 16px;border-radius:4px;margin:20px 0;">
      <p style="margin:0 0 10px;font-weight:700;color:{GWC_DARK};">📋 Action Instructions</p>
      <ol style="margin:0;padding-left:20px;color:{GWC_DARK};line-height:1.8;">
        <li>Contact the customer and send your freight quotation.</li>
        <li><strong>Include <span style="color:{GWC_GREEN};">{gwc_id}</span> in the subject line of every email you send to the customer.</strong></li>
        <li><strong>CC <a href="mailto:{SHARED_MAILBOX}" style="color:{GWC_BLUE};">{SHARED_MAILBOX}</a> on all correspondence</strong> with this customer so our system can track progress.</li>
        <li>Once you have sent the quotation, the lead status will automatically update.</li>
      </ol>
    </div>

    <p style="margin:14px 0 0;font-size:13px;color:{GWC_BORDER};">
      ⚠ Failure to CC the shared mailbox will prevent the system from tracking this lead's progress and may result in missed follow-ups.
    </p>"""

    return subject, _html_base(content, subject)


def _missing_fields_block(missing_fields: list, mot: str = "") -> str:
    """
    Render the yellow missing-fields warning block, splitting fields into:
      • Universal (required for all shipments)
      • MOT-specific (required for the declared mode of transport)

    Args:
        missing_fields: list of field key strings
        mot: mode_of_freight string (e.g. "Air", "Sea", "Overland") — used for label only
    """
    if not missing_fields:
        return ""

    universal = [f for f in missing_fields if f in UNIVERSAL_REQUIRED_FIELDS]
    mot_specific = [f for f in missing_fields if f not in UNIVERSAL_REQUIRED_FIELDS]

    mot_label = f"{mot} shipments" if mot and mot.strip() else "this shipment type"

    def _items(fields):
        return "".join(
            f'<li style="margin-bottom:6px;"><strong style="color:{GWC_DARK};">{_field_label(f)}</strong></li>'
            for f in fields
        )

    universal_section = ""
    if universal:
        universal_section = f"""
      <p style="margin:10px 0 4px;font-size:13px;font-weight:700;color:{GWC_DARK};">
        Required for all shipments:
      </p>
      <ul style="margin:0 0 6px;padding-left:20px;color:{GWC_DARK};line-height:1.8;">
        {_items(universal)}
      </ul>"""

    mot_section = ""
    if mot_specific:
        mot_section = f"""
      <p style="margin:10px 0 4px;font-size:13px;font-weight:700;color:{GWC_DARK};">
        Required for {mot_label}:
      </p>
      <ul style="margin:0;padding-left:20px;color:{GWC_DARK};line-height:1.8;">
        {_items(mot_specific)}
      </ul>"""

    return f"""
    <div style="background-color:#FFF3CD;border-left:4px solid #E67E22;padding:14px 16px;border-radius:4px;margin:20px 0;">
      <p style="margin:0 0 6px;font-weight:700;color:{GWC_DARK};">⚠ Missing Information — Please Collect From Customer</p>
      {universal_section}
      {mot_section}
    </div>"""


def build_routing_email_partial(lead: dict, rep_name: str, missing_fields: list) -> tuple[str, str]:
    """
    Build routing email for a PARTIALLY_QUALIFIED lead.
    Missing fields are split into universal vs MOT-specific sections.
    Returns (subject, html_body).
    """
    gwc_id = lead.get("gwc_id", "")
    to_country = lead.get("to_country", "")
    mot = lead.get("mode_of_freight", "")

    subject = f"⚠️ Incomplete Lead — {gwc_id} | Missing Info Required | {to_country}"

    content = f"""
    <h2 style="margin:0 0 6px;font-size:22px;font-weight:700;color:#E67E22;">Incomplete Lead — Missing Information</h2>
    <p style="margin:0 0 18px;font-size:13px;color:{GWC_BLUE};font-weight:600;text-transform:uppercase;letter-spacing:0.5px;">Action Required: Collect missing details from customer/agency</p>

    <p style="margin:0 0 14px;">Dear {rep_name},</p>
    <p style="margin:0 0 14px;">A new freight lead has been received and assigned to you. However, it is <strong>missing some required information</strong> needed to prepare a quotation. Please contact the customer or agency to collect the missing details below.</p>

    {_lead_details_table(lead)}

    {_missing_fields_block(missing_fields, mot)}

    <div style="background-color:#E8F5E6;border-left:4px solid {GWC_GREEN};padding:14px 16px;border-radius:4px;margin:20px 0;">
      <p style="margin:0 0 10px;font-weight:700;color:{GWC_DARK};">📋 Action Instructions</p>
      <ol style="margin:0;padding-left:20px;color:{GWC_DARK};line-height:1.8;">
        <li>Contact the customer/agency and request the missing information listed above.</li>
        <li><strong>Include <span style="color:{GWC_GREEN};">{gwc_id}</span> in the subject line of every email you send to the customer.</strong></li>
        <li><strong>CC <a href="mailto:{SHARED_MAILBOX}" style="color:{GWC_BLUE};">{SHARED_MAILBOX}</a> on all correspondence</strong> with this customer so our system can track progress.</li>
        <li>Once all information is collected, prepare and send the quotation.</li>
      </ol>
    </div>

    <p style="margin:14px 0 0;font-size:13px;color:{GWC_BORDER};">
      ⚠ Failure to CC the shared mailbox will prevent the system from tracking this lead's progress and may result in missed follow-ups.
    </p>"""

    return subject, _html_base(content, subject)


def build_routing_email_unroutable(lead: dict, manager_email: str, missing_fields: list = None) -> tuple[str, str]:
    """
    Build alert email for leads where no rep is found for the destination country.
    Includes the missing fields section (if any) so the manager knows what info
    to collect when manually assigning and forwarding to a rep.
    Returns (subject, html_body) for the manager/admin.
    """
    gwc_id = lead.get("gwc_id", "")
    to_country = lead.get("to_country", "")
    mot = lead.get("mode_of_freight", "")
    classification = lead.get("classification", "")

    if missing_fields is None:
        # Graceful fallback: try to parse from lead record if caller forgot to pass
        import json as _json
        raw = lead.get("missing_fields", "[]") or "[]"
        try:
            missing_fields = _json.loads(raw)
        except Exception:
            missing_fields = []

    subject = f"🔴 Unroutable Lead — {gwc_id} | No Rep for {to_country}"

    # Show missing fields block only for PARTIALLY_QUALIFIED leads
    missing_section = ""
    if classification == "PARTIALLY_QUALIFIED" and missing_fields:
        missing_section = _missing_fields_block(missing_fields, mot)
    elif missing_fields:
        # QUALIFIED but unroutable — still show fields for completeness context
        missing_section = _missing_fields_block(missing_fields, mot)

    content = f"""
    <h2 style="margin:0 0 6px;font-size:22px;font-weight:700;color:#C0392B;">Unroutable Lead — Manual Assignment Required</h2>
    <p style="margin:0 0 18px;font-size:13px;color:{GWC_BLUE};font-weight:600;text-transform:uppercase;letter-spacing:0.5px;">Action Required: Assign this lead manually</p>

    <p style="margin:0 0 14px;">A new lead ({gwc_id}) has been ingested but <strong>no sales rep is configured for the destination country: <span style="color:#C0392B;">{to_country}</span></strong>.</p>
    <p style="margin:0 0 14px;">Please manually assign this lead to the appropriate rep and update the country_rep_mapping.csv to prevent this in future.</p>

    {_lead_details_table(lead)}

    {missing_section}

    <div style="background-color:#FDEDEC;border-left:4px solid #C0392B;padding:14px 16px;border-radius:4px;margin:20px 0;">
      <p style="margin:0;font-weight:700;color:{GWC_DARK};">Next Steps</p>
      <ol style="margin:8px 0 0;padding-left:20px;color:{GWC_DARK};line-height:1.8;">
        <li>Identify the appropriate sales rep for destination: <strong>{to_country}</strong></li>
        <li>Forward this lead to the correct rep manually</li>
        <li>If the lead is incomplete, ask the rep to collect the missing information listed above before quoting</li>
        <li>Add the country mapping to <code>country_rep_mapping.csv</code> to automate future routing</li>
      </ol>
    </div>"""

    return subject, _html_base(content, subject)


def build_plain_text_fallback(lead: dict, classification: str, missing_fields: list, rep_name: str) -> str:
    """Plain text fallback for email clients that don't render HTML."""
    gwc_id = lead.get("gwc_id", "")
    lines = [
        f"GWC Lead Management System",
        f"{'=' * 40}",
        f"",
        f"Dear {rep_name},",
        f"",
    ]
    if classification == "QUALIFIED":
        lines += [
            f"A new QUALIFIED lead has been assigned to you. Please send a quotation.",
            f"",
        ]
    else:
        lines += [
            f"A new lead has been assigned to you. It is missing required information.",
            f"Please collect the following from the customer:",
            "",
        ] + [f"  - {_field_label(f)}" for f in missing_fields] + [""]

    lines += [
        f"Lead Details:",
        f"  GWC ID:         {gwc_id}",
        f"  Company:        {lead.get('company_name', '')}",
        f"  Contact:        {lead.get('contact_name', '')}",
        f"  Phone:          {lead.get('phone', '')}",
        f"  Origin:         {lead.get('from_country', '')}",
        f"  Destination:    {lead.get('to_country', '')}",
        f"  Mode:           {lead.get('mode_of_freight', '')}",
        f"  Commodity:      {lead.get('product', '')}",
        f"  Weight:         {lead.get('weight_kg', '')} kg",
        f"",
        f"IMPORTANT:",
        f"  1. Include {gwc_id} in ALL email subject lines to the customer.",
        f"  2. CC {SHARED_MAILBOX} on ALL correspondence.",
        f"",
        f"© 2004-{CURRENT_YEAR} GWC. All Rights Reserved.",
    ]
    return "\n".join(lines)
