"""
dashboard_builder.py
---------------------
Reads leads_maturity.csv and lead_activity_log.csv and returns a single
JSON-serialisable dict that powers all 7 dashboard tabs.

Tabs:
  1  Pipeline Overview   — funnel, 60-day arrivals trend, mode donut
  2  No Response         — inaction by country, overdue counts
  3  Engagement          — response-speed histogram, MOT breakdown, cumulative %
  4  Quoting & Follow-Up — quote age, engagement→quote gap, cadence burn-down, follow-up aging
  5  Won / Loss          — deal outcomes, close-age distribution, deal detail
  6  Rep Performance     — leaderboard, volume bar, avg response vs SLA
  7  Data Quality        — field completeness, MOT heatmap, gap patterns

All lead rows are embedded as raw JSON so the JS date-range filter can re-slice
every chart without a server round-trip.

Usage:
    from dashboard_builder import build_dashboard_data
    data = build_dashboard_data(store)
    # data is JSON-serialisable
"""

import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import os
import re

# ── Field definitions ─────────────────────────────────────────────────────────

# (csv_key, display_label) — shown in the Data Quality tab
ALL_DISPLAY_FIELDS = [
    ("contact_name",       "Contact Name"),
    ("company_name",       "Company"),
    ("phone",              "Phone"),
    ("from_country",       "Origin Country"),
    ("to_country",         "Destination Country"),
    ("mode_of_freight",    "Mode of Freight"),
    ("product",            "Commodity / Product"),
    ("weight_kg",          "Weight (kg)"),
    ("incoterms",          "Incoterms"),
    ("packages",           "Number of Packages"),
    ("dimension_lwh",      "Dimensions (L×W×H)"),
    ("volume_m3",          "Volume (m³)"),
    ("chargeable_weight",  "Chargeable Weight"),
    ("stackable",          "Stackable (Y/N)"),
    ("container_mode",     "Container Mode"),
    ("shipping_requirements", "Shipping Requirements"),
    ("notes",              "Notes"),
    ("hubspot_create_date","HubSpot Create Date"),
]

# Fields required for ALL shipments regardless of MOT
UNIVERSAL_REQUIRED = {"incoterms", "packages", "dimension_lwh"}

# Extra fields required per canonical MOT
MOT_EXTRA_REQUIRED = {
    "Air":      {"chargeable_weight"},
    "Sea":      {"volume_m3", "chargeable_weight", "stackable", "container_mode"},
    "Overland": {"container_mode"},
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_iso(ts: str):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _days_between(ts_start: str, ts_end: str):
    a = _parse_iso(ts_start)
    b = _parse_iso(ts_end)
    if not a or not b:
        return None
    diff = (b - a).days
    return diff if diff >= 0 else None


def _days_since(ts: str):
    dt = _parse_iso(ts)
    if not dt:
        return None
    return max(0, (datetime.now(timezone.utc) - dt).days)


def _safe_float(val):
    try:
        return float(val) if val not in (None, "", "None") else None
    except (ValueError, TypeError):
        return None


def _mot_key(mot: str) -> str:
    """Normalise mode_of_freight to a key used in MOT_EXTRA_REQUIRED."""
    if not mot:
        return ""
    m = mot.strip().title()
    if m in ("Air",):
        return "Air"
    if m in ("Sea",):
        return "Sea"
    if m in ("Overland",):
        return "Overland"
    return m


def _bucket_response(days) -> str:
    """Day 1–15+ histogram bucket for response-speed chart."""
    if days is None:
        return None
    if days == 0:
        return "Same day"
    if days <= 15:
        return f"Day {days}"
    return "Day 15+"


def _quote_age_bucket(days) -> str:
    if days is None:
        return "Unknown"
    if days <= 3:
        return "0–3 days"
    if days <= 7:
        return "4–7 days"
    if days <= 14:
        return "8–14 days"
    if days <= 30:
        return "15–30 days"
    return "30+ days"


def _close_age_bucket(days) -> str:
    if days is None:
        return "Unknown"
    if days <= 7:
        return "≤7 days"
    if days <= 14:
        return "8–14 days"
    if days <= 30:
        return "15–30 days"
    if days <= 60:
        return "31–60 days"
    return "60+ days"


def _missing_fields_list(lead: dict) -> list:
    """Return parsed missing_fields list from the lead record."""
    raw = lead.get("missing_fields", "")
    try:
        if raw:
            return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    return []




#new
# ── Freight domain keyword groups (hardcoded; enriched from freight_domain_knowledge.md if present) ──
_DOMAIN_KW_GROUPS = {
    "Incoterms":    ["exw", "fob", "cif", "cfr", "ddp", "dap", "cpt", "fca", "dpu", "fas", "cip"],
    "Customs":      ["customs", "clearance", "import", "export", "duties", "tariff", "hs code", "declaration"],
    "Timeline":     ["etd", "eta", "urgent", "asap", "within a week", "by end", "next week", "2 days", "3 days"],
    "Packaging":    ["pallet", "carton", "box", "crate", "drum", "bag", "bundle", "loose", "stackable"],
    "Cargo Type":   ["perishable", "dg", "dangerous", "hazmat", "temperature", "frozen", "cold chain", "fragile", "msds"],
    "Service Req":  ["door to door", "port to port", "warehouse", "last mile", "pickup", "delivery", "consolidation"],
}


def _load_domain_kw_groups(workspace=None):
    """Return keyword groups, optionally enriched from freight_domain_knowledge.md."""
    groups = {k: list(v) for k, v in _DOMAIN_KW_GROUPS.items()}
    if not workspace:
        return groups
    fdk = os.path.join(workspace, "skills", "lead-status-tracker", "references", "freight_domain_knowledge.md")
    if not os.path.exists(fdk):
        return groups
    try:
        text = open(fdk, encoding="utf-8").read().lower()
        extra = list({
            w for w in re.findall(r'\b[a-z]{4,}\b', text)
            if w not in {kw for grp in groups.values() for kw in grp}
        })[:60]
        groups["Domain Knowledge"] = extra
    except Exception:
        pass
    return groups


def _build_notes_intelligence(leads, workspace=None):
    """Compute all Tab 8 metrics from notes_quality_score + notes text."""
    kw_groups = _load_domain_kw_groups(workspace)
    all_kws   = [kw for grp in kw_groups.values() for kw in grp]

    _EMPTY = {"", "n/a", "n/a  \xa0", "none"}
    def _is_empty(n): return str(n or "").strip().lower() in _EMPTY

    active = [l for l in leads if l.get("current_status") != "REJECTED"]
    filled = [l for l in active if not _is_empty(l.get("notes"))]
    empty  = [l for l in active if     _is_empty(l.get("notes"))]

    # Score distribution
    score_dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for l in active:
        s = int(_safe_float(l.get("notes_quality_score") or 0) or 0)
        if 1 <= s <= 5:
            score_dist[s] += 1

    scored_vals = [_safe_float(l.get("notes_quality_score") or 0) for l in active]
    scored_vals = [v for v in scored_vals if v and v >= 1]
    avg_score = round(sum(scored_vals) / len(scored_vals), 2) if scored_vals else None

    # Length bucketing
    LEN_BKTS = ["Empty", "1–3 words", "4–10 words", "11–25 words", "25+ words"]
    def _len_bkt(note):
        n = str(note or "").strip()
        if _is_empty(n): return "Empty"
        wc = len(n.split())
        if wc <= 3:  return "1–3 words"
        if wc <= 10: return "4–10 words"
        if wc <= 25: return "11–25 words"
        return "25+ words"
    len_dist = defaultdict(int)
    for l in active:
        len_dist[_len_bkt(l.get("notes", ""))] += 1

    # Keyword hits across filled notes
    kw_hits = defaultdict(int)
    for l in filled:
        note_lc = str(l.get("notes", "")).lower()
        for kw in all_kws:
            if kw in note_lc:
                kw_hits[kw] += 1

    # Per-group presence rate (% of filled notes mentioning ≥1 kw from group)
    group_presence = {}
    for gname, kws in kw_groups.items():
        hits = sum(1 for l in filled if any(kw in str(l.get("notes","")).lower() for kw in kws))
        group_presence[gname] = {
            "hits": hits,
            "pct": round(hits / len(filled) * 100) if filled else 0,
        }

    # Avg score by MOT
    mot_scores = defaultdict(list)
    for l in active:
        v = _safe_float(l.get("notes_quality_score") or 0)
        if v and v >= 1:
            mot_scores[l.get("mode_of_freight") or "Unknown"].append(v)
    mot_avg_score = {m: round(sum(v)/len(v), 1) for m, v in mot_scores.items() if v}

    # Avg score by destination country (top 8 by lead count)
    country_scores = defaultdict(list)
    for l in active:
        v = _safe_float(l.get("notes_quality_score") or 0)
        if v and v >= 1:
            country_scores[
                (l.get("quip_country") or l.get("assigned_country") or l.get("to_country") or "Unknown").strip() or "Unknown"
            ].append(v)
    country_avg_score = sorted(
        [{"country": c, "avg": round(sum(v)/len(v), 1), "count": len(v)}
         for c, v in country_scores.items() if v],
        key=lambda x: -x["count"]
    )[:8]

    # Domain group coverage per note (how many groups covered)
    cov_dist = defaultdict(int)
    for l in filled:
        note_lc = str(l.get("notes","")).lower()
        covered = sum(1 for kws in kw_groups.values() if any(kw in note_lc for kw in kws))
        cov_dist[covered] += 1
    cov_dist[0] += len(empty)  # empty notes cover 0 groups

    # Worst notes (score=1, non-empty — for Extensia training)
    worst = sorted(
        [l for l in active
         if int(_safe_float(l.get("notes_quality_score") or 0) or 0) == 1
         and not _is_empty(l.get("notes"))],
        key=lambda x: x.get("email_received_at", ""), reverse=True
    )[:10]

    # Best notes (score 4-5)
    best = [l for l in active if int(_safe_float(l.get("notes_quality_score") or 0) or 0) >= 4][:5]

    def _canonical_country(l):
        return (
            l.get("quip_country") or
            l.get("assigned_country") or
            l.get("to_country") or
            "Unknown"
        ).strip() or "Unknown"

    def _note_row(l):
        return {
            "gwc_id":    l.get("gwc_id", ""),
            "company":   l.get("company_name", "") or l.get("contact_name", ""),
            "mot":       l.get("mode_of_freight", ""),
            "to_country": _canonical_country(l),
            "notes":     str(l.get("notes", ""))[:200],
            "score":     int(_safe_float(l.get("notes_quality_score") or 0) or 0),
            "feedback":  str(l.get("extensia_feedback", "") or ""),
        }
    
    # ── A: Radar — Domain Coverage per group (already have group_presence) ──
    # group_presence is already computed above — reuse it directly for the radar

    # ── B: Scatter — Quality Score vs. Lead Age Days ──
    scatter_age = []
    for l in active:
        s = int(_safe_float(l.get("notes_quality_score") or 0) or 0)
        age = _safe_float(l.get("lead_age_days") or 0)
        if 1 <= s <= 5 and age is not None:
            scatter_age.append({"x": s, "y": round(age, 1)})

    # ── C: Scatter — Quality Score vs. deal_outcome (Won/Lost counts) ──
    outcome_by_score = {i: {"Won": 0, "Lost": 0} for i in range(1, 6)}
    for l in active:
        s = int(_safe_float(l.get("notes_quality_score") or 0) or 0)
        outcome = str(l.get("deal_outcome") or "").strip().upper()
        if 1 <= s <= 5:
            if "WON" in outcome:
                outcome_by_score[s]["Won"] += 1
            elif "LOST" in outcome or "LOSS" in outcome:
                outcome_by_score[s]["Lost"] += 1

    # ── D: Technical Gap Bubble — missing fields in PARTIALLY_QUALIFIED leads ──
    from collections import Counter
    partial_leads = [l for l in active if l.get("classification") == "PARTIALLY_QUALIFIED"]
    missing_counter = Counter()
    for l in partial_leads:
        mf = l.get("missing_fields_list") or l.get("missing_fields") or []
        if isinstance(mf, str):
            import json as _json
            try:    mf = _json.loads(mf)
            except: mf = [x.strip().strip('"') for x in mf.strip("[]").split(",") if x.strip()]
        for f in mf:
            if f:
                missing_counter[f.strip()] += 1
    top_missing = [{"field": f, "count": c} for f, c in missing_counter.most_common(12)]

    # ── E: Grouped Bar — Note Sentiment (extensia_feedback) by score ──
    # from the MD file
    # 1. Expand the requirements to track categories
    TECHNICAL_REQUIREMENTS = {
        "specs": ["weight", "kg", "cbm", "dimensions", "volume", "pallet", "pkg"],
        "commercial": ["incoterm", "exw", "fob", "cif", "dap", "ddp"],
        "cargo": ["perishable", "hazardous", "dg", "imo", "msds", "temp"],
        "docs": ["hs code", "cr number", "packing list", "invoice"]
    }

    # 2. Track coverage per category for the Radar Chart
    coverage_totals = {cat: 0 for cat in TECHNICAL_REQUIREMENTS}
    sentiment_by_score = {i: {"positive": 0, "technical_gap": 0, "mismatch": 0, "other": 0} for i in range(1, 6)}

    for l in active:
        s = int(_safe_float(l.get("notes_quality_score") or 0) or 0)
        fb = str(l.get("extensia_feedback") or "").lower()
        notes = str(l.get("notes") or "").lower()
        
        # Calculate category presence
        lead_tech_profile = {}
        for cat, keywords in TECHNICAL_REQUIREMENTS.items():
            found = any(k in notes for k in keywords)
            lead_tech_profile[cat] = found
            if found: coverage_totals[cat] += 1

        tech_depth = sum(lead_tech_profile.values())
        
        if 1 <= s <= 5:
            # Positive: Rep is happy
            if any(w in fb for w in ["good", "useful", "great", "helpful", "sufficient"]):
                sentiment_by_score[s]["positive"] += 1
            # Technical Gap: Rep says missing, and we see low tech depth
            elif any(w in fb for w in ["missing", "needs", "incomplete"]) and tech_depth < 2:
                sentiment_by_score[s]["technical_gap"] += 1
            # Mismatch: System said it's good (4+), but rep said it's poor
            elif s >= 4 and any(w in fb for w in ["poor", "lacking", "no context", "insufficient"]):
                sentiment_by_score[s]["mismatch"] += 1
            else:
                sentiment_by_score[s]["other"] += 1

    return {
        "total_active":      len(active),
        "notes_filled_count":len(filled),
        "notes_empty_count": len(empty),
        "notes_fill_rate":   round(len(filled) / len(active) * 100) if active else 0,
        "avg_score":         avg_score,
        "score_dist":        score_dist,
        "length_buckets":    LEN_BKTS,
        "length_dist":       dict(len_dist),
        "group_presence":    group_presence,
        "mot_avg_score":     mot_avg_score,
        "country_avg_score": country_avg_score,
        "coverage_dist":     dict(cov_dist),
        "top_keywords":      sorted(kw_hits.items(), key=lambda x: -x[1])[:20],
        "worst_samples":     [_note_row(l) for l in worst],
        "best_samples":      [_note_row(l) for l in best],

        "scatter_age":         scatter_age,
        "outcome_by_score":    outcome_by_score,
        "top_missing_fields":  top_missing,
        "sentiment_by_score":  sentiment_by_score,
    }

# ── Main builder ──────────────────────────────────────────────────────────────

def build_dashboard_data(store) -> dict:
    """
    Return a JSON-serialisable dict for the full 7-tab dashboard.
    All date filtering happens in JavaScript; Python provides the raw material.
    """
    leads    = store._read_csv(store.leads_path)
    activity = store._read_csv(store.activity_path)
    now      = datetime.now(timezone.utc)

    # True unfiltered total — needed for Card 1 (In/Not-in-Quip) when the store
    # is a QuipFilteredStore.  _orig_read exists on filtered stores; plain CSVStore
    # raises AttributeError so we fall back to the already-loaded leads list.
    try:
        _all_leads_raw  = store._orig_read(store.leads_path)
        _full_total     = len(_all_leads_raw)
    except AttributeError:
        _full_total     = len(leads)

    # ── Primary rep lookup (country → primary rep) ───────────────────────────
    # Built once here; reused by both Tab 2 (no-response) and Tab 6 (rep perf).
    # Only is_primary=TRUE rows are included — primary reps are the team leads
    # who are the single point of accountability for each country.
    _COUNTRY_ALIASES = {
        "saudi arabia":            "ksa",
        "kingdom of saudi arabia": "ksa",
        "ksa":                     "ksa",
        "united arab emirates":    "uae",
        "u.a.e":                   "uae",
        "uae":                     "uae",
        "bahrain":                 "bahrain",
        "qatar":                   "qatar",
        "oman":                    "oman",
    }

    def _norm(name: str) -> str:
        n = (name or "").strip().lower()
        return _COUNTRY_ALIASES.get(n, n)

    country_rep_lookup = {}   # normalised_name → {rep_email, rep_name, display_name}
    _mapping_display   = {}   # normalised_name → canonical display name from CSV
    try:
        mapping_rows = store._read_csv(store.mapping_path)
        for row in mapping_rows:
            if str(row.get("is_primary", "")).upper() == "TRUE":
                c = (row.get("country_name") or "").strip()
                if c:
                    key = _norm(c)
                    country_rep_lookup[key] = {
                        "rep_email":    row.get("rep_email", ""),
                        "rep_name":     row.get("rep_name", ""),
                        "display_name": c,
                    }
                    _mapping_display[key] = c
    except Exception:
        pass

    # ── Enrich each lead ─────────────────────────────────────────────────────
    enriched = []
    for raw in leads:
        lead = dict(raw)
        mot  = _mot_key(lead.get("mode_of_freight", ""))

        # Timing deltas
        lead["days_to_response"]          = _days_between(lead.get("email_received_at",""), lead.get("first_response_at",""))
        lead["days_to_quote"]             = _days_between(lead.get("email_received_at",""), lead.get("quote_sent_at",""))
        lead["days_engagement_to_quote"]  = _days_between(lead.get("first_response_at",""), lead.get("quote_sent_at",""))
        lead["days_to_close"]             = _days_between(lead.get("email_received_at",""), lead.get("deal_confirmed_at",""))
        lead["lead_age_days"]             = _days_since(lead.get("email_received_at",""))

        # Bucket labels
        lead["response_bucket"]   = _bucket_response(lead["days_to_response"])
        lead["quote_age_bucket"]  = _quote_age_bucket(lead["days_to_quote"])
        lead["close_age_bucket"]  = _close_age_bucket(lead["days_to_close"])

        # Missing fields
        lead["missing_fields_list"] = _missing_fields_list(lead)

        # Weight
        lead["weight_kg"] = _safe_float(lead.get("weight_kg", ""))

        # Field completeness — count which fields have a value
        required_keys = UNIVERSAL_REQUIRED | MOT_EXTRA_REQUIRED.get(mot, set())
        lead["required_fields"]   = sorted(required_keys)
        lead["fields_missing"]    = [f for f in required_keys if not lead.get(f, "").strip()]
        lead["fields_present"]    = [f for f in required_keys if lead.get(f, "").strip()]
        lead["completeness_pct"]  = (
            round(len(lead["fields_present"]) / len(required_keys) * 100)
            if required_keys else 100
        )

        # Resolve effective rep — primary rep is the team lead accountable for
        # this lead. Use assigned_rep from Phase 2 routing if present; otherwise
        # fall back to the primary rep from country_rep_mapping.csv via
        # assigned_country (routing decision) → to_country (email parse).
        # "Unassigned" should never appear after routing; if it does it is a
        # data-quality signal, not a permanent label.
        eff_email = (lead.get("assigned_rep_email") or "").strip()
        eff_name  = (lead.get("assigned_rep_name")  or "").strip()
        if not eff_email:
            # Priority: Quip "Support" column (team closest to origin/deal) →
            # assigned_country (Phase 2 routing decision) → to_country (email parse).
            # quip_country is the most authoritative — it's set by the sales manager
            # in Quip and reflects which GWC office owns the lead.
            lookup_key = _norm(
                lead.get("quip_country") or lead.get("assigned_country") or lead.get("to_country") or ""
            )
            rep_info  = country_rep_lookup.get(lookup_key, {})
            eff_email = rep_info.get("rep_email", "")
            eff_name  = rep_info.get("rep_name",  "")
        lead["effective_rep_email"] = eff_email or "Unassigned"
        lead["effective_rep_name"]  = eff_name  or "Unassigned"

        enriched.append(lead)

    # ── Quip-matched lead set (used for all Tabs 2–8 and most of Tab 1) ─────────
    # Leads processed by Phase 2 (REP_NOTIFIED or ESCALATION activity) are the
    # Quip-matched set — the only leads actively managed by the sales team.
    quip_ids      = {
        r["gwc_id"] for r in activity
        if r.get("activity_type") in ("REP_NOTIFIED", "ESCALATION")
    }
    quip_total    = len(quip_ids)
    quip_enriched = [l for l in enriched if l.get("gwc_id") in quip_ids]

    # ── TAB 1: Pipeline Overview ──────────────────────────────────────────────
    # all-leads funnel — kept ONLY for Response Rate denominator
    # (resp_rate_pct = all responded / all non-rejected)
    STATUS_ORDER = ["NO_ACTION", "ENGAGED", "QUOTED", "FOLLOW_UP", "WON_LOSS", "REJECTED"]
    funnel = {s: 0 for s in STATUS_ORDER}
    for l in enriched:
        st = l.get("current_status", "UNKNOWN")
        if st in funnel:
            funnel[st] += 1

    # All-leads KPIs (used only for Response Rate card and Not Matched card)
    active_statuses  = {"ENGAGED", "QUOTED", "FOLLOW_UP"}
    active_count     = sum(1 for l in enriched if l.get("current_status") in active_statuses)
    no_action_count  = funnel.get("NO_ACTION", 0)
    rejected_count   = funnel.get("REJECTED", 0)

    # Quip funnel — drives funnel bars, Active Pipeline card, Unresponded card,
    # arrivals chart, mode donut, and all of Tabs 2–8
    quip_funnel = {s: 0 for s in STATUS_ORDER}
    for l in quip_enriched:
        st = l.get("current_status", "UNKNOWN")
        if st in quip_funnel:
            quip_funnel[st] += 1

    quip_active_count    = sum(1 for l in quip_enriched if l.get("current_status") in active_statuses)
    quip_no_action_count = quip_funnel.get("NO_ACTION", 0)
    won_count            = sum(1 for l in quip_enriched if l.get("deal_outcome") == "WON")

    # Quip arrivals trend (60 days)
    arrivals = {}
    for i in range(60):
        day = (now - timedelta(days=59 - i)).strftime("%Y-%m-%d")
        arrivals[day] = 0
    for l in quip_enriched:
        ts = _parse_iso(l.get("email_received_at", ""))
        if ts:
            day = ts.strftime("%Y-%m-%d")
            if day in arrivals:
                arrivals[day] += 1
    arrivals_series = [{"date": d, "count": c} for d, c in sorted(arrivals.items())]

    # Quip mode breakdown (exclude REJECTED)
    mode_counts = defaultdict(int)
    for l in quip_enriched:
        if l.get("current_status") not in ("REJECTED",):
            mode_counts[l.get("mode_of_freight", "") or "Unknown"] += 1

    # ── TAB 2: No Response ───────────────────────────────────────────────────
    no_response_leads = [
        l for l in quip_enriched
        if l.get("current_status") == "NO_ACTION"
        and l.get("classification") != "REJECTED"
    ]

    # country_rep_lookup, _norm, _mapping_display are built at the top of this
    # function and reused here — no need to rebuild.

    # Group by (rep_name, destination_country) — one row per country per rep
    no_resp_by_rep_country = defaultdict(lambda: {"count": 0, "ages": [], "overdue": 0, "in_quip": 0, "rep_email": ""})
    for l in no_response_leads:
        # Use the most-reliable country source: quip_country (Phase 2 canonical) →
        # assigned_country (routing decision) → to_country (raw email parse).
        # This prevents city names like "Riyadh" appearing as separate entries.
        dest = (
            l.get("quip_country") or
            l.get("assigned_country") or
            l.get("to_country") or
            "Unknown"
        ).strip() or "Unknown"
        rep_info  = country_rep_lookup.get(_norm(dest), {"rep_name": "Unroutable", "rep_email": ""})
        rep_name  = rep_info["rep_name"]
        rep_email = rep_info.get("rep_email", "")
        key       = (rep_name, dest)
        age       = l.get("lead_age_days")
        no_resp_by_rep_country[key]["count"] += 1
        no_resp_by_rep_country[key]["rep_email"] = rep_email
        if age is not None:
            no_resp_by_rep_country[key]["ages"].append(age)
        if (age or 0) > 3:
            no_resp_by_rep_country[key]["overdue"] += 1
        if str(l.get("in_quip_sheet", "") or "").strip().upper() == "YES":
            no_resp_by_rep_country[key]["in_quip"] += 1

    # Sort: by rep name alpha, then by count desc within each rep
    sorted_keys = sorted(
        no_resp_by_rep_country.keys(),
        key=lambda k: (k[0], -no_resp_by_rep_country[k]["count"])
    )

    no_response_country_rows = []
    for (rep_name, dest) in sorted_keys:
        d       = no_resp_by_rep_country[(rep_name, dest)]
        avg_age = round(sum(d["ages"]) / len(d["ages"]), 1) if d["ages"] else None
        no_response_country_rows.append({
            "country":       dest,          # destination country handled by this rep
            "rep_name":      rep_name,
            "rep_email":     d["rep_email"],
            "count":         d["count"],
            "avg_age_days":  avg_age,
            "overdue_count": d["overdue"],
            "in_quip_count": d["in_quip"],
        })

    # Unique reps and countries for KPI cards
    reps_affected      = len({k[0] for k in no_resp_by_rep_country})
    overdue_count      = sum(1 for l in no_response_leads if (l.get("lead_age_days") or 0) > 3)
    countries_affected = len({k[1] for k in no_resp_by_rep_country})  # unique dest countries
    in_quip_count      = sum(
        1 for l in no_response_leads
        if str(l.get("in_quip_sheet", "") or "").strip().upper() == "YES"
    )



    # ── TAB 3: Engagement ────────────────────────────────────────────────────

    RESPONSE_BUCKETS = ["Same day"] + [f"Day {i}" for i in range(1, 16)] + ["Day 15+"]
    response_hist = {b: 0 for b in RESPONSE_BUCKETS}
    
    # Rep-specific response data
    rep_response_hist = defaultdict(lambda: {b: 0 for b in RESPONSE_BUCKETS})
    rep_cumulative_pct = {}
    rep_mot_response = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    
    for l in quip_enriched:
        rep = (l.get("effective_rep_email") or l.get("assigned_rep_email") 
               or l.get("effective_rep_name") or l.get("assigned_rep_name") or "Unassigned")
        
        b = l.get("response_bucket")
        if b and b in response_hist:
            response_hist[b] += 1
            rep_response_hist[rep][b] += 1
        elif not b and l.get("first_response_at"):
            # Timing anomaly: first_response_at stored as midnight (date-only) but
            # email_received_at has a later time the same day → diff is negative →
            # _days_between returns None → bucket is None.  These ARE same-day
            # responses — count them in "Same day" so the total matches engaged count.
            response_hist["Same day"] += 1
            rep_response_hist[rep]["Same day"] += 1

    # Cumulative % for response histogram (global)
    total_responded = sum(response_hist.values())
    cumulative = 0
    cumulative_pct = []
    for b in RESPONSE_BUCKETS:
        cumulative += response_hist.get(b, 0)
        pct = round(cumulative / total_responded * 100, 1) if total_responded else 0
        cumulative_pct.append(pct)
    
    # Rep-specific cumulative %
    for rep, hist in rep_response_hist.items():
        total_rep_responded = sum(hist.values())
        if total_rep_responded > 0:
            cumulative = 0
            rep_cumulative_pct[rep] = []
            for b in RESPONSE_BUCKETS:
                cumulative += hist.get(b, 0)
                pct = round(cumulative / total_rep_responded * 100, 1)
                rep_cumulative_pct[rep].append(pct)

    # Response speed by MOT (global)
    # mot_response = defaultdict(lambda: defaultdict(int))
    # for l in quip_enriched:
    #     if l.get("days_to_response") is not None:
    #         mot = l.get("mode_of_freight", "") or "Unknown"
    #         b   = l.get("response_bucket")
    #         if b:
    #             mot_response[mot][b] += 1
                
    #             # Rep-specific MOT response
    #             rep = (l.get("effective_rep_email") or l.get("assigned_rep_email") 
    #                    or l.get("effective_rep_name") or l.get("assigned_rep_name") or "Unassigned")
    #             rep_mot_response[rep][mot][b] += 1

    mot_response = defaultdict(lambda: defaultdict(int))
    for l in quip_enriched:
        mot = l.get("mode_of_freight", "") or "Unknown"
        b   = l.get("response_bucket")
        if b:
            mot_response[mot][b] += 1
        elif not b and l.get("first_response_at"):
            # Timing anomaly: same-day response stored without a bucket — count as "Same day"
            mot_response[mot]["Same day"] += 1
            rep = (l.get("effective_rep_email") or l.get("assigned_rep_email")
                or l.get("effective_rep_name") or l.get("assigned_rep_name") or "Unassigned")
            rep_mot_response[rep][mot]["Same day"] += 1

    # Average response time
    responded_leads  = [l for l in quip_enriched if l.get("days_to_response") is not None]
    avg_response_days = (
        round(sum(l["days_to_response"] for l in responded_leads) / len(responded_leads), 1)
        if responded_leads else None
    )

    # ── TAB 4: Quoting & Follow-Up ───────────────────────────────────────────

    QUOTE_AGE_BUCKETS = ["0–3 days", "4–7 days", "8–14 days", "15–30 days", "30+ days"]
    quote_age_hist = {b: 0 for b in QUOTE_AGE_BUCKETS}
    
    # Rep-specific quote age data
    rep_quote_age_hist = defaultdict(lambda: {b: 0 for b in QUOTE_AGE_BUCKETS})
    rep_gap_hist = defaultdict(lambda: {b: 0 for b in GAP_BUCKETS})
    rep_followup_age_hist = defaultdict(lambda: {b: 0 for b in FOLLOWUP_AGE_BUCKETS})
    rep_cadence_week_sent = defaultdict(lambda: {"Week 1": 0, "Week 2": 0, "Week 3": 0, "Week 4+": 0})
    
    for l in quip_enriched:
        rep = (l.get("effective_rep_email") or l.get("assigned_rep_email") 
               or l.get("effective_rep_name") or l.get("assigned_rep_name") or "Unassigned")
        
        b = l.get("quote_age_bucket")
        if b and l.get("quote_sent_at") and b in quote_age_hist:
            quote_age_hist[b] += 1
            rep_quote_age_hist[rep][b] += 1

    quoted_leads     = [l for l in quip_enriched if l.get("days_to_quote") is not None]
    avg_quote_age    = (
        round(sum(l["days_to_quote"] for l in quoted_leads) / len(quoted_leads), 1)
        if quoted_leads else None
    )

    # Engagement → quote gap
    gap_leads = [l for l in quip_enriched if l.get("days_engagement_to_quote") is not None]
    GAP_BUCKETS = ["Same day", "Day 1", "Day 2", "Day 3", "Day 4", "Day 5", "Day 6", "Day 7", "Day 7+"]
    gap_hist = {b: 0 for b in GAP_BUCKETS}
    for l in gap_leads:
        rep = (l.get("effective_rep_email") or l.get("assigned_rep_email") 
               or l.get("effective_rep_name") or l.get("assigned_rep_name") or "Unassigned")
        
        d = l["days_engagement_to_quote"]
        if d == 0:
            gap_hist["Same day"] += 1
            rep_gap_hist[rep]["Same day"] += 1
        elif d <= 7:
            gap_hist[f"Day {d}"] += 1
            rep_gap_hist[rep][f"Day {d}"] += 1
        else:
            gap_hist["Day 7+"] += 1
            rep_gap_hist[rep]["Day 7+"] += 1

    # Follow-up aging histogram
    followup_leads = [l for l in quip_enriched if l.get("current_status") == "FOLLOW_UP"]
    FOLLOWUP_AGE_BUCKETS = ["1–7 days", "8–14 days", "15–21 days", "22–28 days", "28+ days"]
    followup_age_hist = {b: 0 for b in FOLLOWUP_AGE_BUCKETS}
    unique_followup_customers = set()
    for l in followup_leads:
        rep = (l.get("effective_rep_email") or l.get("assigned_rep_email") 
               or l.get("effective_rep_name") or l.get("assigned_rep_name") or "Unassigned")
        
        age = l.get("lead_age_days") or 0
        if age <= 7:
            followup_age_hist["1–7 days"] += 1
            rep_followup_age_hist[rep]["1–7 days"] += 1
        elif age <= 14:
            followup_age_hist["8–14 days"] += 1
            rep_followup_age_hist[rep]["8–14 days"] += 1
        elif age <= 21:
            followup_age_hist["15–21 days"] += 1
            rep_followup_age_hist[rep]["15–21 days"] += 1
        elif age <= 28:
            followup_age_hist["22–28 days"] += 1
            rep_followup_age_hist[rep]["22–28 days"] += 1
        else:
            followup_age_hist["28+ days"] += 1
            rep_followup_age_hist[rep]["28+ days"] += 1
        cid = l.get("company_name", "") or l.get("contact_name", "")
        if cid:
            unique_followup_customers.add(cid.strip().lower())

    unique_followup = len(unique_followup_customers)

    # Cadence burn-down: count FOLLOW_UP_REMINDER / ESCALATION_REMINDER events per week
    # Bucket using threshold_day from activity_detail JSON (most accurate);
    # fall back to age_at_reminder via created_at - email_received_at if missing.
    cadence_week_sent = {"Week 1": 0, "Week 2": 0, "Week 3": 0, "Week 4+": 0}
    escalations_sent  = 0
    total_reminders   = 0
    rep_escalations_sent = defaultdict(int)
    for ev in activity:
        gwc_id = ev.get("gwc_id", "")
        lead_rec = next((l for l in quip_enriched if l.get("gwc_id") == gwc_id), None)
        rep = "Unassigned"
        if lead_rec:
            rep = (lead_rec.get("effective_rep_email") or lead_rec.get("assigned_rep_email") 
                   or lead_rec.get("effective_rep_name") or lead_rec.get("assigned_rep_name") or "Unassigned")
        
        atype = ev.get("activity_type", "")
        if atype in ("FOLLOW_UP_REMINDER", "CHASER_REMINDER", "QUOTE_REMINDER"):
            total_reminders += 1
            # Try threshold_day from activity_detail first (exact cadence day)
            detail_raw = ev.get("activity_detail", "")
            threshold_day = None
            if detail_raw:
                try:
                    import json as _json
                    detail = _json.loads(detail_raw) if isinstance(detail_raw, str) else detail_raw
                    threshold_day = int(detail.get("threshold_day", 0) or 0)
                except Exception:
                    threshold_day = None
            if threshold_day:
                if threshold_day <= 7:
                    cadence_week_sent["Week 1"] += 1
                    rep_cadence_week_sent[rep]["Week 1"] += 1
                elif threshold_day <= 14:
                    cadence_week_sent["Week 2"] += 1
                    rep_cadence_week_sent[rep]["Week 2"] += 1
                elif threshold_day <= 21:
                    cadence_week_sent["Week 3"] += 1
                    rep_cadence_week_sent[rep]["Week 3"] += 1
                else:
                    cadence_week_sent["Week 4+"] += 1
                    rep_cadence_week_sent[rep]["Week 4+"] += 1
            else:
                # Fallback: use created_at (not "timestamp") vs email_received_at
                ts = _parse_iso(ev.get("created_at", ""))
                if lead_rec and ts:
                    received = _parse_iso(lead_rec.get("email_received_at", ""))
                    if received:
                        age_at_reminder = (ts - received).days
                        if age_at_reminder <= 7:
                            cadence_week_sent["Week 1"] += 1
                            rep_cadence_week_sent[rep]["Week 1"] += 1
                        elif age_at_reminder <= 14:
                            cadence_week_sent["Week 2"] += 1
                            rep_cadence_week_sent[rep]["Week 2"] += 1
                        elif age_at_reminder <= 21:
                            cadence_week_sent["Week 3"] += 1
                            rep_cadence_week_sent[rep]["Week 3"] += 1
                        else:
                            cadence_week_sent["Week 4+"] += 1
                            rep_cadence_week_sent[rep]["Week 4+"] += 1
                    else:
                        cadence_week_sent["Week 1"] += 1  # safe default
                        rep_cadence_week_sent[rep]["Week 1"] += 1
                else:
                    cadence_week_sent["Week 1"] += 1  # safe default
                    rep_cadence_week_sent[rep]["Week 1"] += 1
        if atype == "ESCALATION_REMINDER":
            escalations_sent += 1
            total_reminders  += 1
            cadence_week_sent["Week 4+"] += 1
            rep_cadence_week_sent[rep]["Week 4+"] += 1
            rep_escalations_sent[rep] += 1


    quote_age_vals = [quote_age_hist.get(b, 0) for b in QUOTE_AGE_BUCKETS]
    gap_vals = [gap_hist.get(b, 0) for b in GAP_BUCKETS]


    # ── TAB 5: Won / Loss ─────────────────────────────────────────────────────

    won_loss_leads = [l for l in quip_enriched if l.get("current_status") == "WON_LOSS"]
    loss_count     = sum(1 for l in won_loss_leads if l.get("deal_outcome") != "WON")

    win_rate = (
        round(won_count / len(won_loss_leads) * 100)
        if won_loss_leads else None
    )

    CLOSE_AGE_BUCKETS = ["≤7 days", "8–14 days", "15–30 days", "31–60 days", "60+ days"]
    close_age_won  = {b: 0 for b in CLOSE_AGE_BUCKETS}
    close_age_lost = {b: 0 for b in CLOSE_AGE_BUCKETS}
    
    # Rep-specific close age data
    rep_close_age_won = defaultdict(lambda: {b: 0 for b in CLOSE_AGE_BUCKETS})
    rep_close_age_lost = defaultdict(lambda: {b: 0 for b in CLOSE_AGE_BUCKETS})
    
    for l in won_loss_leads:
        rep = (l.get("effective_rep_email") or l.get("assigned_rep_email") 
               or l.get("effective_rep_name") or l.get("assigned_rep_name") or "Unassigned")
        
        b = l.get("close_age_bucket")
        if b and b in close_age_won:
            if l.get("deal_outcome") == "WON":
                close_age_won[b]  += 1
                rep_close_age_won[rep][b] += 1
            else:
                close_age_lost[b] += 1
                rep_close_age_lost[rep][b] += 1

    close_ages = [l["days_to_close"] for l in won_loss_leads if l.get("days_to_close") is not None]
    avg_close_age = round(sum(close_ages) / len(close_ages), 1) if close_ages else None

    won_loss_detail = [
        {
            "gwc_id":          l.get("gwc_id",""),
            "company":         l.get("company_name","") or l.get("contact_name",""),
            "to_country":      l.get("to_country",""),
            "mode":            l.get("mode_of_freight",""),
            "outcome":         l.get("deal_outcome",""),
            "days_to_close":   l.get("days_to_close"),
            "rep":             l.get("assigned_rep_name","") or l.get("assigned_rep_email",""),
            "rep_key":         (l.get("effective_rep_email") or l.get("assigned_rep_email")
                                or l.get("effective_rep_name") or l.get("assigned_rep_name") or ""),
        }
        for l in won_loss_leads
    ]

    quote_age_labels = QUOTE_AGE_BUCKETS
    quote_age_vals = [quote_age_hist.get(b, 0) for b in QUOTE_AGE_BUCKETS]
    
    response_gap_labels = GAP_BUCKETS
    response_gap_vals = [gap_hist.get(b, 0) for b in GAP_BUCKETS]
    
    close_age_labels = CLOSE_AGE_BUCKETS
    close_age_won_json = json.dumps([close_age_won.get(b, 0) for b in CLOSE_AGE_BUCKETS])
    close_age_loss_json = json.dumps([close_age_lost.get(b, 0) for b in CLOSE_AGE_BUCKETS])

    # ── TAB 6: Rep Performance ────────────────────────────────────────────────

    rep_data = defaultdict(lambda: {
        "rep_email": "", "rep_name": "",
        "total": 0, "responded": 0, "quoted": 0, "won": 0, "lost": 0,
        "response_days_sum": 0, "response_days_count": 0,
        "quote_days_sum": 0,    "quote_days_count": 0,
    })

    for l in quip_enriched:
        # Use effective_rep resolved during enrichment — primary rep for the
        # country if routing hasn't explicitly assigned one yet.
        rep  = l.get("effective_rep_email", "Unassigned")
        name = l.get("effective_rep_name",  "Unassigned")
        st   = l.get("current_status", "")

        rep_data[rep]["rep_email"] = rep
        rep_data[rep]["rep_name"]  = name
        rep_data[rep]["total"]    += 1

        if st in ("ENGAGED", "QUOTED", "FOLLOW_UP", "WON_LOSS"):
            rep_data[rep]["responded"] += 1
            if l["days_to_response"] is not None:
                rep_data[rep]["response_days_sum"]   += l["days_to_response"]
                rep_data[rep]["response_days_count"] += 1

        if st in ("QUOTED", "FOLLOW_UP", "WON_LOSS"):
            rep_data[rep]["quoted"] += 1
            if l["days_engagement_to_quote"] is not None:
                rep_data[rep]["quote_days_sum"]   += l["days_engagement_to_quote"]
                rep_data[rep]["quote_days_count"] += 1

        if st == "WON_LOSS":
            if l.get("deal_outcome") == "WON":
                rep_data[rep]["won"]  += 1
            else:
                rep_data[rep]["lost"] += 1

    # ── Build poc_data: per-working-rep aggregation keyed by
    #    (primary_rep_email, working_rep_email) ────────────────────────────────
    # Working rep resolution priority:
    #   1. bd_poc_email (set by Phase 1 from Quip "GWC BD POC" column)
    #   2. detected_working_rep_email (set by Phase 3 from first thread sender)
    #   3. Falls back to the same primary rep (no POC drill-down for that lead)
    poc_data: dict = defaultdict(lambda: {
        "poc_email": "", "poc_name": "",
        "total": 0, "responded": 0, "quoted": 0, "won": 0, "lost": 0,
        "response_days_sum": 0, "response_days_count": 0,
        "quote_days_sum": 0,    "quote_days_count": 0,
    })

    for l in quip_enriched:
        primary = l.get("effective_rep_email", "Unassigned")
        poc_email = (
            l.get("bd_poc_email") or
            l.get("detected_working_rep_email") or
            ""
        ).strip()
        poc_name = (
            l.get("bd_poc_name") or
            l.get("detected_working_rep_name") or
            ""
        ).strip()

        if not poc_email or poc_email.lower() == primary.lower():
            continue  # no separate working rep for this lead

        key = (primary, poc_email)
        poc_data[key]["poc_email"] = poc_email
        poc_data[key]["poc_name"]  = poc_name or poc_email.split("@")[0]
        poc_data[key]["total"]    += 1
        st = l.get("current_status", "")

        if st in ("ENGAGED", "QUOTED", "FOLLOW_UP", "WON_LOSS"):
            poc_data[key]["responded"] += 1
            if l["days_to_response"] is not None:
                poc_data[key]["response_days_sum"]   += l["days_to_response"]
                poc_data[key]["response_days_count"] += 1

        if st in ("QUOTED", "FOLLOW_UP", "WON_LOSS"):
            poc_data[key]["quoted"] += 1
            if l["days_engagement_to_quote"] is not None:
                poc_data[key]["quote_days_sum"]   += l["days_engagement_to_quote"]
                poc_data[key]["quote_days_count"] += 1

        if st == "WON_LOSS":
            if l.get("deal_outcome") == "WON":
                poc_data[key]["won"]  += 1
            else:
                poc_data[key]["lost"] += 1

    # ── Group poc rows by primary rep ────────────────────────────────────────
    poc_by_primary: dict = defaultdict(list)
    for (primary, poc_email), d in poc_data.items():
        avg_resp  = round(d["response_days_sum"] / d["response_days_count"], 1) \
                    if d["response_days_count"] else None
        avg_quote = round(d["quote_days_sum"] / d["quote_days_count"], 1) \
                    if d["quote_days_count"] else None
        poc_by_primary[primary].append({
            "rep_email":         d["poc_email"],
            "rep_name":          d["poc_name"],
            "total":             d["total"],
            "responded":         d["responded"],
            "quoted":            d["quoted"],
            "won":               d["won"],
            "lost":              d["lost"],
            "avg_response_days": avg_resp,
            "avg_quote_days":    avg_quote,
            "response_rate_pct": round(d["responded"] / d["total"] * 100) if d["total"] else 0,
            "quote_rate_pct":    round(d["quoted"] / d["responded"] * 100) if d["responded"] else 0,
        })
    for pocs in poc_by_primary.values():
        pocs.sort(key=lambda r: -r["total"])

    rep_rows = []
    for rep, d in rep_data.items():
        avg_resp  = round(d["response_days_sum"] / d["response_days_count"], 1) \
                    if d["response_days_count"] else None
        avg_quote = round(d["quote_days_sum"]    / d["quote_days_count"],    1) \
                    if d["quote_days_count"]    else None
        rep_rows.append({
            "rep_email":          d["rep_email"],
            "rep_name":           d["rep_name"],
            "total":              d["total"],
            "responded":          d["responded"],
            "quoted":             d["quoted"],
            "won":                d["won"],
            "lost":               d["lost"],
            "avg_response_days":  avg_resp,
            "avg_quote_days":     avg_quote,
            "response_rate_pct":  round(d["responded"] / d["total"]      * 100) if d["total"]      else 0,
            "quote_rate_pct":     round(d["quoted"]    / d["responded"]   * 100) if d["responded"]  else 0,
            "poc_rows":           poc_by_primary.get(d["rep_email"], []),
        })
    # Exclude the synthetic "Unassigned" row — these are data-quality gaps,
    # not real reps, and should never appear in the leaderboard or charts.
    rep_rows = [r for r in rep_rows if r.get("rep_email", "") not in ("Unassigned", "")
                and r.get("rep_name", "") != "Unassigned"]
    rep_rows.sort(key=lambda r: -r["total"])

    # Enrich each rep row with the countries they are primary for.
    # Build rep_email → [country_name, ...] from the mapping (active, is_primary rows).
    rep_primary_countries: dict = defaultdict(list)
    try:
        for row in mapping_rows:
            if (str(row.get("is_primary", "")).upper() == "TRUE"
                    and str(row.get("active", "")).upper() == "TRUE"):
                email   = (row.get("rep_email") or "").strip().lower()
                country = (row.get("country_name") or "").strip()
                if email and country:
                    rep_primary_countries[email].append(country)
    except Exception:
        pass

    # Hardcode: Add Bahrain to Rafat AlZourgan (rafat.zourgan@gwclogistics.com)
    rafat_email = "rafat.zourgan@gwclogistics.com"
    if rafat_email.lower() not in rep_primary_countries or "Bahrain" not in rep_primary_countries[rafat_email.lower()]:
        if rafat_email.lower() not in rep_primary_countries:
            rep_primary_countries[rafat_email.lower()] = []
        rep_primary_countries[rafat_email.lower()].append("Bahrain")

    for r in rep_rows:
        countries = rep_primary_countries.get((r.get("rep_email") or "").lower(), [])
        r["countries"]       = countries
        r["country_display"] = " · ".join(countries) if countries else "—"

    # ── TAB 7: Data Quality ───────────────────────────────────────────────────

    # Per-field completeness across Quip non-REJECTED leads
    active_leads = [l for l in quip_enriched if l.get("current_status") != "REJECTED"]

    field_completeness = []
    for key, label in ALL_DISPLAY_FIELDS:
        filled = sum(
            1 for l in active_leads
            if (lambda v: bool(v.strip()) if isinstance(v, str) else v is not None)(l.get(key, ""))
        )
        total  = len(active_leads)
        pct    = round(filled / total * 100) if total else 0
        field_completeness.append({
            "key": key, "label": label,
            "filled": filled, "total": total, "pct": pct,
        })

    # MOT heatmap: for each MOT × required-field, show fill rate
    mot_groups = defaultdict(list)
    for l in active_leads:
        mot_groups[l.get("mode_of_freight", "") or "Unknown"].append(l)

    heatmap_data = []
    for mot, mot_leads in sorted(mot_groups.items()):
        required = UNIVERSAL_REQUIRED | MOT_EXTRA_REQUIRED.get(_mot_key(mot), set())
        row = {"mot": mot, "count": len(mot_leads), "fields": {}}
        for fk in sorted(required):
            filled = sum(
                1 for l in mot_leads
                if (l.get(fk, "") or "").strip()
            )
            row["fields"][fk] = round(filled / len(mot_leads) * 100) if mot_leads else 0
        heatmap_data.append(row)

    # Gap patterns: most common combinations of missing fields
    gap_pattern_counts = defaultdict(int)
    for l in active_leads:
        missing = tuple(sorted(l.get("fields_missing", [])))
        if missing:
            gap_pattern_counts[missing] += 1

    gap_patterns = [
        {"pattern": list(k), "count": v}
        for k, v in sorted(gap_pattern_counts.items(), key=lambda x: -x[1])
    ][:10]  # top 10

    # Summary stats for quality tab
    missing_counts = [len(l.get("fields_missing", [])) for l in active_leads]
    avg_missing    = round(sum(missing_counts) / len(missing_counts), 1) if missing_counts else 0
    fully_complete = sum(1 for l in active_leads if len(l.get("fields_missing", [])) == 0)
    pct_complete   = round(fully_complete / len(active_leads) * 100) if active_leads else 0

     # ── TAB 8: Notes Intelligence ─────────────────────────────────────────────
    _workspace = os.path.dirname(os.path.dirname(str(store.leads_path)))
    tab8_notes = _build_notes_intelligence(quip_enriched, _workspace)

    # ── Summary stats (used in header KPIs) ──────────────────────────────────

    return {
        # Meta
        "generated_at":         now.isoformat(),
        "total_leads":          len(enriched),        # ALL leads (filtered scope) — for Response Rate
        "all_leads_total":      _full_total,           # true unfiltered total — for Card 1 / Card 5
        "not_in_quip_count":    _full_total - quip_total - rejected_count,  # leads not matched in Quip
        "active_count":         active_count,          # ALL leads — for Response Rate denominator
        "no_action_count":      no_action_count,       # ALL leads — for Response Rate denominator
        "won_count":            won_count,             # Quip leads — Tab 5 win rate + WL donut
        "rejected_count":       rejected_count,        # ALL leads — for Response Rate denominator
        "avg_response_days":    avg_response_days,
        "avg_quote_age_days":   avg_quote_age,

        # Quip-scoped Tab 1 display values
        "quip_total":           quip_total,            # for "Not Matched in Quip" = total - quip_total
        "quip_funnel":          quip_funnel,           # drives funnel bars + WL donut segments
        "quip_active_count":    quip_active_count,     # Active Pipeline card
        "quip_no_action_count": quip_no_action_count,  # Unresponded card + Tab 3 Still Waiting

        # Tab 1 — Pipeline Overview (Quip arrivals + mode; all-leads funnel kept for resp_rate)
        "funnel":               funnel,                # ALL leads — Response Rate only
        "arrivals_series":      arrivals_series,       # Quip leads
        "mode_counts":          dict(mode_counts),     # Quip leads

        # Tab 2 — No Response
        "no_response_leads":       len(no_response_leads),
        "overdue_count":           overdue_count,
        "countries_affected":      countries_affected,
        "in_quip_count":           in_quip_count,
        "no_response_country_rows": no_response_country_rows,

        # Tab 3 — Engagement
        "response_hist":        response_hist,
        "response_buckets":     RESPONSE_BUCKETS,
        "cumulative_pct":       cumulative_pct,
        "mot_response":         {k: dict(v) for k, v in mot_response.items()},
        "rep_response_hist":    dict(rep_response_hist),
        "rep_cumulative_pct":   dict(rep_cumulative_pct),
        "rep_mot_response":     {k: dict(v) for k, v in rep_mot_response.items()},

        # Tab 4 — Quoting & Follow-Up
        "quote_age_buckets":    QUOTE_AGE_BUCKETS,
        "quote_age_hist":       quote_age_hist,
        "gap_buckets":          GAP_BUCKETS,
        "gap_hist":             gap_hist,
        "followup_age_buckets": FOLLOWUP_AGE_BUCKETS,
        "followup_age_hist":    followup_age_hist,
        "unique_followup":      unique_followup,
        "total_reminders":      total_reminders,
        "escalations_sent":     escalations_sent,
        "cadence_week_sent":    cadence_week_sent,
        "rep_quote_age_hist":   dict(rep_quote_age_hist),
        "rep_gap_hist":         dict(rep_gap_hist),
        "rep_followup_age_hist": dict(rep_followup_age_hist),
        "rep_cadence_week_sent": dict(rep_cadence_week_sent),

        # Tab 5 — Won / Loss
        "loss_count":           loss_count,
        "win_rate":             win_rate,
        "avg_close_age":        avg_close_age,
        "won_loss_detail":      won_loss_detail,
        "close_age_won":        close_age_won,
        "close_age_lost":       close_age_lost,
        "close_age_buckets":    CLOSE_AGE_BUCKETS,
        "rep_close_age_won":    dict(rep_close_age_won),
        "rep_close_age_lost":   dict(rep_close_age_lost),
        "rep_escalations_sent": dict(rep_escalations_sent),

        # Tab 6 — Rep Performance
        "rep_rows":             rep_rows,

        # Tab 7 — Data Quality
        "field_completeness":   field_completeness,
        "gap_patterns":         gap_patterns,
        "avg_missing_fields":   avg_missing,
        "pct_complete":         pct_complete,

        # Tab 8 — Notes Intelligence
        "notes_intelligence":   tab8_notes,

        # Raw leads (used by HTML for detail tables + date filter)
        "all_leads":            enriched,
        "quip_leads":           quip_enriched,
    }
