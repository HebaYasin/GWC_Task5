"""
gap_detector.py
---------------
Identifies structural gaps in the GWC lead pipeline and scores Extensia
submission quality.

Gap categories (pipeline health):
  1. UNROUTABLE_LEADS    — leads with no assigned rep (country not in mapping)
  2. STALE_ENGAGED       — ENGAGED > 14 days without sending a quote
  3. STALE_QUOTED        — QUOTED > 10 days without customer response
  4. STALE_FOLLOW_UP     — FOLLOW_UP > 20 days without deal closure
  5. MISSING_FIELDS      — active leads with incomplete data for quoting
  6. DARK_LEADS          — ENGAGED/QUOTED/FOLLOW_UP with no email scan for 5+ days
  7. HIGH_REJECTION_RATE — REJECTED count > 30% of total (flag data quality issue)
  8. LONG_PIPELINE_AGE   — leads older than 30 days still in active status

Extensia quality analysis:
  - Field completeness score per lead (% of mode-specific mandatory fields present)
  - Notes quality score (AI-scored 1–5, stored in notes_quality_score field)
  - Patterns of consistently missing fields
  - Specific leads with insufficient Notes flagged for Extensia training samples

IMPORTANT: Notes quality scoring (1–5) is an AI judgment call.
When this module returns leads for notes scoring, Claude reads the Notes field
and scores it inline — do NOT call an external model. Score criteria:
  1 — Empty or completely uninformative (e.g. "N/A", "nil", "-")
  2 — Minimal info (e.g. single keyword like "customs needed")
  3 — Some context but missing specifics (partial timeline or vague requirement)
  4 — Good context — enough for a rep to start a productive conversation
  5 — Excellent — detailed timeline, packaging info, specific requirements

Usage:
    from gap_detector import detect_gaps, build_gap_summary, analyze_extensia_quality
    gaps  = detect_gaps(store)
    extensia = analyze_extensia_quality(store)   # call separately (requires AI scoring)
"""

import json
from datetime import datetime, timezone
from collections import defaultdict


# ── Mode-specific mandatory fields (mirrors classify_lead.py, Section 4) ──────

MANDATORY_CORE = ["from_country", "to_country", "mode_of_freight", "product", "weight_kg"]
MANDATORY_ALL_MOT = ["incoterms", "packages", "dimension_lwh"]

MANDATORY_BY_MOT = {
    "Air":      ["volume_m3", "chargeable_weight", "stackable"],
    "Sea_LCL":  ["volume_m3", "chargeable_weight", "stackable"],
    "Sea_FCL":  ["container_type"],
    "Overland": ["volume_m3"],
}

CONDITIONAL_MANDATORY = {
    "perishable": "temperature_details",
    "dg_class":   "msds",
}


def _get_mandatory_fields(lead: dict) -> list[str]:
    """Return the full list of mandatory fields for this lead's MOT + container mode."""
    mot = (lead.get("mode_of_freight") or "").strip().capitalize()
    cmode = (lead.get("container_mode") or "").strip().upper()

    fields = list(MANDATORY_CORE) + list(MANDATORY_ALL_MOT)

    if mot == "Air":
        fields += MANDATORY_BY_MOT["Air"]
    elif mot == "Sea":
        if cmode == "LCL":
            fields += MANDATORY_BY_MOT["Sea_LCL"]
        elif cmode == "FCL":
            fields += MANDATORY_BY_MOT["Sea_FCL"]
    elif mot == "Overland":
        fields += MANDATORY_BY_MOT["Overland"]

    # Conditional fields
    for trigger, dependent in CONDITIONAL_MANDATORY.items():
        if (lead.get(trigger) or "").strip().upper() == "Y":
            fields.append(dependent)

    # Deduplicate
    seen = set()
    return [f for f in fields if not (f in seen or seen.add(f))]


def _field_completeness_pct(lead: dict) -> float:
    """Return % of mode-specific mandatory fields that are non-empty (0–100)."""
    mandatory = _get_mandatory_fields(lead)
    if not mandatory:
        return 100.0
    filled = sum(
        1 for f in mandatory
        if (lead.get(f) or "").strip() not in ("", "n/a", "na", "-", "none")
    )
    return round(filled / len(mandatory) * 100, 1)


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


def _status_entry_ts(lead: dict) -> str:
    """Return the timestamp the lead entered its current status."""
    status = lead.get("current_status", "")
    ts_map = {
        "ENGAGED":   lead.get("first_response_at", ""),
        "QUOTED":    lead.get("quote_sent_at", ""),
        "FOLLOW_UP": lead.get("follow_up_started_at", ""),
    }
    ts = ts_map.get(status, "")
    if ts:
        return ts
    # Fallback: status_history
    try:
        for entry in reversed(json.loads(lead.get("status_history", "[]") or "[]")):
            if entry.get("status") == status:
                return entry.get("timestamp", "")
    except (json.JSONDecodeError, TypeError):
        pass
    return lead.get("email_received_at", "")


# ── Pipeline gap detectors ────────────────────────────────────────────────────

def detect_gaps(store) -> dict:
    """
    Run all pipeline gap checks and return a dict of categorised gap lists.

    Returns:
        {
          "unroutable":         [lead, ...],
          "stale_engaged":      [lead, ...],
          "stale_quoted":       [lead, ...],
          "stale_follow_up":    [lead, ...],
          "missing_fields":     [{lead, missing_field_names}, ...],
          "dark_leads":         [{lead, days_silent}, ...],
          "high_rejection":     bool,
          "rejection_rate_pct": float,
          "long_age":           [{lead, age_days}, ...],
          "summary_counts":     {category: count, ...},
          "total_gaps":         int,
          "total_leads":        int,
          "rejected_count":     int,
        }
    """
    leads = store._read_csv(store.leads_path)
    total = len(leads)
    now   = datetime.now(timezone.utc)

    unroutable      = []
    stale_engaged   = []
    stale_quoted    = []
    stale_follow_up = []
    missing_fields  = []
    dark_leads      = []
    long_age        = []
    rejected        = 0

    for lead in leads:
        status  = lead.get("current_status", "")
        gwc_id  = lead.get("gwc_id", "")
        company = lead.get("company_name", "")
        rep     = lead.get("assigned_rep_email", "")
        route   = f"{lead.get('from_country','')} → {lead.get('to_country','')}"

        # 1. Unroutable
        if status == "NO_ACTION" and not rep:
            unroutable.append({
                "gwc_id":   gwc_id,
                "company":  company,
                "contact":  lead.get("contact_name", ""),
                "route":    route,
                "age_days": _days_since(lead.get("email_received_at", "")),
                "notes":    lead.get("notes", ""),
            })

        # 2–4. Stale by status
        if status == "ENGAGED":
            days = _days_since(lead.get("first_response_at", "") or _status_entry_ts(lead))
            if days >= 14:
                stale_engaged.append({
                    "gwc_id": gwc_id, "company": company,
                    "route": route, "rep": rep, "days": days,
                })

        if status == "QUOTED":
            days = _days_since(lead.get("quote_sent_at", "") or _status_entry_ts(lead))
            if days >= 10:
                stale_quoted.append({
                    "gwc_id": gwc_id, "company": company,
                    "route": route, "rep": rep, "days": days,
                })

        if status == "FOLLOW_UP":
            days = _days_since(lead.get("follow_up_started_at", "") or _status_entry_ts(lead))
            if days >= 20:
                stale_follow_up.append({
                    "gwc_id": gwc_id, "company": company,
                    "route": route, "rep": rep, "days": days,
                })

        # 5. Missing fields (only relevant for active/actionable leads)
        if status not in ("REJECTED", "WON_LOSS", "GAP_ANALYSIS"):
            raw = lead.get("missing_fields", "")
            try:
                fields = json.loads(raw) if raw else []
            except (json.JSONDecodeError, TypeError):
                fields = []
            if fields:
                missing_fields.append({
                    "gwc_id":  gwc_id,
                    "company": company,
                    "status":  status,
                    "rep":     rep,
                    "missing": fields,
                })

        # 6. Dark leads
        if status in ("ENGAGED", "QUOTED", "FOLLOW_UP"):
            last = lead.get("last_email_scan_at", "") or lead.get("updated_at", "")
            days_silent = _days_since(last)
            if days_silent >= 5:
                dark_leads.append({
                    "gwc_id":      gwc_id,
                    "company":     company,
                    "status":      status,
                    "rep":         rep,
                    "days_silent": days_silent,
                })

        # 7. Rejection counter
        if status == "REJECTED":
            rejected += 1

        # 8. Long pipeline age (active leads > 30 days)
        if status in ("NO_ACTION", "ENGAGED", "QUOTED", "FOLLOW_UP"):
            age = _days_since(lead.get("email_received_at", ""))
            if age >= 30:
                long_age.append({
                    "gwc_id":  gwc_id,
                    "company": company,
                    "status":  status,
                    "rep":     rep,
                    "age":     age,
                })

    rejection_rate = (rejected / total * 100) if total else 0
    high_rejection = rejection_rate > 30

    summary_counts = {
        "unroutable":      len(unroutable),
        "stale_engaged":   len(stale_engaged),
        "stale_quoted":    len(stale_quoted),
        "stale_follow_up": len(stale_follow_up),
        "missing_fields":  len(missing_fields),
        "dark_leads":      len(dark_leads),
        "long_age":        len(long_age),
        "high_rejection":  1 if high_rejection else 0,
    }
    total_gaps = sum(summary_counts.values())

    return {
        "unroutable":         unroutable,
        "stale_engaged":      stale_engaged,
        "stale_quoted":       stale_quoted,
        "stale_follow_up":    stale_follow_up,
        "missing_fields":     missing_fields,
        "dark_leads":         dark_leads,
        "high_rejection":     high_rejection,
        "rejection_rate_pct": round(rejection_rate, 1),
        "long_age":           long_age,
        "summary_counts":     summary_counts,
        "total_gaps":         total_gaps,
        "total_leads":        total,
        "rejected_count":     rejected,
    }


# ── Extensia quality analysis ─────────────────────────────────────────────────

def analyze_extensia_quality(store, date_from: str = "", date_to: str = "") -> dict:
    """
    Analyse Extensia submission quality across all leads (or a filtered date range).

    This function prepares data for AI-assisted Notes quality scoring.
    Claude must score each lead's Notes field (1–5) inline when executing this skill
    — do NOT delegate to an external model.

    Scoring rubric for notes_quality_score:
      1 — Empty, "nil", "N/A", or a single meaningless word
      2 — Minimal (e.g. single keyword: "customs needed" / "ETD 07 may")
      3 — Partial context — mentions one useful detail but misses specifics
      4 — Good — enough for a rep to start a productive conversation
      5 — Excellent — detailed timeline, packaging, specific requirements, ETD

    Args:
        store:     CSVStore instance
        date_from: ISO date string (inclusive) to filter leads, e.g. "2026-03-01"
        date_to:   ISO date string (inclusive) to filter leads, e.g. "2026-04-30"

    Returns:
        {
          "leads_analyzed":         int,
          "avg_completeness_pct":   float,
          "completeness_by_mot":    {mot: avg_pct, ...},
          "most_missing_fields":    [(field_name, count), ...],  # top 10, sorted desc
          "notes_to_score":         [                            # leads needing AI scoring
              {
                  "gwc_id":         str,
                  "company":        str,
                  "mot":            str,
                  "notes":          str,
                  "completeness_pct": float,
                  "already_scored": bool,
                  "current_score":  int or None,
              },
              ...
          ],
          "poor_notes_samples":     [...],  # notes_quality_score <= 2
          "date_from":              str,
          "date_to":                str,
        }
    """
    leads = store._read_csv(store.leads_path)

    # Filter by date range if provided
    if date_from or date_to:
        filtered = []
        for lead in leads:
            received = (lead.get("email_received_at") or lead.get("created_at") or "")[:10]
            if date_from and received < date_from:
                continue
            if date_to and received > date_to:
                continue
            filtered.append(lead)
        leads = filtered

    # Skip purely rejected leads with no shipment data
    scoreable = [l for l in leads if l.get("current_status") != "REJECTED" or l.get("gwc_id")]

    total = len(scoreable)
    if total == 0:
        return {
            "leads_analyzed": 0,
            "avg_completeness_pct": 0.0,
            "completeness_by_mot": {},
            "most_missing_fields": [],
            "notes_to_score": [],
            "poor_notes_samples": [],
            "date_from": date_from,
            "date_to": date_to,
        }

    # Field completeness stats
    completeness_scores = []
    completeness_by_mot = defaultdict(list)
    missing_field_counts = defaultdict(int)

    notes_to_score = []
    poor_notes_samples = []

    for lead in scoreable:
        gwc_id  = lead.get("gwc_id", "")
        mot     = lead.get("mode_of_freight", "Unknown")
        company = lead.get("company_name", "")
        notes   = (lead.get("notes") or "").strip()

        # Field completeness
        pct = _field_completeness_pct(lead)
        completeness_scores.append(pct)
        completeness_by_mot[mot].append(pct)

        # Missing field frequency
        raw_missing = lead.get("missing_fields", "")
        try:
            missing = json.loads(raw_missing) if raw_missing else []
        except (json.JSONDecodeError, TypeError):
            missing = []
        for f in missing:
            missing_field_counts[f] += 1

        # Notes scoring data
        already_scored = bool(lead.get("notes_quality_score", "").strip())
        current_score = None
        if already_scored:
            try:
                current_score = int(float(lead["notes_quality_score"]))
            except (ValueError, TypeError):
                current_score = None

        notes_entry = {
            "gwc_id":           gwc_id,
            "company":          company,
            "mot":              mot,
            "notes":            notes if notes else "(empty)",
            "completeness_pct": pct,
            "already_scored":   already_scored,
            "current_score":    current_score,
        }
        notes_to_score.append(notes_entry)

        # Collect poor-notes samples (already scored at 1 or 2)
        if current_score is not None and current_score <= 2:
            poor_notes_samples.append({
                "gwc_id":  gwc_id,
                "company": company,
                "mot":     mot,
                "notes":   notes if notes else "(empty)",
                "score":   current_score,
            })

    avg_completeness = round(sum(completeness_scores) / total, 1) if total else 0.0
    avg_by_mot = {
        mot: round(sum(scores) / len(scores), 1)
        for mot, scores in completeness_by_mot.items()
    }

    # Top missing fields
    top_missing = sorted(missing_field_counts.items(), key=lambda x: -x[1])[:10]

    return {
        "leads_analyzed":       total,
        "avg_completeness_pct": avg_completeness,
        "completeness_by_mot":  avg_by_mot,
        "most_missing_fields":  top_missing,
        "notes_to_score":       notes_to_score,
        "poor_notes_samples":   poor_notes_samples,
        "date_from":            date_from,
        "date_to":              date_to,
    }


def save_notes_scores(store, scored_leads: list[dict]):
    """
    Write AI-generated notes_quality_score and extensia_feedback back to leads_maturity.csv.

    Args:
        store: CSVStore instance
        scored_leads: list of dicts with keys:
            gwc_id, notes_quality_score (int 1-5), extensia_feedback (str)
    """
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for item in scored_leads:
        gwc_id = item.get("gwc_id")
        if not gwc_id:
            continue
        store.update_lead_field(gwc_id, {
            "notes_quality_score": str(item.get("notes_quality_score", "")),
            "extensia_feedback":   item.get("extensia_feedback", ""),
            "updated_at":          now_iso,
        })


def transition_to_gap_analysis(store, gwc_id: str, reason: str = "Gap analysis reviewed"):
    """
    Transition a lead to GAP_ANALYSIS status.
    Only applies to leads that have completed the main pipeline (WON_LOSS or long-stale active).
    Status moves forward only — this cannot override WON_LOSS or REJECTED.

    Call this after a human SME has reviewed the lead's gap analysis output.
    """
    from datetime import datetime, timezone
    import json

    lead = store.get_lead(gwc_id)
    if not lead:
        return False

    current = lead.get("current_status", "")
    # GAP_ANALYSIS is a terminal review state — only transition from active or WON_LOSS
    if current in ("REJECTED", "GAP_ANALYSIS"):
        return False

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        history = json.loads(lead.get("status_history", "[]") or "[]")
    except (json.JSONDecodeError, TypeError):
        history = []

    history.append({
        "status":     "GAP_ANALYSIS",
        "timestamp":  now_iso,
        "changed_by": "SYSTEM",
        "reason":     reason,
    })

    store.update_lead_field(gwc_id, {
        "current_status": "GAP_ANALYSIS",
        "status_history": json.dumps(history),
        "updated_at":     now_iso,
    })

    store.log_activity(
        gwc_id=gwc_id,
        activity_type="STATUS_CHANGE",
        detail={
            "previous_status": current,
            "new_status":      "GAP_ANALYSIS",
            "reason":          reason,
        },
        performed_by="SYSTEM",
    )
    return True


def build_gap_summary(gaps: dict) -> str:
    """Return a compact plain-text summary of gap findings (for console output)."""
    lines = [
        f"Gap Analysis Summary ({gaps['total_leads']} leads total)",
        "─" * 50,
    ]
    counts = gaps["summary_counts"]
    labels = {
        "unroutable":      "Unroutable leads (no rep assigned)",
        "stale_engaged":   "Stale ENGAGED (14+ days, no quote)",
        "stale_quoted":    "Stale QUOTED (10+ days, no reply)",
        "stale_follow_up": "Stale FOLLOW_UP (20+ days, not closed)",
        "missing_fields":  "Leads with missing required fields",
        "dark_leads":      "Dark leads (5+ days silent)",
        "long_age":        "Old leads (30+ days in pipeline)",
        "high_rejection":  "High rejection rate flag",
    }
    for key, label in labels.items():
        n = counts.get(key, 0)
        icon = "⚠️ " if n > 0 else "✅ "
        lines.append(f"  {icon}{label}: {n}")

    if gaps["high_rejection"]:
        lines.append(
            f"\n  ⚠️  Rejection rate: {gaps['rejection_rate_pct']}% "
            f"({gaps['rejected_count']}/{gaps['total_leads']} leads)"
        )
    lines.append(f"\nTotal gap items: {gaps['total_gaps']}")
    return "\n".join(lines)
