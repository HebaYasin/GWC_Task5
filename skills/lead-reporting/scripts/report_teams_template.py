"""
report_teams_template.py
-------------------------
GWC-branded Microsoft Teams Adaptive Card for the weekly lead pipeline report.

Returns (title, card_dict) for use with MICROSOFT_TEAMS_TEAMS_POST_CHAT_MESSAGE.
Sent as a 1:1 DM to the manager (hebah.yasin@gwclogistics.com).

Replaces report_email_template.py (HTML/Outlook).

Usage:
    from report_teams_template import build_report_card, card_to_attachment
    title, card = build_report_card(data)
    attachment  = card_to_attachment(card)
"""

import json
import uuid
from datetime import datetime

CURRENT_YEAR = datetime.utcnow().year

STATUS_EMOJI = {
    "NO_ACTION":  "⬜",
    "ENGAGED":    "🟢",
    "QUOTED":     "🟡",
    "FOLLOW_UP":  "🟣",
    "WON_LOSS":   "🔵",
    "REJECTED":   "⚫",
}


def card_to_attachment(card: dict) -> dict:
    """
    Wrap an Adaptive Card dict into a Teams message attachment object.
    Reference the id in body HTML: <attachment id='{attachment["id"]}'></attachment>
    """
    attachment_id = str(uuid.uuid4()).replace("-", "")[:16]
    return {
        "id": attachment_id,
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": json.dumps(card),
    }


def build_report_card(data: dict) -> tuple:
    """
    Build the weekly report Adaptive Card.

    Args:
        data: output of report_builder.build_report(store)

    Returns:
        (title: str, card: dict)
    """
    m        = data["meta"]
    funnel   = data["funnel"]
    by_rep   = data["by_rep"]
    by_mode  = data["by_mode"]
    dark     = data["dark_leads"]
    stale    = data["stale_leads"]
    activity = data["recent_activity"]

    active_count = sum(
        data["status_counts"].get(s, 0)
        for s in ("ENGAGED", "QUOTED", "FOLLOW_UP")
    )

    title = (
        f"📊 GWC Weekly Lead Report — Week of {m['week_end']} | "
        f"{m['total_leads']} leads, {data['new_this_period']} new"
    )

    # ── Key metrics row ───────────────────────────────────────────────────────
    metrics_columns = {
        "type": "ColumnSet",
        "spacing": "Medium",
        "columns": [
            {
                "type": "Column",
                "width": "stretch",
                "items": [
                    {"type": "TextBlock", "text": str(m["total_leads"]),
                     "size": "ExtraLarge", "weight": "Bolder", "color": "Accent"},
                    {"type": "TextBlock", "text": "Total Leads",
                     "size": "Small", "isSubtle": True},
                ],
            },
            {
                "type": "Column",
                "width": "stretch",
                "items": [
                    {"type": "TextBlock", "text": str(data["new_this_period"]),
                     "size": "ExtraLarge", "weight": "Bolder", "color": "Good"},
                    {"type": "TextBlock", "text": "New This Week",
                     "size": "Small", "isSubtle": True},
                ],
            },
            {
                "type": "Column",
                "width": "stretch",
                "items": [
                    {"type": "TextBlock", "text": str(active_count),
                     "size": "ExtraLarge", "weight": "Bolder", "color": "Warning"},
                    {"type": "TextBlock", "text": "Active Pipeline",
                     "size": "Small", "isSubtle": True},
                ],
            },
            {
                "type": "Column",
                "width": "stretch",
                "items": [
                    {"type": "TextBlock", "text": str(data["won"]),
                     "size": "ExtraLarge", "weight": "Bolder", "color": "Accent"},
                    {"type": "TextBlock", "text": "Won Deals",
                     "size": "Small", "isSubtle": True},
                ],
            },
        ],
    }

    # ── Pipeline funnel ───────────────────────────────────────────────────────
    funnel_facts = []
    for f in funnel:
        emoji = STATUS_EMOJI.get(f["status"], "•")
        funnel_facts.append({
            "title": f"{emoji} {f['status']}",
            "value": str(f["count"]),
        })
    if data.get("rejected_count"):
        funnel_facts.append({
            "title": "⚫ REJECTED",
            "value": f"{data['rejected_count']} (not in funnel)",
        })

    funnel_block = {
        "type": "Container",
        "spacing": "Medium",
        "items": [
            {"type": "TextBlock", "text": "Pipeline Funnel",
             "weight": "Bolder", "separator": True, "spacing": "Medium"},
            {"type": "FactSet", "facts": funnel_facts},
        ],
    }

    # ── Alerts ────────────────────────────────────────────────────────────────
    alerts_items = [
        {"type": "TextBlock", "text": "Alerts",
         "weight": "Bolder", "separator": True, "spacing": "Medium"},
    ]
    if dark:
        dark_list = ", ".join(
            f"{d['gwc_id']} ({d.get('company', '')} · {d['days_silent']}d silent)"
            for d in dark
        )
        alerts_items.append({
            "type": "TextBlock",
            "text": f"🔴 **{len(dark)} dark lead(s):** {dark_list}",
            "wrap": True,
            "color": "Attention",
        })
    if stale:
        stale_list = ", ".join(
            f"{s['gwc_id']} ({s['status']} · {s['days_in_status']}d)"
            for s in stale
        )
        alerts_items.append({
            "type": "TextBlock",
            "text": f"🟡 **{len(stale)} stale lead(s):** {stale_list}",
            "wrap": True,
            "color": "Warning",
        })
    if not dark and not stale:
        alerts_items.append({
            "type": "TextBlock",
            "text": "✅ No dark or stale leads.",
            "color": "Good",
        })
    alerts_block = {"type": "Container", "spacing": "Medium", "items": alerts_items}

    # ── Rep performance ───────────────────────────────────────────────────────
    rep_facts = []
    for r in by_rep:
        name = r["rep_name"] or r["rep_email"]
        rep_facts.append({
            "title": name,
            "value": (
                f"Total: {r['total']} | "
                f"Engaged: {r['engaged']} | "
                f"Quoted: {r['quoted']} | "
                f"Follow-Up: {r['follow_up']} | "
                f"Won: {r['won']}"
            ),
        })
    rep_block = {
        "type": "Container",
        "spacing": "Medium",
        "items": [
            {"type": "TextBlock", "text": "Performance by Rep",
             "weight": "Bolder", "separator": True, "spacing": "Medium"},
            {
                "type": "FactSet",
                "facts": rep_facts if rep_facts else [{"title": "—", "value": "No reps assigned yet."}],
            },
        ],
    }

    # ── Mode of freight ───────────────────────────────────────────────────────
    mode_facts = [
        {"title": m2["mode"] or "Unknown", "value": str(m2["count"])}
        for m2 in by_mode
    ]
    mode_block = {
        "type": "Container",
        "spacing": "Medium",
        "items": [
            {"type": "TextBlock", "text": "Leads by Mode of Freight",
             "weight": "Bolder", "separator": True, "spacing": "Medium"},
            {
                "type": "FactSet",
                "facts": mode_facts if mode_facts else [{"title": "—", "value": "No data."}],
            },
        ],
    }

    # ── Recent activity ───────────────────────────────────────────────────────
    act_facts = []
    for a in activity[:10]:
        ts_short = a["ts"][:16].replace("T", " ") if a.get("ts") else "—"
        act_facts.append({
            "title": f"{ts_short} · {a['gwc_id']}",
            "value": a["type"],
        })
    activity_block = {
        "type": "Container",
        "spacing": "Medium",
        "items": [
            {"type": "TextBlock",
             "text": f"Recent Activity (last {m['period_days']} days)",
             "weight": "Bolder", "separator": True, "spacing": "Medium"},
            {
                "type": "FactSet",
                "facts": act_facts if act_facts else [{"title": "—", "value": "No activity this week."}],
            },
        ],
    }

    # ── Assemble card ─────────────────────────────────────────────────────────
    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": [
            # Header
            {
                "type": "Container",
                "style": "emphasis",
                "bleed": True,
                "items": [
                    {"type": "TextBlock",
                     "text": "GWC Logistics · Lead Maturity System",
                     "size": "Small", "color": "Light", "isSubtle": True},
                    {"type": "TextBlock",
                     "text": "📊 Weekly Lead Pipeline Report",
                     "weight": "Bolder", "size": "Large", "color": "Accent",
                     "wrap": True, "spacing": "Small"},
                    {"type": "TextBlock",
                     "text": f"{m['week_start']} – {m['week_end']} · Generated {m['generated_at']}",
                     "size": "Small", "color": "Light", "isSubtle": True,
                     "wrap": True, "spacing": "Small"},
                ],
            },
            # Metrics
            {
                "type": "Container",
                "spacing": "Medium",
                "items": [
                    {"type": "TextBlock", "text": "Summary",
                     "weight": "Bolder", "separator": True, "spacing": "Medium"},
                    metrics_columns,
                ],
            },
            funnel_block,
            alerts_block,
            rep_block,
            mode_block,
            activity_block,
            # Footer
            {
                "type": "TextBlock",
                "text": f"© {CURRENT_YEAR} GWC Logistics · Automated weekly report — do not reply",
                "size": "Small",
                "isSubtle": True,
                "wrap": True,
                "separator": True,
                "spacing": "Medium",
            },
        ],
    }

    return title, card
