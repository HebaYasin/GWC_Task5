"""
teams_templates.py
------------------
GWC-branded Microsoft Teams Adaptive Card templates for lead routing notifications.

Returns (title, card_dict) tuples where card_dict is an Adaptive Card JSON object
ready to be sent via MICROSOFT_TEAMS_TEAMS_POST_CHAT_MESSAGE as an attachment.

Replaces the previous email_templates.py (HTML/Outlook).

Usage:
    from teams_templates import (
        build_routing_card_qualified,
        build_routing_card_partial,
        build_routing_card_unroutable,
        card_to_attachment,
    )
    title, card = build_routing_card_qualified(lead, rep_name)
    attachment = card_to_attachment(card)
    # Then post via Teams:
    # MICROSOFT_TEAMS_TEAMS_POST_CHAT_MESSAGE(
    #     chat_id=chat_id,
    #     body={"contentType": "html", "content": f"<attachment id='{attachment['id']}'></attachment>"},
    #     attachments=[attachment]
    # )
"""

import json
import uuid
from datetime import datetime

SHARED_MAILBOX = "Sales.rfq@gwclogistics.com"
CURRENT_YEAR = datetime.utcnow().year

FIELD_LABELS = {
    "incoterms":           "Incoterms",
    "volume_m3":           "Volume (m³)",
    "packages":            "Number of Packages",
    "chargeable_weight":   "Chargeable Weight (kg)",
    "dimension_lwh":       "Dimensions (L × W × H)",
    "stackable":           "Stackable (Y/N)",
    "container_type":      "Container Type",
    "perishable":          "Perishable (Y/N)",
    "temperature_details": "Temperature Requirements",
    "dg_class":            "Dangerous Goods Class (Y/N)",
    "msds":                "MSDS / Safety Data Sheet",
    "from_country":        "Origin Country",
    "to_country":          "Destination Country",
    "mode_of_freight":     "Mode of Freight",
    "product":             "Commodity / Product Description",
    "weight_kg":           "Weight (kg)",
    "pickup_location":     "Pickup Location",
}

UNIVERSAL_REQUIRED_FIELDS = {"incoterms", "packages", "dimension_lwh"}


def _field_label(field_key: str) -> str:
    return FIELD_LABELS.get(field_key, field_key.replace("_", " ").title())


def _lead_facts(lead: dict) -> list:
    """Build a FactSet facts list from lead fields (skips empty values)."""
    fields = [
        ("GWC Lead ID",         lead.get("gwc_id", "")),
        ("Contact Name",        lead.get("contact_name", "")),
        ("Company",             lead.get("company_name", "")),
        ("Phone",               lead.get("phone", "")),
        ("WhatsApp",            lead.get("whatsapp", "")),
        ("Origin Country",      lead.get("from_country", "")),
        ("Destination Country", lead.get("to_country", "")),
        ("Mode of Freight",     lead.get("mode_of_freight", "")),
        ("Container Mode",      lead.get("container_mode", "")),
        ("Commodity",           lead.get("product", "")),
        ("Weight (kg)",         lead.get("weight_kg", "")),
        ("Notes",               lead.get("notes", "")),
        ("HubSpot Create Date", lead.get("hubspot_create_date", "")),
    ]
    return [
        {"title": label, "value": str(value)}
        for label, value in fields
        if str(value).strip()
    ]


def _base_card(header_text: str, header_color: str, body_items: list) -> dict:
    """Wrap body items in a GWC-branded Adaptive Card envelope."""
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": [
            {
                "type": "Container",
                "style": "emphasis",
                "bleed": True,
                "items": [
                    {
                        "type": "TextBlock",
                        "text": "GWC Logistics · Lead Management System",
                        "size": "Small",
                        "color": "Light",
                        "isSubtle": True,
                    },
                    {
                        "type": "TextBlock",
                        "text": header_text,
                        "weight": "Bolder",
                        "size": "Large",
                        "color": header_color,
                        "wrap": True,
                        "spacing": "Small",
                    },
                ],
            },
            *body_items,
            {
                "type": "TextBlock",
                "text": f"© {CURRENT_YEAR} GWC Logistics · Automated notification — do not reply",
                "size": "Small",
                "isSubtle": True,
                "wrap": True,
                "separator": True,
                "spacing": "Medium",
            },
        ],
    }


def card_to_attachment(card: dict) -> dict:
    """
    Wrap an Adaptive Card dict into a Teams message attachment object.
    Use the returned dict in the `attachments` parameter of
    MICROSOFT_TEAMS_TEAMS_POST_CHAT_MESSAGE, and reference the `id` in
    the message body HTML: <attachment id='{attachment["id"]}'></attachment>
    """
    attachment_id = str(uuid.uuid4()).replace("-", "")[:16]
    return {
        "id": attachment_id,
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": json.dumps(card),
    }


# ── Phase 2: Routing notifications ───────────────────────────────────────────

def build_routing_card_qualified(lead: dict, rep_name: str) -> tuple:
    """
    Adaptive Card for a QUALIFIED lead notification.
    Returns (title: str, card: dict).
    """
    gwc_id     = lead.get("gwc_id", "")
    to_country = lead.get("to_country", "")
    first_name = rep_name.split()[0] if rep_name else rep_name

    title = f"🚢 New Qualified Lead — {gwc_id} | {to_country}"

    body_items = [
        {
            "type": "TextBlock",
            "text": (
                f"Hi **{first_name}**, a new **QUALIFIED** freight lead has been assigned to you. "
                "All required shipment details have been provided — please prepare and send a quotation."
            ),
            "wrap": True,
            "spacing": "Medium",
        },
        {
            "type": "TextBlock",
            "text": "📦 Lead Details",
            "weight": "Bolder",
            "spacing": "Medium",
            "separator": True,
        },
        {
            "type": "FactSet",
            "facts": _lead_facts(lead),
        },
        {
            "type": "TextBlock",
            "text": "✅ Action Required",
            "weight": "Bolder",
            "color": "Good",
            "spacing": "Medium",
            "separator": True,
        },
        {
            "type": "TextBlock",
            "text": (
                f"1. Contact the customer and send your freight quotation.\n"
                f"2. Include **{gwc_id}** in the subject line of every email to the customer.\n"
                f"3. CC **{SHARED_MAILBOX}** on all customer correspondence so the system can track progress.\n"
                f"4. The lead status will update automatically once a quotation is detected."
            ),
            "wrap": True,
            "spacing": "Small",
        },
        {
            "type": "TextBlock",
            "text": (
                f"⚠️ Failure to CC {SHARED_MAILBOX} will prevent the system from tracking "
                "this lead's progress and may result in missed follow-ups."
            ),
            "wrap": True,
            "color": "Warning",
            "size": "Small",
            "spacing": "Small",
        },
    ]

    return title, _base_card(title, "Good", body_items)


def build_routing_card_partial(lead: dict, rep_name: str, missing_fields: list) -> tuple:
    """
    Adaptive Card for a PARTIALLY_QUALIFIED lead notification.
    Returns (title: str, card: dict).
    """
    gwc_id     = lead.get("gwc_id", "")
    to_country = lead.get("to_country", "")
    first_name = rep_name.split()[0] if rep_name else rep_name

    title = f"⚠️ Incomplete Lead — {gwc_id} | Missing Info | {to_country}"

    universal    = [f for f in missing_fields if f in UNIVERSAL_REQUIRED_FIELDS]
    mot_specific = [f for f in missing_fields if f not in UNIVERSAL_REQUIRED_FIELDS]

    missing_lines = []
    if universal:
        missing_lines.append("**Required for all shipments:**")
        missing_lines.extend(f"• {_field_label(f)}" for f in universal)
    if mot_specific:
        mot = lead.get("mode_of_freight", "this mode")
        missing_lines.append(f"**Required for {mot} shipments:**")
        missing_lines.extend(f"• {_field_label(f)}" for f in mot_specific)
    missing_text = "\n".join(missing_lines) if missing_lines else "• (none listed)"

    body_items = [
        {
            "type": "TextBlock",
            "text": (
                f"Hi **{first_name}**, a new freight lead has been assigned to you. "
                "It is **missing some required information** needed to prepare a quotation. "
                "Please contact the customer or agency to collect the missing details."
            ),
            "wrap": True,
            "spacing": "Medium",
        },
        {
            "type": "TextBlock",
            "text": "📦 Lead Details",
            "weight": "Bolder",
            "spacing": "Medium",
            "separator": True,
        },
        {
            "type": "FactSet",
            "facts": _lead_facts(lead),
        },
        {
            "type": "TextBlock",
            "text": "⚠️ Missing Information — Please Collect From Customer",
            "weight": "Bolder",
            "color": "Warning",
            "spacing": "Medium",
            "separator": True,
        },
        {
            "type": "TextBlock",
            "text": missing_text,
            "wrap": True,
            "spacing": "Small",
            "color": "Warning",
        },
        {
            "type": "TextBlock",
            "text": "📋 Action Required",
            "weight": "Bolder",
            "color": "Good",
            "spacing": "Medium",
            "separator": True,
        },
        {
            "type": "TextBlock",
            "text": (
                f"1. Contact the customer/agency and request the missing information listed above.\n"
                f"2. Include **{gwc_id}** in the subject line of every email to the customer.\n"
                f"3. CC **{SHARED_MAILBOX}** on all correspondence.\n"
                f"4. Once all information is collected, prepare and send the quotation."
            ),
            "wrap": True,
            "spacing": "Small",
        },
    ]

    return title, _base_card(title, "Warning", body_items)


def build_routing_card_unroutable(lead: dict, manager_email: str, missing_fields: list = None) -> tuple:
    """
    Adaptive Card alert for leads where no rep is found (sent to manager via 1:1 DM).
    Returns (title: str, card: dict).
    """
    if missing_fields is None:
        raw = lead.get("missing_fields", "[]") or "[]"
        try:
            missing_fields = json.loads(raw)
        except Exception:
            missing_fields = []

    gwc_id         = lead.get("gwc_id", "")
    to_country     = lead.get("to_country", "")
    classification = lead.get("classification", "")

    title = f"🔴 Unroutable Lead — {gwc_id} | No Rep for {to_country}"

    body_items = [
        {
            "type": "TextBlock",
            "text": (
                f"A new lead **{gwc_id}** has been ingested but **no sales rep is configured "
                f"for the destination country: {to_country}**. Manual assignment required."
            ),
            "wrap": True,
            "spacing": "Medium",
            "color": "Attention",
        },
        {
            "type": "TextBlock",
            "text": "📦 Lead Details",
            "weight": "Bolder",
            "spacing": "Medium",
            "separator": True,
        },
        {
            "type": "FactSet",
            "facts": _lead_facts(lead),
        },
        {
            "type": "TextBlock",
            "text": "🔴 Next Steps",
            "weight": "Bolder",
            "color": "Attention",
            "spacing": "Medium",
            "separator": True,
        },
        {
            "type": "TextBlock",
            "text": (
                f"1. Identify the appropriate sales rep for destination: **{to_country}**\n"
                f"2. Forward this lead to the correct rep manually\n"
                f"3. Update `country_rep_mapping.csv` with the new country mapping to automate future routing"
            ),
            "wrap": True,
            "spacing": "Small",
        },
    ]

    if missing_fields and (classification == "PARTIALLY_QUALIFIED" or missing_fields):
        missing_text = "\n".join(f"• {_field_label(f)}" for f in missing_fields)
        body_items += [
            {
                "type": "TextBlock",
                "text": "⚠️ Additionally, this lead is missing required fields:",
                "wrap": True,
                "color": "Warning",
                "spacing": "Small",
            },
            {
                "type": "TextBlock",
                "text": missing_text,
                "wrap": True,
                "color": "Warning",
                "spacing": "Small",
            },
        ]

    return title, _base_card(title, "Attention", body_items)
