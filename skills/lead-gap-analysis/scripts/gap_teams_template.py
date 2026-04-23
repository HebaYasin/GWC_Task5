"""
gap_teams_template.py
----------------------
GWC-branded Microsoft Teams Adaptive Card for the monthly gap analysis report.

Returns (title, card_dict) for use with MICROSOFT_TEAMS_TEAMS_POST_CHAT_MESSAGE.
Sent as a 1:1 DM to the manager (hebah.yasin@gwclogistics.com).

Replaces gap_report_template.py (HTML/Outlook).

Usage:
    from gap_teams_template import build_gap_card, card_to_attachment
    title, card = build_gap_card(gaps, extensia)
    attachment  = card_to_attachment(card)
"""

import json
import uuid
from datetime import datetime

CURRENT_YEAR = datetime.utcnow().year


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


def _gap_facts(rows: list, key_map: list) -> list:
    """
    Convert a list of gap row dicts into FactSet facts.
    key_map: list of (field_key, label) tuples defining which fields to include per row.
    Returns one fact per row, combining all fields into the value string.
    """
    facts = []
    for r in rows:
        parts = []
        for key, label in key_map:
            val = r.get(key, "—")
            if val and str(val).strip() and str(val) != "—":
                parts.append(f"{label}: {val}")
        facts.append({
            "title": r.get("gwc_id", "—"),
            "value": " | ".join(parts) if parts else "—",
        })
    return facts


def _section(title: str, count: int, description: str, facts: list,
             color: str = "Warning") -> dict:
    """Build a collapsible gap section container."""
    if not facts:
        items = [
            {"type": "TextBlock", "text": "✅ No issues found.",
             "color": "Good", "spacing": "Small"},
        ]
    else:
        items = [
            {"type": "TextBlock", "text": description,
             "wrap": True, "size": "Small", "isSubtle": True, "spacing": "Small"},
            {"type": "FactSet", "facts": facts},
        ]

    return {
        "type": "Container",
        "spacing": "Medium",
        "items": [
            {
                "type": "TextBlock",
                "text": f"{title} ({count})",
                "weight": "Bolder",
                "color": color if count > 0 else "Good",
                "separator": True,
                "spacing": "Medium",
            },
            *items,
        ],
    }


def build_gap_card(gaps: dict, extensia: dict = None) -> tuple:
    """
    Build the monthly gap analysis Adaptive Card.

    Args:
        gaps:     output of gap_detector.detect_gaps(store)
        extensia: output of gap_detector.analyze_extensia_quality(store) — optional

    Returns:
        (title: str, card: dict)
    """
    now_str  = datetime.utcnow().strftime("%B %Y")
    total    = gaps["total_gaps"]
    counts   = gaps["summary_counts"]

    if total > 3:
        severity = "🔴 Action Required"
    elif total > 0:
        severity = "🟡 Attention Needed"
    else:
        severity = "✅ All Clear"

    title = f"🔍 GWC Gap Analysis — {now_str} | {total} gap item(s) | {severity}"

    # ── Scorecard (2 rows of 3) ───────────────────────────────────────────────
    stale_total = (
        counts.get("stale_engaged", 0) +
        counts.get("stale_quoted", 0) +
        counts.get("stale_follow_up", 0)
    )

    def _metric_col(label: str, value: int, good_when_zero: bool = True) -> dict:
        color = ("Good" if value == 0 else "Attention") if good_when_zero else "Accent"
        return {
            "type": "Column",
            "width": "stretch",
            "items": [
                {"type": "TextBlock", "text": str(value),
                 "size": "ExtraLarge", "weight": "Bolder", "color": color},
                {"type": "TextBlock", "text": label,
                 "size": "Small", "isSubtle": True, "wrap": True},
            ],
        }

    scorecard_row1 = {
        "type": "ColumnSet",
        "spacing": "Medium",
        "columns": [
            _metric_col("Unroutable",   counts.get("unroutable", 0)),
            _metric_col("Dark Leads",   counts.get("dark_leads", 0)),
            _metric_col("Stale Leads",  stale_total),
        ],
    }
    scorecard_row2 = {
        "type": "ColumnSet",
        "columns": [
            _metric_col("Missing Fields", counts.get("missing_fields", 0)),
            _metric_col("Aged 30d+",      counts.get("long_age", 0)),
            {
                "type": "Column",
                "width": "stretch",
                "items": [
                    {"type": "TextBlock",
                     "text": f"{gaps.get('rejection_rate_pct', 0)}%",
                     "size": "ExtraLarge", "weight": "Bolder",
                     "color": "Attention" if gaps.get("high_rejection") else "Good"},
                    {"type": "TextBlock", "text": "Rejection Rate",
                     "size": "Small", "isSubtle": True},
                ],
            },
        ],
    }

    # ── Gap sections ──────────────────────────────────────────────────────────
    unroutable_section = _section(
        "⚠️ Unroutable Leads",
        counts.get("unroutable", 0),
        "No rep mapped for the destination country. Manual assignment required.",
        _gap_facts(gaps.get("unroutable", []),
                   [("company", "Co"), ("route", "Route"),
                    ("age_days", "Age"), ("notes", "Notes")]),
        color="Attention",
    )

    stale_engaged_section = _section(
        "🕐 Stale ENGAGED Leads",
        counts.get("stale_engaged", 0),
        "Rep engaged but no quote sent after 14+ days.",
        _gap_facts(gaps.get("stale_engaged", []),
                   [("company", "Co"), ("route", "Route"),
                    ("rep", "Rep"), ("days", "Days")]),
    )

    stale_quoted_section = _section(
        "🕐 Stale QUOTED Leads",
        counts.get("stale_quoted", 0),
        "Quote sent but no customer response after 10+ days.",
        _gap_facts(gaps.get("stale_quoted", []),
                   [("company", "Co"), ("route", "Route"),
                    ("rep", "Rep"), ("days", "Days")]),
    )

    stale_followup_section = _section(
        "🕐 Stale FOLLOW_UP Leads",
        counts.get("stale_follow_up", 0),
        "Deal not closed after 20+ days in follow-up.",
        _gap_facts(gaps.get("stale_follow_up", []),
                   [("company", "Co"), ("route", "Route"),
                    ("rep", "Rep"), ("days", "Days")]),
    )

    missing_fields_section = _section(
        "📋 Missing Required Fields",
        counts.get("missing_fields", 0),
        "Active leads that cannot be fully quoted due to missing data.",
        _gap_facts(
            [
                {**r, "missing": ", ".join(r.get("missing", []))}
                for r in gaps.get("missing_fields", [])
            ],
            [("company", "Co"), ("status", "Status"),
             ("rep", "Rep"), ("missing", "Missing")],
        ),
    )

    dark_section = _section(
        "🔕 Dark Leads",
        counts.get("dark_leads", 0),
        "No email thread activity detected for 5+ days on active leads.",
        _gap_facts(gaps.get("dark_leads", []),
                   [("company", "Co"), ("status", "Status"),
                    ("rep", "Rep"), ("days_silent", "Silent")]),
        color="Attention",
    )

    long_age_section = _section(
        "📅 Aged Leads (30d+)",
        counts.get("long_age", 0),
        "Leads in the pipeline for over 30 days without closure.",
        _gap_facts(gaps.get("long_age", []),
                   [("company", "Co"), ("status", "Status"),
                    ("rep", "Rep"), ("age", "Age")]),
    )

    # ── High rejection rate ───────────────────────────────────────────────────
    rej_items = []
    if gaps.get("high_rejection"):
        rej_pct = gaps.get("rejection_rate_pct", 0)
        rej_cnt = gaps.get("rejected_count", 0)
        total_l = gaps.get("total_leads", 0)
        rej_items = [
            {
                "type": "Container",
                "spacing": "Medium",
                "items": [
                    {
                        "type": "TextBlock",
                        "text": f"🚫 High Rejection Rate — {rej_pct}% ({rej_cnt} of {total_l} leads)",
                        "weight": "Bolder",
                        "color": "Attention",
                        "separator": True,
                        "spacing": "Medium",
                    },
                    {
                        "type": "TextBlock",
                        "text": (
                            f"Rejection rate of **{rej_pct}%** exceeds the 30% threshold. "
                            "Review the HubSpot lead qualification form — too many leads are "
                            "arriving with insufficient data."
                        ),
                        "wrap": True,
                        "color": "Attention",
                        "spacing": "Small",
                    },
                ],
            }
        ]

    # ── Extensia quality section ──────────────────────────────────────────────
    extensia_items = []
    if extensia:
        avg_pct      = extensia.get("avg_completeness_pct", 0)
        poor_samples = extensia.get("poor_notes_samples", [])
        top_missing  = extensia.get("most_missing_fields", [])[:5]
        scored_count = extensia.get("scored_count", 0)

        completeness_color = "Good" if avg_pct >= 80 else ("Warning" if avg_pct >= 60 else "Attention")
        missing_text = ", ".join(top_missing) if top_missing else "None"
        poor_ids     = ", ".join(p["gwc_id"] for p in poor_samples[:5]) if poor_samples else "None"

        extensia_items = [
            {
                "type": "Container",
                "spacing": "Medium",
                "items": [
                    {
                        "type": "TextBlock",
                        "text": "📊 Extensia Submission Quality",
                        "weight": "Bolder",
                        "separator": True,
                        "spacing": "Medium",
                    },
                    {
                        "type": "FactSet",
                        "facts": [
                            {"title": "Avg Field Completeness",
                             "value": f"{avg_pct}%"},
                            {"title": "Leads Notes-Scored",
                             "value": str(scored_count)},
                            {"title": "Top Missing Fields",
                             "value": missing_text},
                            {"title": f"Poor Notes (score ≤ 2, first 5)",
                             "value": poor_ids},
                        ],
                    },
                    {
                        "type": "TextBlock",
                        "text": (
                            f"Field completeness is **{avg_pct}%**. "
                            + ("✅ Acceptable." if avg_pct >= 80
                               else "⚠️ Below target — Extensia training may be needed.")
                        ),
                        "wrap": True,
                        "color": completeness_color,
                        "spacing": "Small",
                    },
                ],
            }
        ]

    # ── All clear block ───────────────────────────────────────────────────────
    if total == 0:
        gap_body = [
            {
                "type": "TextBlock",
                "text": "✅ Excellent — no pipeline gaps detected this month. All leads are on track.",
                "color": "Good",
                "weight": "Bolder",
                "wrap": True,
                "spacing": "Medium",
            }
        ]
    else:
        gap_body = [
            unroutable_section,
            stale_engaged_section,
            stale_quoted_section,
            stale_followup_section,
            missing_fields_section,
            dark_section,
            long_age_section,
            *rej_items,
        ]

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
                     "text": f"🔍 Monthly Gap Analysis — {now_str}",
                     "weight": "Bolder", "size": "Large",
                     "color": "Attention" if total > 0 else "Good",
                     "wrap": True, "spacing": "Small"},
                    {"type": "TextBlock",
                     "text": f"{gaps.get('total_leads', 0)} total leads · {total} gap item(s) · {severity}",
                     "size": "Small", "color": "Light", "isSubtle": True,
                     "wrap": True, "spacing": "Small"},
                ],
            },
            # Scorecard
            {
                "type": "Container",
                "spacing": "Medium",
                "items": [
                    {"type": "TextBlock", "text": "Overview",
                     "weight": "Bolder", "separator": True, "spacing": "Medium"},
                    scorecard_row1,
                    scorecard_row2,
                ],
            },
            # Gap sections
            *gap_body,
            # Extensia quality
            *extensia_items,
            # Footer
            {
                "type": "TextBlock",
                "text": f"© {CURRENT_YEAR} GWC Logistics · Automated monthly gap analysis — do not reply",
                "size": "Small",
                "isSubtle": True,
                "wrap": True,
                "separator": True,
                "spacing": "Medium",
            },
        ],
    }

    return title, card
