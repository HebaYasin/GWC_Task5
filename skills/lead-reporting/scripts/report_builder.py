"""
report_builder.py
-----------------
Aggregates leads_maturity.csv and lead_activity_log.csv into structured
data used by the weekly lead report.

All functions return plain dicts/lists — no rendering here.
report_email_template.py handles HTML output.

Usage:
    from report_builder import build_report
    data = build_report(store)
"""

import json
from datetime import datetime, timezone, timedelta
from collections import defaultdict


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_iso(ts: str):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _days_since(ts: str) -> int:
    dt = _parse_iso(ts)
    if not dt:
        return 0
    return (datetime.now(timezone.utc) - dt).days


def _status_order(status: str) -> int:
    order = {"NO_ACTION": 0, "ENGAGED": 1, "QUOTED": 2,
             "FOLLOW_UP": 3, "WON_LOSS": 4, "REJECTED": 5}
    return order.get(status, 99)


# ── Main aggregator ────────────────────────────────────────────────────────────

def build_report(store, period_days: int = 7) -> dict:
    """
    Build a full weekly report data structure.

    Args:
        store:       CSVStore instance
        period_days: look-back window for "new this week" counts (default 7)

    Returns:
        dict with keys: meta, funnel, by_status, by_rep, by_mode,
                        dark_leads, recent_activity, stale_leads, all_leads
    """
    now       = datetime.now(timezone.utc)
    week_ago  = now - timedelta(days=period_days)
    leads     = store._read_csv(store.leads_path)
    activity  = store._read_csv(store.activity_path)

    # ── Meta ──────────────────────────────────────────────────────────────────
    meta = {
        "generated_at":  now.strftime("%Y-%m-%d %H:%M UTC"),
        "report_date":   now.strftime("%d %B %Y"),
        "period_days":   period_days,
        "week_start":    week_ago.strftime("%d %b"),
        "week_end":      now.strftime("%d %b %Y"),
        "total_leads":   len(leads),
    }

    # ── Status counts ──────────────────────────────────────────────────────────
    status_counts = defaultdict(int)
    for l in leads:
        status_counts[l.get("current_status", "UNKNOWN")] += 1

    # ── Funnel (active pipeline only) ─────────────────────────────────────────
    funnel_statuses = ["NO_ACTION", "ENGAGED", "QUOTED", "FOLLOW_UP", "WON_LOSS"]
    funnel = [
        {"status": s, "count": status_counts.get(s, 0)}
        for s in funnel_statuses
    ]
    rejected_count = status_counts.get("REJECTED", 0)

    # ── New leads this period ──────────────────────────────────────────────────
    new_this_period = [
        l for l in leads
        if _parse_iso(l.get("email_received_at", "")) and
           _parse_iso(l.get("email_received_at", "")) >= week_ago
    ]

    # ── By rep ────────────────────────────────────────────────────────────────
    rep_stats = defaultdict(lambda: {
        "rep_email": "", "rep_name": "",
        "total": 0, "engaged": 0, "quoted": 0,
        "follow_up": 0, "won": 0, "lost": 0,
    })
    for l in leads:
        rep  = l.get("assigned_rep_email", "") or "Unassigned"
        name = l.get("assigned_rep_name", "")  or "Unassigned"
        st   = l.get("current_status", "")
        rep_stats[rep]["rep_email"] = rep
        rep_stats[rep]["rep_name"]  = name
        rep_stats[rep]["total"]    += 1
        if st == "ENGAGED":    rep_stats[rep]["engaged"]   += 1
        if st == "QUOTED":     rep_stats[rep]["quoted"]    += 1
        if st == "FOLLOW_UP":  rep_stats[rep]["follow_up"] += 1
        if st == "WON_LOSS":
            outcome = l.get("deal_outcome", "")
            if outcome == "WON":  rep_stats[rep]["won"]  += 1
            if outcome == "LOSS": rep_stats[rep]["lost"] += 1

    by_rep = sorted(rep_stats.values(), key=lambda r: -r["total"])

    # ── By mode of freight ────────────────────────────────────────────────────
    mode_counts = defaultdict(int)
    for l in leads:
        mode = l.get("mode_of_freight", "") or "Unknown"
        if l.get("current_status") != "REJECTED":
            mode_counts[mode] += 1
    by_mode = sorted(
        [{"mode": m, "count": c} for m, c in mode_counts.items()],
        key=lambda x: -x["count"]
    )

    # ── Dark leads (ENGAGED/QUOTED/FOLLOW_UP, 5+ days since last activity) ───
    dark_leads = []
    for l in leads:
        st = l.get("current_status", "")
        if st not in ("ENGAGED", "QUOTED", "FOLLOW_UP"):
            continue
        last_scan = l.get("last_email_scan_at", "") or l.get("updated_at", "")
        days_silent = _days_since(last_scan)
        if days_silent >= 5:
            dark_leads.append({
                "gwc_id":      l["gwc_id"],
                "company":     l.get("company_name", ""),
                "contact":     l.get("contact_name", ""),
                "status":      st,
                "rep_email":   l.get("assigned_rep_email", ""),
                "days_silent": days_silent,
            })

    # ── Stale leads (in ENGAGED/QUOTED beyond expected thresholds) ───────────
    STALE_DAYS = {"ENGAGED": 14, "QUOTED": 10, "FOLLOW_UP": 20}
    stale_leads = []
    for l in leads:
        st = l.get("current_status", "")
        if st not in STALE_DAYS:
            continue
        ts_map = {
            "ENGAGED":    l.get("first_response_at", ""),
            "QUOTED":     l.get("quote_sent_at", ""),
            "FOLLOW_UP":  l.get("follow_up_started_at", ""),
        }
        days_in_status = _days_since(ts_map.get(st, ""))
        if days_in_status >= STALE_DAYS[st]:
            stale_leads.append({
                "gwc_id":         l["gwc_id"],
                "company":        l.get("company_name", ""),
                "status":         st,
                "days_in_status": days_in_status,
                "rep_email":      l.get("assigned_rep_email", ""),
                "route":          f"{l.get('from_country','')} → {l.get('to_country','')}",
            })

    # ── Recent activity (last N days) ─────────────────────────────────────────
    recent_activity = []
    for row in activity:
        ts = _parse_iso(row.get("timestamp", ""))
        if ts and ts >= week_ago:
            recent_activity.append({
                "gwc_id":   row.get("gwc_id", ""),
                "type":     row.get("activity_type", ""),
                "detail":   row.get("detail", ""),
                "ts":       row.get("timestamp", ""),
                "by":       row.get("performed_by", ""),
            })
    recent_activity.sort(key=lambda r: r["ts"], reverse=True)

    # ── Win/loss summary ──────────────────────────────────────────────────────
    won  = sum(1 for l in leads if l.get("deal_outcome") == "WON")
    lost = sum(1 for l in leads if l.get("deal_outcome") == "LOSS")

    return {
        "meta":            meta,
        "funnel":          funnel,
        "status_counts":   dict(status_counts),
        "rejected_count":  rejected_count,
        "new_this_period": len(new_this_period),
        "by_rep":          by_rep,
        "by_mode":         by_mode,
        "dark_leads":      dark_leads,
        "stale_leads":     stale_leads,
        "recent_activity": recent_activity,
        "won":             won,
        "lost":            lost,
        "all_leads":       leads,
    }
