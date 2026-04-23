"""
send_followup_reminders.py
--------------------------
Main orchestrator for the lead-follow-up-cadence skill.

Provides helper functions that the SKILL.md instructs Claude to call
step-by-step. Claude uses OUTLOOK_SEND_EMAIL (outlook-composio MCP)
to send each reminder and then calls log_reminder() to record it.

Usage (see SKILL.md for full step-by-step):
    from send_followup_reminders import get_reminder_tasks, log_reminder, build_summary
"""

import json
from datetime import datetime, timezone

WORKSPACE = "/sessions/gifted-affectionate-lovelace/mnt/marketing-campaign-leads-tracking automation"
SHARED_MAILBOX = "Sales.rfq@gwclogistics.com"
MANAGER_EMAIL  = "hebah.yasin@gwclogistics.com"


# ── Step 1 helper: find due reminders ────────────────────────────────────────

def get_reminder_tasks(store) -> list[dict]:
    """
    Return a list of reminder task dicts for every lead that is due a
    follow-up reminder today and has not already received one.

    Each dict contains: lead, status, days_elapsed, threshold_day,
    context, rep_email, rep_name, gwc_id.

    Returns [] if nothing is due.
    """
    import sys
    sys.path.insert(0, f"{WORKSPACE}/skills/lead-follow-up-cadence/scripts")
    from cadence_rules import get_leads_needing_reminder
    return get_leads_needing_reminder(store)


# ── Step 3 helper: log a sent reminder ───────────────────────────────────────

def log_reminder(store, task: dict, sent_ok: bool, error_msg: str = "") -> None:
    """
    Record a reminder entry in the activity log.
    NO_ACTION leads use activity_type "NO_REPLY_REMINDER"; all others use
    "FOLLOW_UP_REMINDER" (ESCALATION_REMINDER is recorded via context type).
    Always call this after attempting to send, whether it succeeded or failed.
    """
    reminder_type = task["context"]["type"]
    if reminder_type == "ESCALATION_REMINDER":
        activity_type = "ESCALATION_REMINDER"
    elif task["status"] == "NO_ACTION":
        activity_type = "NO_REPLY_REMINDER"
    else:
        activity_type = "FOLLOW_UP_REMINDER"

    store.log_activity(
        gwc_id=task["gwc_id"],
        activity_type=activity_type,
        detail={
            "status":        task["status"],
            "days_elapsed":  task["days_elapsed"],
            "threshold_day": task["threshold_day"],
            "reminder_type": reminder_type,
            "rep_email":     task["rep_email"],
            "sent_ok":       sent_ok,
            "error":         error_msg,
        },
        performed_by="SYSTEM",
    )


# ── Step 4 helper: build the printed summary ──────────────────────────────────

def build_summary(tasks: list[dict], results: list[dict]) -> str:
    """
    Build a human-readable summary string after all reminders have been processed.

    Args:
        tasks:   list of reminder task dicts
        results: list of {"task": task, "sent": bool, "error": str}
    """
    sent_ok  = [r for r in results if r["sent"]]
    failed   = [r for r in results if not r["sent"]]

    lines = [
        "✅ Follow-Up Cadence Complete",
        "─" * 46,
        f"Reminders due:   {len(tasks)}",
        f"  Sent:          {len(sent_ok)}",
        f"  Failed:        {len(failed)}",
        "",
    ]

    if tasks:
        lines.append("Reminder breakdown:")
        for r in results:
            t = r["task"]
            icon = "✓" if r["sent"] else "✗"
            rtype = t["context"]["type"]
            lines.append(
                f"  {icon} {t['gwc_id']} | {t['status']} | Day {t['threshold_day']} "
                f"| {rtype} → {t['rep_email']}"
                + (f"  [ERROR: {r['error']}]" if not r["sent"] else "")
            )
    else:
        lines.append("No reminders due today.")

    lines += ["", "Activity logged ✓"]
    return "\n".join(lines)


# ── Dry-run helper (no Outlook calls) ────────────────────────────────────────

def dry_run(store) -> str:
    """
    Return a preview of what reminders WOULD be sent without actually
    sending any emails or writing to the activity log.
    Useful for testing and for the SKILL.md dry-run step.
    """
    tasks = get_reminder_tasks(store)

    if not tasks:
        return "DRY RUN: No reminders due today."

    lines = [
        f"DRY RUN: {len(tasks)} reminder(s) would be sent:",
        "",
    ]
    for t in tasks:
        lines.append(
            f"  • {t['gwc_id']} | {t['status']} | Day {t['threshold_day']} "
            f"({t['days_elapsed']} days elapsed)"
        )
        lines.append(f"    → TO: {t['rep_email']}")
        lines.append(f"    → TYPE: {t['context']['type']}")
        lines.append(f"    → ACTION: {t['context']['action']}")
        lines.append("")
    return "\n".join(lines)
