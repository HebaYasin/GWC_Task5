"""
dark_lead_detector.py
---------------------
Detects "dark leads" — leads in ENGAGED+ status where the sales rep
has gone quiet (no CC'd emails in the shared mailbox for 5+ days).

Dark lead rules (from build spec):
  - Lead is in ENGAGED, QUOTED, or FOLLOW_UP status
  - No new CC'd emails detected for DARK_LEAD_DAYS (5) days
  - System should flag and nudge the rep

This is separate from the follow-up cadence (Skill 4), which handles
scheduled reminders. Dark lead detection is about detecting when reps
forget to CC the shared mailbox entirely.

Usage:
    from dark_lead_detector import check_dark_leads
    dark = check_dark_leads(active_leads, thread_email_counts)
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

DARK_LEAD_DAYS = 5
DARK_LEAD_STATUSES = {"ENGAGED", "QUOTED", "FOLLOW_UP"}


def _parse_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def check_dark_leads(active_leads: list[dict], thread_counts: dict[str, int]) -> list[dict]:
    """
    For each active lead, determine if it's a dark lead.

    Args:
        active_leads:   list of lead row dicts from leads_maturity.csv
        thread_counts:  dict mapping gwc_id → number of CC'd emails found
                        (0 means no CC emails visible in the mailbox)

    Returns:
        list of dark lead dicts with:
          gwc_id, current_status, assigned_rep_email, assigned_rep_name,
          days_silent, last_known_activity, reason
    """
    now = datetime.now(timezone.utc)
    dark_leads = []

    for lead in active_leads:
        status = lead.get("current_status", "")
        gwc_id = lead.get("gwc_id", "")

        if status not in DARK_LEAD_STATUSES:
            continue

        # Determine last known activity date
        # Priority: quote_sent_at > first_response_at > email_received_at
        last_activity_str = (
            lead.get("quote_sent_at")
            or lead.get("first_response_at")
            or lead.get("email_received_at")
            or ""
        )
        last_activity_dt = _parse_dt(last_activity_str)

        if not last_activity_dt:
            continue

        days_silent = (now - last_activity_dt).days
        cc_count = thread_counts.get(gwc_id, 0)

        if days_silent >= DARK_LEAD_DAYS and cc_count == 0:
            dark_leads.append({
                "gwc_id":              gwc_id,
                "current_status":      status,
                "assigned_rep_email":  lead.get("assigned_rep_email", ""),
                "assigned_rep_name":   lead.get("assigned_rep_name", ""),
                "company_name":        lead.get("company_name", ""),
                "contact_name":        lead.get("contact_name", ""),
                "to_country":          lead.get("to_country", ""),
                "days_silent":         days_silent,
                "last_known_activity": last_activity_str,
                "reason":              (
                    f"Lead has been in {status} status for {days_silent} days "
                    f"with no CC'd emails detected in {SHARED_MAILBOX}."
                ),
            })

    return dark_leads


def build_dark_lead_summary(dark_leads: list[dict]) -> str:
    """
    Build a human-readable summary of dark leads for logging/reporting.
    """
    if not dark_leads:
        return "No dark leads detected."

    lines = [f"⚠️  {len(dark_leads)} dark lead(s) detected:\n"]
    for d in dark_leads:
        lines.append(
            f"  • {d['gwc_id']} — {d['company_name']} → {d['to_country']}\n"
            f"    Status: {d['current_status']} | Silent for: {d['days_silent']} days\n"
            f"    Rep: {d['assigned_rep_name']} <{d['assigned_rep_email']}>\n"
            f"    Action: Nudge rep to CC {SHARED_MAILBOX} on all correspondence\n"
        )
    return "\n".join(lines)


# ── Constants re-exported for use in SKILL.md ─────────────────────────────────
SHARED_MAILBOX = "Sales.rfq@gwclogistics.com"
