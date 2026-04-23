"""
cadence_rules.py
----------------
Defines follow-up cadence thresholds for each active lead status and
determines which leads need a reminder today.

Cadence schedule (days since entering each status):

  NO_ACTION → rep was notified but has not yet sent any email to the customer
              → daily reminders on days 1–14 (NO_REPLY_REMINDER)
  ENGAGED   → remind rep to send a quote on days 3, 7, 14
  QUOTED    → remind rep to chase customer on days 2, 5, 10
  FOLLOW_UP → NEW SCHEDULE (v2 spec, Section 6):
                Week 1 (days 1–7):  Daily reminders
                Week 2 (days 8–14): Twice per week → day thresholds 10, 14
                Week 3 (days 15–21): Once per week  → day threshold 21
                Week 4 (days 22–28): Final closure  → day threshold 28 (ESCALATION)

Deduplication: a reminder is only sent once per (gwc_id, status, threshold_day).
The activity log is checked for existing FOLLOW_UP_REMINDER / NO_REPLY_REMINDER
entries to prevent repeats.

Escalation: FOLLOW_UP leads that cross day 28 are flagged for manager review
(escalation email sent in addition to the regular reminder).
"""

import json
from datetime import datetime, timezone
from typing import Optional


# ── Cadence thresholds ────────────────────────────────────────────────────────

# Keys must match current_status values exactly.
# Values are lists of day thresholds at which a reminder should fire.
CADENCE_THRESHOLDS: dict[str, list[int]] = {
    # Rep notified but has not replied to the customer yet — fire daily for 14 days.
    # Stops naturally once Phase 3 transitions the lead to ENGAGED.
    "NO_ACTION":  list(range(1, 15)),
    "ENGAGED":    [3, 7, 14],
    "QUOTED":     [2, 5, 10],
    # Week 1: daily (1–7), Week 2: approx Mon+Thu (10, 14),
    # Week 3: approx Mon (21), Week 4: final closure (28)
    "FOLLOW_UP":  [1, 2, 3, 4, 5, 6, 7, 10, 14, 21, 28],
}

# Day 28 threshold in FOLLOW_UP triggers manager escalation
FOLLOW_UP_ESCALATION_DAY = 28

# Human-friendly message per status (used in emails & logs)
CADENCE_CONTEXT: dict[str, dict] = {
    "NO_ACTION": {
        "action":  "reply to the customer",
        "urgency": "You have been assigned this lead but have not yet sent any email to the customer.",
        "type":    "NO_REPLY_REMINDER",
        "escalate": False,
    },
    "ENGAGED": {
        "action":  "send a quotation",
        "urgency": "The customer is waiting for your proposal.",
        "type":    "QUOTE_REMINDER",
        "escalate": False,
    },
    "QUOTED": {
        "action":  "follow up with the customer",
        "urgency": "The customer has not replied since you sent the quote.",
        "type":    "CHASER_REMINDER",
        "escalate": False,
    },
    "FOLLOW_UP": {
        "action":  "close this deal",
        "urgency": "The deal has been in follow-up — push to confirm won or lost.",
        "type":    "CLOSE_REMINDER",
        "escalate": False,
    },
}

# Context overrides for escalation day
FOLLOW_UP_ESCALATION_CONTEXT = {
    "action":  "resolve or escalate this deal immediately",
    "urgency": "This lead has been in follow-up for 4+ weeks with no resolution. "
               "Manager has been notified and manual review is required.",
    "type":    "ESCALATION_REMINDER",
    "escalate": True,
}

# Human-readable week label for FOLLOW_UP thresholds (for email subject lines)
FOLLOW_UP_WEEK_LABELS: dict[int, str] = {
    1:  "Week 1 – Day 1",
    2:  "Week 1 – Day 2",
    3:  "Week 1 – Day 3",
    4:  "Week 1 – Day 4",
    5:  "Week 1 – Day 5",
    6:  "Week 1 – Day 6",
    7:  "Week 1 – Day 7",
    10: "Week 2 – Follow-up",
    14: "Week 2 – Final Push",
    21: "Week 3 – Weekly Check",
    28: "Week 4 – Final Closure",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_iso(ts: str) -> Optional[datetime]:
    """Parse ISO 8601 timestamp to an aware UTC datetime."""
    if not ts:
        return None
    try:
        ts = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def days_since_status_entry(lead: dict) -> Optional[int]:
    """
    Return how many full days have elapsed since the lead entered its current status.

    Uses dedicated timestamp fields first, then falls back to parsing status_history JSON.
    Returns None if no entry timestamp can be determined.
    """
    status = lead.get("current_status", "")

    # Try dedicated timestamp fields first
    ts_field_map = {
        "NO_ACTION":  lead.get("routed_at", ""),
        "ENGAGED":    lead.get("first_response_at", ""),
        "QUOTED":     lead.get("quote_sent_at", ""),
        "FOLLOW_UP":  lead.get("follow_up_started_at", ""),
    }
    ts_str = ts_field_map.get(status, "")
    entry_dt = _parse_iso(ts_str)

    # Fallback: find the status entry in status_history JSON
    if not entry_dt:
        try:
            history = json.loads(lead.get("status_history", "[]") or "[]")
            for entry in reversed(history):
                if entry.get("status") == status:
                    entry_dt = _parse_iso(entry.get("timestamp", ""))
                    break
        except (json.JSONDecodeError, TypeError):
            pass

    if not entry_dt:
        return None

    now = datetime.now(timezone.utc)
    return (now - entry_dt).days


def get_due_threshold(days_elapsed: int, thresholds: list[int]) -> Optional[int]:
    """
    Return the highest threshold that has been crossed.

    The deduplication check (was_reminder_sent) ensures each threshold only
    fires once — this function just returns the current maximum crossed threshold.

    E.g. thresholds = [3, 7, 14], days_elapsed = 8
    → day 7 threshold is crossed (day 14 not yet reached) → return 7

    Returns None if no threshold has been crossed yet.
    """
    if days_elapsed is None:
        return None
    crossed = [t for t in thresholds if days_elapsed >= t]
    if not crossed:
        return None
    return max(crossed)


def was_reminder_sent(gwc_id: str, threshold_day: int,
                      activity_rows: list[dict],
                      activity_types: tuple = ("FOLLOW_UP_REMINDER", "ESCALATION_REMINDER")) -> bool:
    """
    Check whether a reminder of the given activity_types has already been logged
    for this (gwc_id, threshold_day) combination.

    Pass activity_types=("NO_REPLY_REMINDER",) for NO_ACTION leads so that
    a logged NO_REPLY day-3 entry never blocks a QUOTE_REMINDER day-3 entry
    after the lead is promoted to ENGAGED.
    """
    for row in activity_rows:
        if row.get("gwc_id") != gwc_id:
            continue
        if row.get("activity_type") not in activity_types:
            continue
        try:
            detail = json.loads(row.get("activity_detail", "{}") or "{}")
            if int(detail.get("threshold_day", -1)) == threshold_day:
                return True
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    return False


def get_follow_up_context(threshold_day: int) -> dict:
    """
    Return context dict for a FOLLOW_UP reminder, with escalation override
    applied if the lead has crossed the escalation threshold (day 28).
    """
    if threshold_day >= FOLLOW_UP_ESCALATION_DAY:
        return FOLLOW_UP_ESCALATION_CONTEXT
    ctx = dict(CADENCE_CONTEXT["FOLLOW_UP"])
    week_label = FOLLOW_UP_WEEK_LABELS.get(threshold_day, f"Day {threshold_day}")
    ctx["week_label"] = week_label
    return ctx


# ── Main eligibility function ─────────────────────────────────────────────────

def get_leads_needing_reminder(store) -> list[dict]:
    """
    Scan all active (ENGAGED / QUOTED / FOLLOW_UP) leads in the CSV store
    and return a list of reminder task dicts for every lead that is due a
    reminder and has not already received one for that threshold.

    Each reminder task dict:
    {
        "lead":          <lead row dict>,
        "status":        "ENGAGED" | "QUOTED" | "FOLLOW_UP",
        "days_elapsed":  <int>,
        "threshold_day": <int>,
        "context":       <dict from CADENCE_CONTEXT / FOLLOW_UP_ESCALATION_CONTEXT>,
        "rep_email":     <str>,
        "rep_name":      <str>,
        "gwc_id":        <str>,
        "is_escalation": <bool>,
    }
    """
    # Load activity log once for deduplication checks
    try:
        activity_rows = store._read_csv(store.activity_path)
    except Exception:
        activity_rows = []

    tasks = []

    all_leads = store._read_csv(store.leads_path)
    for lead in all_leads:
        status = lead.get("current_status", "")
        if status not in CADENCE_THRESHOLDS:
            continue  # skip REJECTED, WON_LOSS, GAP_ANALYSIS

        # NO_ACTION leads are only eligible once a rep has been assigned
        if status == "NO_ACTION" and not lead.get("assigned_rep_email", "").strip():
            continue

        rep_email = lead.get("assigned_rep_email", "").strip()
        if not rep_email:
            continue  # no rep assigned → can't send reminder

        thresholds = CADENCE_THRESHOLDS[status]
        days_elapsed = days_since_status_entry(lead)
        if days_elapsed is None:
            continue

        threshold_day = get_due_threshold(days_elapsed, thresholds)
        if threshold_day is None:
            continue  # not yet due

        gwc_id = lead["gwc_id"]

        # Use status-specific activity_types for deduplication so that
        # a NO_REPLY day-N entry never blocks a QUOTE_REMINDER day-N entry
        # once the lead is promoted to ENGAGED.
        if status == "NO_ACTION":
            dedup_types = ("NO_REPLY_REMINDER",)
        else:
            dedup_types = ("FOLLOW_UP_REMINDER", "ESCALATION_REMINDER")

        if was_reminder_sent(gwc_id, threshold_day, activity_rows, dedup_types):
            continue  # already sent for this threshold

        # Build context (FOLLOW_UP gets week-aware context)
        if status == "FOLLOW_UP":
            context = get_follow_up_context(threshold_day)
        else:
            context = CADENCE_CONTEXT[status]

        is_escalation = context.get("escalate", False)

        tasks.append({
            "lead":          lead,
            "status":        status,
            "days_elapsed":  days_elapsed,
            "threshold_day": threshold_day,
            "context":       context,
            "rep_email":     rep_email,
            "rep_name":      lead.get("assigned_rep_name", rep_email.split("@")[0]),
            "gwc_id":        gwc_id,
            "is_escalation": is_escalation,
        })

    return tasks
