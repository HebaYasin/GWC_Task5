"""
followup_teams_templates.py
----------------------------
GWC-branded Microsoft Teams Adaptive Card templates for lead follow-up cadence reminders.

Returns (title, card_dict) tuples for use with MICROSOFT_TEAMS_TEAMS_POST_CHAT_MESSAGE.

Three reminder types, one per active status:
  ENGAGED   → "Please send a quotation" (rep hasn't quoted yet)
  QUOTED    → "Please chase the customer" (customer hasn't replied)
  FOLLOW_UP → "Please close this deal" (deal not confirmed after customer replied)

All cards are sent as 1:1 DMs FROM the automation account TO the assigned rep.
Escalation (Day 28 FOLLOW_UP) sends a separate DM to the manager as well.

Replaces followup_email_templates.py (HTML/Outlook).
"""

import json
import uuid
from datetime import datetime

SHARED_MAILBOX = "Sales.rfq@gwclogistics.com"
CURRENT_YEAR = datetime.utcnow().year


def card_to_attachment(card: dict) -> dict:
    """
    Wrap an Adaptive Card dict into a Teams message attachment object.
    Use the returned dict in the `attachments` parameter of
    MICROSOFT_TEAMS_TEAMS_POST_CHAT_MESSAGE.
    Reference the id in body HTML: <attachment id='{attachment["id"]}'></attachment>
    """
    attachment_id = str(uuid.uuid4()).replace("-", "")[:16]
    return {
        "id": attachment_id,
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": json.dumps(card),
    }


def _base_card(header_text: str, sub_text: str, body_items: list, urgency: str = "default") -> dict:
    """
    Wrap body items in a GWC-branded Adaptive Card.
    urgency: "default" | "warning" | "urgent"
    """
    header_color_map = {
        "default": "Accent",
        "warning": "Warning",
        "urgent":  "Attention",
    }
    header_color = header_color_map.get(urgency, "Accent")

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
                        "text": "GWC Logistics · Lead Maturity System",
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
                    {
                        "type": "TextBlock",
                        "text": sub_text,
                        "size": "Small",
                        "color": "Light",
                        "isSubtle": True,
                        "wrap": True,
                        "spacing": "Small",
                    },
                ],
            },
            *body_items,
            {
                "type": "TextBlock",
                "text": f"© {CURRENT_YEAR} GWC Logistics · Automated reminder — do not reply",
                "size": "Small",
                "isSubtle": True,
                "wrap": True,
                "separator": True,
                "spacing": "Medium",
            },
        ],
    }


def _lead_card_block(lead: dict) -> dict:
    """Compact lead summary as a FactSet."""
    route = f"{lead.get('from_country', '?')} → {lead.get('to_country', '?')}"
    mode  = lead.get("mode_of_freight", "") or "—"
    wt    = lead.get("weight_kg", "") or "—"
    try:
        wt_str = f"{float(wt):,.0f} kg" if wt and wt != "—" else "—"
    except (ValueError, TypeError):
        wt_str = str(wt)
    return {
        "type": "FactSet",
        "facts": [
            {"title": "GWC ID",    "value": lead.get("gwc_id", "")},
            {"title": "Customer",  "value": f"{lead.get('contact_name', '')} — {lead.get('company_name', '')}"},
            {"title": "Route",     "value": route},
            {"title": "Mode",      "value": mode},
            {"title": "Weight",    "value": wt_str},
            {"title": "Product",   "value": lead.get("product", "") or "—"},
        ],
    }


def _tracking_reminder_block(gwc_id: str) -> dict:
    return {
        "type": "TextBlock",
        "text": (
            f"📌 **Tracking reminder:** Always CC **{SHARED_MAILBOX}** on every email "
            f"to this customer, and include **{gwc_id}** in the subject line."
        ),
        "wrap": True,
        "size": "Small",
        "color": "Good",
        "spacing": "Small",
    }


# ── Template 0: NO_ACTION → remind rep they haven't replied yet ──────────────

def build_no_reply_reminder(lead: dict, rep_name: str,
                            days_elapsed: int, threshold_day: int) -> tuple:
    """
    Adaptive Card for a NO_ACTION lead where the rep was notified but has not
    yet sent any email to the customer. Fires daily for up to 14 days.
    Returns (title: str, card: dict).
    """
    gwc_id     = lead.get("gwc_id", "")
    first_name = rep_name.split()[0] if rep_name else rep_name
    urgency    = "urgent" if days_elapsed >= 7 else "warning"
    label      = "🔴 No Reply Yet" if days_elapsed >= 7 else "🟡 Action Needed"

    title    = f"[{label}] Please contact the customer — {gwc_id} ({days_elapsed}d, no reply sent)"
    sub_text = (
        f"You were assigned this lead {days_elapsed} day{'s' if days_elapsed != 1 else ''} ago "
        f"but no email has been sent to the customer yet."
    )

    body_items = [
        {
            "type": "TextBlock",
            "text": (
                f"Hi **{first_name}**, you were assigned the following freight lead "
                f"**{days_elapsed} day{'s' if days_elapsed != 1 else ''} ago** but the system "
                "has not detected any outbound email to the customer. "
                "Please reply to the customer today to keep this lead active."
            ),
            "wrap": True,
            "spacing": "Medium",
        },
        {
            "type": "TextBlock",
            "text": "📦 Lead",
            "weight": "Bolder",
            "spacing": "Medium",
            "separator": True,
        },
        _lead_card_block(lead),
        {
            "type": "TextBlock",
            "text": f"{'🔴 Overdue' if days_elapsed >= 7 else '🟡 Pending'} — Day {days_elapsed} with no response",
            "weight": "Bolder",
            "color": "Attention" if days_elapsed >= 7 else "Warning",
            "spacing": "Medium",
            "separator": True,
        },
        {
            "type": "TextBlock",
            "text": (
                "**What to do:**\n"
                "1. Send an introductory email to the customer acknowledging their enquiry\n"
                "2. Request any missing shipment details if needed\n"
                "3. Aim to send a quotation or rate indication as soon as possible\n"
                f"4. CC **{SHARED_MAILBOX}** on all customer emails\n"
                f"5. Include **{gwc_id}** in the subject line"
            ),
            "wrap": True,
            "spacing": "Small",
        },
        _tracking_reminder_block(gwc_id),
        {
            "type": "TextBlock",
            "text": "If this lead should be reassigned or is not relevant, please inform your manager.",
            "wrap": True,
            "size": "Small",
            "isSubtle": True,
            "spacing": "Small",
        },
    ]

    return title, _base_card(title, sub_text, body_items, urgency)


# ── Template 1: ENGAGED → remind rep to send a quotation ─────────────────────

def build_engaged_reminder(lead: dict, rep_name: str,
                           days_elapsed: int, threshold_day: int) -> tuple:
    """
    Adaptive Card for an ENGAGED lead where no quote has been sent.
    Fired at Day 3, 7, 14.
    Returns (title: str, card: dict).
    """
    gwc_id     = lead.get("gwc_id", "")
    first_name = rep_name.split()[0] if rep_name else rep_name
    urgency    = "urgent" if days_elapsed >= 14 else "warning"
    label      = "🔴 Overdue" if days_elapsed >= 14 else "🟡 Reminder"

    title    = f"[{label}] Please send a quote — {gwc_id} ({days_elapsed}d, no proposal sent)"
    sub_text = f"This lead has been engaged for {days_elapsed} day{'s' if days_elapsed != 1 else ''} without a quotation sent."

    body_items = [
        {
            "type": "TextBlock",
            "text": (
                f"Hi **{first_name}**, this is a reminder that the following lead is awaiting "
                f"a **quotation from you**. {days_elapsed} days have passed since you first engaged "
                f"and the customer has not yet received a proposal."
            ),
            "wrap": True,
            "spacing": "Medium",
        },
        {
            "type": "TextBlock",
            "text": "📦 Lead",
            "weight": "Bolder",
            "spacing": "Medium",
            "separator": True,
        },
        _lead_card_block(lead),
        {
            "type": "TextBlock",
            "text": f"{'🔴 Overdue' if days_elapsed >= 14 else '🟡 Action needed'} — Day {days_elapsed}",
            "weight": "Bolder",
            "color": "Attention" if days_elapsed >= 14 else "Warning",
            "spacing": "Medium",
            "separator": True,
        },
        {
            "type": "TextBlock",
            "text": (
                "**What to do:**\n"
                "1. Prepare and send a freight quotation or rate sheet to the customer\n"
                "2. If missing shipment details, request them clearly in one email\n"
                f"3. CC **{SHARED_MAILBOX}** on all customer emails\n"
                f"4. Include **{gwc_id}** in the subject line"
            ),
            "wrap": True,
            "spacing": "Small",
        },
        _tracking_reminder_block(gwc_id),
        {
            "type": "TextBlock",
            "text": "If this lead should be closed or reassigned, please inform your manager.",
            "wrap": True,
            "size": "Small",
            "isSubtle": True,
            "spacing": "Small",
        },
    ]

    return title, _base_card(title, sub_text, body_items, urgency)


# ── Template 2: QUOTED → remind rep to chase the customer ────────────────────

def build_quoted_reminder(lead: dict, rep_name: str,
                          days_elapsed: int, threshold_day: int) -> tuple:
    """
    Adaptive Card for a QUOTED lead with no customer reply.
    Fired at Day 2, 5, 10.
    Returns (title: str, card: dict).
    """
    gwc_id     = lead.get("gwc_id", "")
    first_name = rep_name.split()[0] if rep_name else rep_name
    urgency    = "urgent" if days_elapsed >= 10 else "warning"
    label      = "🔴 Urgent" if days_elapsed >= 10 else "🟡 Chaser"

    title    = f"[{label}] Follow up with customer — {gwc_id} ({days_elapsed}d since quote)"
    sub_text = f"Your quotation was sent {days_elapsed} day{'s' if days_elapsed != 1 else ''} ago — no customer reply yet."

    body_items = [
        {
            "type": "TextBlock",
            "text": (
                f"Hi **{first_name}**, you sent a quotation to the customer below "
                f"but no reply has been received yet. A follow-up message today could make the difference."
            ),
            "wrap": True,
            "spacing": "Medium",
        },
        {
            "type": "TextBlock",
            "text": "📦 Lead",
            "weight": "Bolder",
            "spacing": "Medium",
            "separator": True,
        },
        _lead_card_block(lead),
        {
            "type": "TextBlock",
            "text": f"{'🔴 Urgent' if days_elapsed >= 10 else '🟡 Chaser'} — {days_elapsed} days since quote",
            "weight": "Bolder",
            "color": "Attention" if days_elapsed >= 10 else "Warning",
            "spacing": "Medium",
            "separator": True,
        },
        {
            "type": "TextBlock",
            "text": (
                "**Suggested action:**\n"
                "1. Send a polite follow-up referencing your previous quotation\n"
                "2. Offer to answer any questions or adjust the proposal if needed\n"
                f"3. CC **{SHARED_MAILBOX}** on all customer emails\n"
                f"4. Include **{gwc_id}** in the subject line"
            ),
            "wrap": True,
            "spacing": "Small",
        },
        _tracking_reminder_block(gwc_id),
        {
            "type": "TextBlock",
            "text": "If the customer has already responded via another channel, please update the lead status accordingly.",
            "wrap": True,
            "size": "Small",
            "isSubtle": True,
            "spacing": "Small",
        },
    ]

    return title, _base_card(title, sub_text, body_items, urgency)


# ── Template 3: FOLLOW_UP → remind rep to close the deal ─────────────────────

def build_followup_reminder(lead: dict, rep_name: str,
                            days_elapsed: int, threshold_day: int,
                            is_escalation: bool = False) -> tuple:
    """
    Adaptive Card for a FOLLOW_UP lead that has not been closed.
    Fired at Day 1–7 daily, Day 10, 14, 21, 28 (Day 28 = escalation).
    Returns (title: str, card: dict).
    """
    gwc_id     = lead.get("gwc_id", "")
    first_name = rep_name.split()[0] if rep_name else rep_name
    urgency    = "urgent" if (days_elapsed >= 20 or is_escalation) else "warning"

    if is_escalation:
        label = "🚨 ESCALATION"
    elif days_elapsed >= 20:
        label = "🔴 Critical"
    else:
        label = "🟡 Action needed"

    title    = f"[{label}] Close this deal — {gwc_id} ({days_elapsed}d in follow-up)"
    sub_text = f"This lead has been in FOLLOW_UP for {days_elapsed} day{'s' if days_elapsed != 1 else ''}. Time to close."

    escalation_block = []
    if is_escalation:
        escalation_block = [
            {
                "type": "TextBlock",
                "text": (
                    "🚨 **ESCALATION — Day 28**: This lead has been in follow-up for 28 days "
                    "without resolution. Your manager has been notified. Immediate action required."
                ),
                "wrap": True,
                "color": "Attention",
                "weight": "Bolder",
                "spacing": "Medium",
                "separator": True,
            }
        ]

    body_items = [
        {
            "type": "TextBlock",
            "text": (
                f"Hi **{first_name}**, the customer below has been in follow-up discussions "
                f"for **{days_elapsed} days**. Please take action today to either confirm the deal or close it."
            ),
            "wrap": True,
            "spacing": "Medium",
        },
        {
            "type": "TextBlock",
            "text": "📦 Lead",
            "weight": "Bolder",
            "spacing": "Medium",
            "separator": True,
        },
        _lead_card_block(lead),
        *escalation_block,
        {
            "type": "TextBlock",
            "text": f"{label} — {days_elapsed} days in FOLLOW_UP",
            "weight": "Bolder",
            "color": "Attention" if urgency == "urgent" else "Warning",
            "spacing": "Medium",
            "separator": True,
        },
        {
            "type": "TextBlock",
            "text": (
                "**Action required:**\n"
                "1. Confirm the deal is **WON** (booking confirmed, PO received)\n"
                "2. Or mark as **LOST** if the customer has declined or gone silent\n"
                "3. If still negotiating, send an update so the system can track progress\n"
                f"4. CC **{SHARED_MAILBOX}** on all customer emails\n"
                f"5. Include **{gwc_id}** in the subject line"
            ),
            "wrap": True,
            "spacing": "Small",
        },
        _tracking_reminder_block(gwc_id),
    ]

    return title, _base_card(title, sub_text, body_items, urgency)


def build_escalation_manager_card(lead: dict, rep_name: str, days_elapsed: int) -> tuple:
    """
    Separate Adaptive Card DM sent to the manager on Day 28 FOLLOW_UP escalation.
    Returns (title: str, card: dict).
    """
    gwc_id     = lead.get("gwc_id", "")
    to_country = lead.get("to_country", "")

    title    = f"🚨 Escalation Alert — {gwc_id} | {days_elapsed}d in FOLLOW_UP | {to_country}"
    sub_text = f"Lead {gwc_id} has been in FOLLOW_UP for {days_elapsed} days without closure. Rep: {rep_name}"

    body_items = [
        {
            "type": "TextBlock",
            "text": (
                f"**{gwc_id}** has been in FOLLOW_UP for **{days_elapsed} days** without resolution. "
                f"The assigned rep **{rep_name}** has also been notified. "
                "Manual manager intervention may be required."
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
        _lead_card_block(lead),
        {
            "type": "TextBlock",
            "text": "🔴 Recommended Actions",
            "weight": "Bolder",
            "color": "Attention",
            "spacing": "Medium",
            "separator": True,
        },
        {
            "type": "TextBlock",
            "text": (
                f"1. Contact **{rep_name}** directly to understand the current status\n"
                "2. Decide whether to reassign, close as WON, or mark as LOST\n"
                "3. Consider transitioning to GAP_ANALYSIS if a review is needed"
            ),
            "wrap": True,
            "spacing": "Small",
        },
    ]

    return title, _base_card(title, sub_text, body_items, "urgent")


# ── Dispatcher ────────────────────────────────────────────────────────────────

def build_reminder_card(task: dict) -> tuple:
    """
    Dispatch to the correct Adaptive Card template based on task["status"].

    Args:
        task: reminder task dict from send_followup_reminders.get_reminder_tasks()
              Expected keys: status, lead, rep_name, days_elapsed, threshold_day,
                             is_escalation (bool, optional)

    Returns:
        (title: str, card: dict)
    """
    status        = task["status"]
    lead          = task["lead"]
    rep_name      = task["rep_name"]
    days_elapsed  = task["days_elapsed"]
    threshold_day = task["threshold_day"]
    is_escalation = task.get("is_escalation", False)

    if status == "NO_ACTION":
        return build_no_reply_reminder(lead, rep_name, days_elapsed, threshold_day)
    elif status == "ENGAGED":
        return build_engaged_reminder(lead, rep_name, days_elapsed, threshold_day)
    elif status == "QUOTED":
        return build_quoted_reminder(lead, rep_name, days_elapsed, threshold_day)
    elif status == "FOLLOW_UP":
        return build_followup_reminder(lead, rep_name, days_elapsed, threshold_day, is_escalation)
    else:
        raise ValueError(f"No reminder card template for status: {status!r}")
