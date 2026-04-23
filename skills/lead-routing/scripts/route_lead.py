"""
route_lead.py
-------------
Lead routing logic: looks up destination country → sales rep(s),
determines who to notify, and returns routing decisions.

Routing rules (from build spec):
  - QUALIFIED:           Notify primary rep (or all reps if no primary set)
  - PARTIALLY_QUALIFIED: Notify primary rep (or all reps if no primary set)
  - REJECTED:            Never routed
  - Country not found:   Return unroutable, notify manager

Usage:
    from route_lead import get_routing_decision, MANAGER_EMAIL

    decision = get_routing_decision(lead_row, store)
    # decision = {
    #   "routable":         True/False,
    #   "reps":             [{"rep_email":..., "rep_name":..., "is_primary":...}],
    #   "primary_rep":      {...},  # the one who gets the email
    #   "unroutable_reason": "",
    # }
"""

import sys
import os

# ── Config ────────────────────────────────────────────────────────────────────

# Manager / admin email to notify when a lead cannot be routed
MANAGER_EMAIL = "hebah.yasin@gwclogistics.com"

# Per-country manager overrides. Falls back to MANAGER_EMAIL if country not listed.
# Add a "manager_email" column to country_rep_mapping.csv and this map auto-populates
# at runtime — or hardcode overrides here as needed.
COUNTRY_MANAGER_MAP: dict[str, str] = {
    # "Qatar": "manager.qatar@gwclogistics.com",
    # "KSA & Bahrain": "manager.ksa@gwclogistics.com",
}


def get_manager_for_country(canonical_country: str) -> str:
    """Return the manager email for a country, defaulting to global MANAGER_EMAIL."""
    return COUNTRY_MANAGER_MAP.get(canonical_country, MANAGER_EMAIL)


# Country name normalisation aliases for fuzzy matching
# Maps variants of country names to canonical names in country_rep_mapping.csv
COUNTRY_ALIASES = {
    # UAE variants
    "united arab emirates": "UAE",
    "u.a.e": "UAE",
    "u.a.e.": "UAE",
    "dubai": "UAE",
    "abu dhabi": "UAE",
    "sharjah": "UAE",
    "uae": "UAE",

    # Qatar variants
    "qatar": "Qatar",
    "doha": "Qatar",
    "state of qatar": "Qatar",

    # KSA / Bahrain
    "saudi arabia": "KSA",
    "kingdom of saudi arabia": "KSA",
    "ksa": "KSA",
    "bahrain": "Bahrain",
    "kingdom of bahrain": "Bahrain",
    "riyadh": "KSA",
    "jeddah": "KSA",
    "dammam": "KSA",
    "manama": "Bahrain",

    # Oman
    "oman": "Oman",
    "sultanate of oman": "Oman",
    "muscat": "Oman",
}


def normalise_country(country_name: str) -> str:
    """Return canonical country name for rep lookup, or original if no alias found."""
    if not country_name:
        return ""
    return COUNTRY_ALIASES.get(country_name.strip().lower(), country_name.strip())


def get_routing_decision(lead: dict, store) -> dict:
    """
    Determine routing for a single lead.

    Args:
        lead:  A row dict from leads_maturity.csv
        store: A CSVStore instance (from csv_store.py)

    Returns:
        dict with:
          routable (bool)
          reps (list of rep dicts)
          primary_rep (dict — the rep who gets the email, or None)
          unroutable_reason (str — only if not routable)
          canonical_country (str — normalised country name used for lookup)
    """
    classification = lead.get("classification", "")
    to_country = lead.get("to_country", "")

    # REJECTED leads are never routed
    if classification == "REJECTED":
        return {
            "routable": False,
            "reps": [],
            "primary_rep": None,
            "unroutable_reason": "Lead is REJECTED — not eligible for routing.",
            "canonical_country": "",
        }

    # Already routed (has assigned rep)
    if lead.get("assigned_rep_email", "").strip():
        return {
            "routable": False,
            "reps": [],
            "primary_rep": None,
            "unroutable_reason": f"Already routed to {lead['assigned_rep_email']}",
            "canonical_country": normalise_country(to_country),
        }

    # ── Routing country: Quip "Support" column is authoritative ─────────────
    # The "Support" column (col I) in the Digital Sales Leads Quip sheet
    # (thread XbavARpEgyTa) records which GWC office owns the lead.
    # Rule: if the lead is in Quip, quip_country ALWAYS wins — even if the
    # shipment destination is a country with no configured rep (e.g. Kazakhstan).
    # Never route by email to_country when quip_country is set.
    quip_country   = (lead.get("quip_country") or "").strip()
    routing_source = quip_country if quip_country else to_country

    if quip_country:
        if quip_country != to_country:
            print(f"[routing] Quip Support='{quip_country}' overrides email to_country='{to_country}'")

    canonical = normalise_country(routing_source)
    reps = store.lookup_reps(canonical)
    if not reps and canonical != routing_source:
        reps = store.lookup_reps(routing_source)

    if not reps:
        return {
            "routable": False,
            "reps": [],
            "primary_rep": None,
            "unroutable_reason": f"No sales rep configured for country: '{routing_source}' "
                     f"(Quip: '{quip_country}', email to_country: '{to_country}', "
                     f"canonical: '{canonical}')",
            "canonical_country": canonical,
        }

    # Find primary rep; fall back to first rep if no primary flagged
    # Up to 2 primary reps get notified; all reps are tracked in the activity log
    notify_reps = [r for r in reps if r.get("is_primary", "").upper() == "TRUE"][:2]
    if not notify_reps:
        notify_reps = reps[:1]   # fallback: first rep if none flagged as primary

    return {
        "routable":          True,
        "all_reps":          reps,                              # ALL reps — for tracking/logging only
        "notify_reps":       notify_reps,                       # 1–2 primary reps — for Teams DM
        "primary_rep":       notify_reps[0],                    # kept for backward compat
        "manager_email":     get_manager_for_country(canonical),
        "unroutable_reason": "",
        "canonical_country": canonical,
    }


def get_unrouted_leads(store) -> list[dict]:
    """
    Return all leads that are:
      - current_status == NO_ACTION
      - assigned_rep_email is empty (not yet routed)
      - classification is not REJECTED
      - never previously escalated as unroutable (checked via activity log)
    """
    import json

    rows = store._read_csv(store.leads_path)

    # Build set of GWC IDs that were already escalated as unroutable
    # so we don't send duplicate manager alerts on re-runs.
    activity_rows = store._read_csv(store.activity_path)
    already_escalated = set()
    for a in activity_rows:
        if a.get("activity_type") == "ESCALATION":
            try:
                detail = json.loads(a.get("activity_detail", "{}"))
            except Exception:
                detail = {}
            if detail.get("action") == "unroutable_alert_sent":
                already_escalated.add(a.get("gwc_id", ""))

    return [
        r for r in rows
        if r.get("current_status") == "NO_ACTION"
        and not r.get("assigned_rep_email", "").strip()
        and r.get("classification") != "REJECTED"
        and r.get("gwc_id", "") not in already_escalated
    ]
