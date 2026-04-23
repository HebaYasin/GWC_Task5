"""
dashboard_html.py
-----------------
Generates a standalone GWC-branded HTML dashboard from live pipeline data.
Design exactly matches dashboard_plan.html: sticky green topbar, 7 tabs,
metric-card rows, pipeline funnel bars, country inaction bars, progress bars,
Chart.js visualisations, and a data-quality heatmap.

Usage:
    from dashboard_html import generate_dashboard
    html_path, json_path = generate_dashboard(store)
"""

import json
import sys
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_iso(ts: str):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _days_since(ts: str):
    dt = _parse_iso(ts)
    if not dt:
        return None
    return max(0, (datetime.now(timezone.utc) - dt).days)


def _age_color(avg_days):
    """Colour a country inaction bar by average age of NO_ACTION leads."""
    if avg_days is None:
        return "#B5B5B5"
    if avg_days < 3:
        return "#27AE60"
    if avg_days < 7:
        return "#F39C12"
    if avg_days < 14:
        return "#E67E22"
    return "#C0392B"


def _resp_color(avg_days):
    """Colour rep response time vs 5-day SLA."""
    if avg_days is None:
        return "#B5B5B5"
    if avg_days <= 3:
        return "#3FAE2A"
    if avg_days <= 5:
        return "#3FAE2ACC"
    if avg_days <= 7:
        return "#E67E22CC"
    return "#C0392B"


def _pill(text, cls):
    return f'<span class="pill p-{cls}">{text}</span>'


def _mot_pill(mot, cmode=""):
    """Render a mode-of-freight pill."""
    label = mot or "—"
    if mot == "Air":
        return _pill(label, "green")
    if mot in ("Sea", "Sea LCL", "Sea FCL"):
        suffix = f" {cmode}" if cmode and cmode not in ("Loose Cargo", "") else ""
        return _pill(f"Sea{suffix}", "blue")
    if mot == "Overland":
        return _pill(label, "warn")
    return _pill(label, "grey")


def _pct_color_var(pct):
    """Colour used for field-completeness bars.
    Red  (<20%)  = systemic gap — matches the systemic_gaps KPI threshold.
    Warn (20–80%) = partial coverage.
    Green (>80%) = healthy.
    """
    if pct > 80:
        return "var(--green)"
    if pct >= 20:
        return "var(--warn)"
    return "var(--red)"


def _hm_class(pct):
    if pct > 80:
        return "h-hi"
    if pct >= 50:
        return "h-md"
    return "h-lo"


# ─────────────────────────────────────────────────────────────────────────────
# Per-MOT completeness (heatmap — splits Sea into LCL vs FCL)
# ─────────────────────────────────────────────────────────────────────────────

HEATMAP_FIELDS = [
    ("incoterms",         "Incoterms"),
    ("packages",          "No. of Packages"),
    ("dimension_lwh",     "Dimensions (L×W×H)"),
    ("volume_m3",         "Volume (m³)"),
    ("chargeable_weight", "Chargeable Weight"),
    ("stackable",         "Stackable"),
    ("container_type",    "Container Type"),
    ("weight_kg",         "Weight (kg)"),
    ("product",           "Commodity"),
    ("from_country",      "Origin Country"),
    ("phone",             "Phone / WhatsApp"),
]

# Fields that are N/A (not required) for each MOT
HEATMAP_NA = {
    "Air":      {"container_type"},
    "Sea LCL":  {"container_type"},
    "Sea FCL":  {"volume_m3", "chargeable_weight", "stackable"},
    "Overland": {"volume_m3", "chargeable_weight", "stackable", "container_type"},
}


def _compute_mot_completeness(leads):
    """
    Returns ({mot_label: {field_key: pct}}, {mot_label: count}, {mot_label: leads})
    for Air, Sea LCL, Sea FCL, Overland, ALL. Excludes REJECTED leads.
    """
    groups = {"Air": [], "Sea LCL": [], "Sea FCL": [], "Overland": [], "ALL": []}
    for l in leads:
        if l.get("current_status") == "REJECTED":
            continue
        mot  = (l.get("mode_of_freight") or "").strip().title()
        cmod = (l.get("container_mode") or l.get("container_type") or "").strip().upper()
        if mot == "Air":
            groups["Air"].append(l)
        elif mot == "Sea":
            if "FCL" in cmod:
                groups["Sea FCL"].append(l)
            else:
                groups["Sea LCL"].append(l)
        elif mot == "Overland":
            groups["Overland"].append(l)
        groups["ALL"].append(l)

    result = {}
    for label, grp in groups.items():
        result[label] = {}
        for fk, _ in HEATMAP_FIELDS:
            if not grp:
                result[label][fk] = None
            else:
                filled = sum(1 for l in grp if str(l.get(fk) or "").strip() not in ("", "None"))
                result[label][fk] = round(filled / len(grp) * 100)
    return result, {k: len(v) for k, v in groups.items()}, groups


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def generate_dashboard(store, output_path: str = None) -> tuple:
    """
    Build the dashboard HTML and a companion JSON data file, write both to
    disk, and return (html_path, json_path).

    The JSON file (leads_dashboard_data.json) contains the raw output of
    build_dashboard_data() for full traceability.
    """
    workspace = str(Path(store.leads_path).parent.parent)
    sys.path.insert(0, f"{workspace}/skills/lead-dashboard/scripts")

    from dashboard_builder import build_dashboard_data
    data = build_dashboard_data(store)

    if not output_path:
        output_path = f"{workspace}/leads_dashboard.html"

    json_path = str(output_path).replace(".html", "_data.json")

    html = _build_v2_html(data)

    def _write(path, content):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return path
        except (PermissionError, FileNotFoundError, OSError):
            import re as _re
            session_root = _re.sub(r"/mnt/.*$", "", str(path))
            if not session_root or session_root == str(path):
                session_root = "/sessions/zen-cool-lamport"
            fallback = f"{session_root}/{Path(path).name}"
            with open(fallback, "w", encoding="utf-8") as f:
                f.write(content)
            return fallback

    output_path = _write(output_path, html)

    def _serialise(obj):
        if isinstance(obj, (set, tuple)):
            return list(obj)
        return str(obj)

    json_content = json.dumps(data, indent=2, default=_serialise, ensure_ascii=False)
    json_path = _write(json_path, json_content)

    return output_path, json_path


# ─────────────────────────────────────────────────────────────────────────────
# HTML builder — matches dashboard_plan.html exactly, with live data
# ─────────────────────────────────────────────────────────────────────────────

def _build_v2_html(data: dict) -> str:  # noqa: C901
    """Generate the full dashboard HTML from live pipeline data."""

    # ── 1. Extract ─────────────────────────────────────────────────────────
    leads         = data.get("all_leads", [])
    # ql = Quip-scoped leads — used for all Tab 2–8 inline computations.
    # NEVER fall back to all leads — if quip_leads is missing, use empty list
    # so tables show nothing rather than silently showing all 229 leads.
    ql            = data.get("quip_leads") or []
    if not ql and leads:
        # Fallback: filter all_leads to in_quip_sheet == YES
        ql = [l for l in leads if str(l.get("in_quip_sheet", "") or "").strip().upper() == "YES"]
    funnel        = data.get("funnel", {})         # ALL leads — Response Rate denominator only
    quip_funnel   = data.get("quip_funnel", funnel)  # Quip leads — funnel bars + WL donut
    now_str       = data.get("generated_at", "")[:10]
    total         = data.get("total_leads", 0)
    all_leads_total  = data.get("all_leads_total", total)   # true unfiltered total
    not_in_quip      = data.get("not_in_quip_count", 0)     # leads not matched in Quip
    rejected      = data.get("rejected_count", 0)
    active_count     = data.get("active_count", 0)      # ALL leads — Response Rate denominator
    no_action_count  = data.get("no_action_count", funnel.get("NO_ACTION", 0))  # ALL leads
    # Quip-scoped Tab 1 display values
    quip_total           = data.get("quip_total", 0)
    quip_active_count    = data.get("quip_active_count", active_count)
    quip_no_action_count = data.get("quip_no_action_count", funnel.get("NO_ACTION", 0))
    won_count        = data.get("won_count", 0)
    loss_count       = data.get("loss_count", 0)
    avg_resp         = data.get("avg_response_days")
    avg_quote_age    = data.get("avg_quote_age_days")
    avg_close_age    = data.get("avg_close_age")
    win_rate         = data.get("win_rate")

    # All-leads funnel slots — used ONLY for Response Rate computation
    no_action_n = funnel.get("NO_ACTION", 0)
    engaged_n   = funnel.get("ENGAGED", 0)
    quoted_n    = funnel.get("QUOTED", 0)
    followup_n  = funnel.get("FOLLOW_UP", 0)
    wonloss_n   = funnel.get("WON_LOSS", 0)
    # Quip funnel slots — used for funnel bars, WL donut, Active Pipeline card, etc.
    q_no_action_n = quip_funnel.get("NO_ACTION", 0)
    q_engaged_n   = quip_funnel.get("ENGAGED", 0)
    q_quoted_n    = quip_funnel.get("QUOTED", 0)
    q_followup_n  = quip_funnel.get("FOLLOW_UP", 0)
    q_wonloss_n   = quip_funnel.get("WON_LOSS", 0)
    # Colour constants exposed to Python template (used in legend dots)
    GY_HEX = "#B5B5B5"
    q_rejected_n  = quip_funnel.get("REJECTED", 0)

    non_rejected  = all_leads_total - rejected
    
    # resp_rate = ALL leads that got a reply / ALL non-rejected leads
    # responded_n   = engaged_n + quoted_n + followup_n + wonloss_n
    # resp_rate_pct = round((non_rejected-(q_rejected_n + (all_leads_total-in_quip_count-rejected))) / non_rejected * 100) if non_rejected else 0

    # ── Tab 2 ────────────────────────────────────────────────────────────────
    no_resp_count = data.get("no_response_leads", 0)
    overdue_count = data.get("overdue_count", 0)
    countries_n   = data.get("countries_affected", 0)
    country_rows  = data.get("no_response_country_rows", [])
    
    in_quip_count = data.get("in_quip_count", 0)
    not_in_quip_count = data.get("not_in_quip_count", 0)
    resp_rate_pct = round((q_engaged_n+q_quoted_n+q_followup_n+q_wonloss_n) / (in_quip_count-q_rejected_n) * 100) if in_quip_count - q_rejected_n != 0 else 0
    nr_leads = [l for l in ql
                if l.get("current_status") == "NO_ACTION"
                and l.get("classification") != "REJECTED"]
    nr_ages  = [l["lead_age_days"] for l in nr_leads
                if l.get("lead_age_days") is not None]
    avg_nr_age = round(sum(nr_ages) / len(nr_ages), 1) if nr_ages else 0

    # JS KPI lookup keyed by destination country (one entry per country_row)
    t2_js_country_data = json.dumps({
        r["country"]: {
            "count":   r["count"],
            "overdue": r.get("overdue_count", 0),
            "avg_age": r.get("avg_age_days"),
            "in_quip": r.get("in_quip_count", 0),
            "rep":     r.get("rep_name", "Unroutable"),
        }
        for r in country_rows
        if r.get("country")
    })
    t2_js_totals = json.dumps({
        "count":     no_resp_count,
        "overdue":   overdue_count,
        "countries": countries_n,
        "avg_age":   avg_nr_age,
        "in_quip":   in_quip_count,
    })

    # Per-rep KPIs for Tab 2 cards
    t2_rep_kpis = {}
    for r in country_rows:
        rep = r["rep_name"]
        if rep not in t2_rep_kpis:
            t2_rep_kpis[rep] = {"total": 0, "overdue": 0, "ages": []}
        t2_rep_kpis[rep]["total"] += r["count"]
        t2_rep_kpis[rep]["overdue"] += r["overdue_count"]
        if r["avg_age_days"] is not None:
            t2_rep_kpis[rep]["ages"].append(r["avg_age_days"] * r["count"])  # weighted avg
    for rep, d in t2_rep_kpis.items():
        total = d["total"]
        if total > 0:
            avg_age = sum(d["ages"]) / total
            d["avg_age"] = round(avg_age, 1)
        else:
            d["avg_age"] = None

    # Add global totals for "All Reps Countries"
    t2_rep_kpis[""] = {
        "total": no_resp_count,
        "overdue": overdue_count,
        "avg_age": avg_nr_age
    }

    t2_rep_kpis_js = json.dumps(t2_rep_kpis)

    # ── Tab 3 ────────────────────────────────────────────────────────────────
    resp_buckets   = data.get("response_buckets", [])
    resp_hist      = data.get("response_hist", {})
    cumulative_pct = data.get("cumulative_pct", [])
    mot_response   = data.get("mot_response", {})

    total_engaged = sum(1 for l in ql
                        if l.get("current_status") in ("ENGAGED", "QUOTED", "FOLLOW_UP", "WON_LOSS"))
    day01_pct = 0
    if total_engaged:
        day01_pct = round(
            (resp_hist.get("Same day", 0) + resp_hist.get("Day 1", 0)) / total_engaged * 100
        )

    # # Aggregate MOT response into 4 day-buckets and convert to %
    # mot_datasets = {}
    # MOT_COLORS_MAP = {"Air": "#3FAE2A", "Sea": "#00ABC7", "Overland": "#E67E22"}
    # for mot, buckets in mot_response.items():
    #     tot_mot = sum(buckets.values()) if isinstance(buckets, dict) else buckets

    #     if not tot_mot:
    #         continue
    #     sd   = buckets.get("Same day", 0)
    #     d13  = sum(buckets.get(f"Day {i}", 0) for i in range(1, 4))
    #     d47  = sum(buckets.get(f"Day {i}", 0) for i in range(4, 8))
    #     d8p  = sum(buckets.get(f"Day {i}", 0) for i in range(8, 16)) + buckets.get("Day 15+", 0)
    #     mot_datasets[mot] = {
    #         "data":  [round(sd/tot_mot*100), round(d13/tot_mot*100),
    #                   round(d47/tot_mot*100), round(d8p/tot_mot*100)],
    #         "color": MOT_COLORS_MAP.get(mot, "#9C27B0"),
    #     }

    # Use sum of responded-bucket counts as denominator so percentages reflect
    # distribution of responded leads across buckets (matches per-rep semantics
    # at lines 558-571 and the chart label "% of Leads Engaged Within Each Day Bucket").
    mot_datasets = {}
    MOT_COLORS_MAP = {"Air": "#3FAE2A", "Sea": "#00ABC7", "Overland": "#E67E22"}
    for mot, buckets in mot_response.items():
        mot_clean = mot.strip().title()
        if mot_clean == "Unknown":
            continue
        tot_mot = sum(buckets.values())
        if not tot_mot:
            continue
        sd   = buckets.get("Same day", 0)
        d13  = sum(buckets.get(f"Day {i}", 0) for i in range(1, 4))
        d47  = sum(buckets.get(f"Day {i}", 0) for i in range(4, 8))
        d8p  = sum(buckets.get(f"Day {i}", 0) for i in range(8, 16)) + buckets.get("Day 15+", 0)
        mot_datasets[mot_clean] = {
            "data":  [round(sd/tot_mot*100), round(d13/tot_mot*100),
                      round(d47/tot_mot*100), round(d8p/tot_mot*100)],
            "color": MOT_COLORS_MAP.get(mot_clean, "#9C27B0"),
        }

    # # Calculate average response days for each MOT (for horizontal summary chart)
    # mot_avg_response = {}
    # for mot, buckets in mot_response.items():
    #     if not buckets:
    #         continue
    #     # Weighted average: Same day=0, Day 1=1, Day 2=2, ..., Day 15+=15
    #     mot_bucket_total = sum(buckets.values())
    #     if mot_bucket_total:
    #         weighted_sum = 0
    #         for day_label, count in buckets.items():
    #             if day_label == "Same day":
    #                 weighted_sum += 0 * count
    #             elif day_label == "Day 15+":
    #                 weighted_sum += 15 * count
    #             elif day_label.startswith("Day "):
    #                 try:
    #                     day_num = int(day_label.split()[1])
    #                     weighted_sum += day_num * count
    #                 except:
    #                     pass
    #         mot_avg_response[mot] = round(weighted_sum / mot_bucket_total, 1)
    
    # # Sort by average response (fastest first)
    # mot_sorted = sorted(mot_avg_response.items(), key=lambda x: x[1])
    # mot_response_labels_js = json.dumps([m for m, _ in mot_sorted])
    # mot_response_data_js   = json.dumps([d for _, d in mot_sorted])


    # Weighted avg response days per MOT from mot_response buckets.
    # "Same day"=0, "Day N"=N, "Day 15+"=15. Using the aggregated buckets keeps
    # this chart consistent with the histogram and includes timing-anomaly
    # same-day responses (first_response_at set but days_to_response==None).
    def _bucket_days(label: str) -> int:
        if label == "Same day":
            return 0
        if label == "Day 15+":
            return 15
        if label.startswith("Day "):
            try: return int(label.split()[1])
            except Exception: return 0
        return 0

    mot_avg_response = {}
    for mot, buckets in mot_response.items():
        mot_clean = mot.strip().title()
        if mot_clean == "Unknown":
            continue
        tot = sum(buckets.values())
        if not tot:
            continue
        weighted = sum(_bucket_days(lbl) * cnt for lbl, cnt in buckets.items())
        mot_avg_response[mot_clean] = round(weighted / tot, 1)

    # Sort by average response (fastest first)
    mot_sorted = sorted(mot_avg_response.items(), key=lambda x: x[1])
    mot_response_labels_js = json.dumps([m for m, _ in mot_sorted])
    mot_response_data_js   = json.dumps([d for _, d in mot_sorted])

    # ── Tab 4 ────────────────────────────────────────────────────────────────
    quote_age_buckets    = data.get("quote_age_buckets", [])
    quote_age_hist       = data.get("quote_age_hist", {})
    gap_buckets          = data.get("gap_buckets", [])
    gap_hist             = data.get("gap_hist", {})
    followup_age_buckets = data.get("followup_age_buckets", [])
    followup_age_hist    = data.get("followup_age_hist", {})
    unique_followup      = data.get("unique_followup", 0)
    total_reminders      = data.get("total_reminders", 0)
    escalations_sent     = data.get("escalations_sent", 0)
    cadence_week_sent    = data.get("cadence_week_sent", {})
    avg_rem_per_lead     = round(total_reminders / unique_followup, 1) if unique_followup else 0

    # ── Tab 5 ────────────────────────────────────────────────────────────────
    won_loss_detail  = data.get("won_loss_detail", [])
    close_age_won    = data.get("close_age_won", {})
    close_age_lost   = data.get("close_age_lost", {})
    close_age_buckets = data.get("close_age_buckets", [])
    
    # Rep-specific data for Tabs 3, 4, 5
    # Use `or {}` (not just default) to guard against builder emitting explicit None.
    rep_response_hist     = data.get("rep_response_hist")     or {}
    rep_cumulative_pct    = data.get("rep_cumulative_pct")    or {}
    rep_mot_response      = data.get("rep_mot_response")      or {}
    rep_quote_age_hist    = data.get("rep_quote_age_hist")    or {}
    rep_gap_hist          = data.get("rep_gap_hist")          or {}
    rep_followup_age_hist = data.get("rep_followup_age_hist") or {}
    rep_cadence_week_sent = data.get("rep_cadence_week_sent") or {}

    # Fallback: recompute per-rep tab 4 hists from ql when builder emits empty dicts.
    _Q_AGE_BUCKETS  = ["0\u20133 days", "4\u20137 days", "8\u201314 days", "15\u201330 days", "30+ days"]
    _GAP_BUCKETS    = ["Same day", "Day 1", "Day 2", "Day 3", "Day 4", "Day 5", "Day 6", "Day 7", "Day 7+"]
    _FU_AGE_BUCKETS = ["1\u20137 days", "8\u201314 days", "15\u201321 days", "22\u201328 days", "28+ days"]

    def _rep_key(l):
        return (l.get("effective_rep_email") or l.get("assigned_rep_email")
                or l.get("effective_rep_name") or l.get("assigned_rep_name") or "Unassigned")

    if not rep_quote_age_hist:
        rep_quote_age_hist = {}
        for _l in ql:
            _b = _l.get("quote_age_bucket")
            if _b and _l.get("quote_sent_at") and _b in _Q_AGE_BUCKETS:
                _rk = _rep_key(_l)
                rep_quote_age_hist.setdefault(_rk, {x: 0 for x in _Q_AGE_BUCKETS})
                rep_quote_age_hist[_rk][_b] += 1

    if not rep_gap_hist:
        rep_gap_hist = {}
        for _l in ql:
            _d = _l.get("days_engagement_to_quote")
            if _d is None:
                continue
            _rk = _rep_key(_l)
            rep_gap_hist.setdefault(_rk, {x: 0 for x in _GAP_BUCKETS})
            if _d == 0:
                rep_gap_hist[_rk]["Same day"] += 1
            elif _d <= 7:
                rep_gap_hist[_rk][f"Day {_d}"] += 1
            else:
                rep_gap_hist[_rk]["Day 7+"] += 1

    if not rep_followup_age_hist:
        rep_followup_age_hist = {}
        for _l in ql:
            if _l.get("current_status") != "FOLLOW_UP":
                continue
            _age = _l.get("lead_age_days") or 0
            _rk  = _rep_key(_l)
            rep_followup_age_hist.setdefault(_rk, {x: 0 for x in _FU_AGE_BUCKETS})
            if _age <= 7:
                rep_followup_age_hist[_rk]["1\u20137 days"]   += 1
            elif _age <= 14:
                rep_followup_age_hist[_rk]["8\u201314 days"]  += 1
            elif _age <= 21:
                rep_followup_age_hist[_rk]["15\u201321 days"] += 1
            elif _age <= 28:
                rep_followup_age_hist[_rk]["22\u201328 days"] += 1
            else:
                rep_followup_age_hist[_rk]["28+ days"]        += 1
    rep_close_age_won  = data.get("rep_close_age_won")  or {}
    rep_close_age_lost = data.get("rep_close_age_lost") or {}

    # Fallback: recompute per-rep close-age hists from ql when builder emits empty dicts.
    if not rep_close_age_won and not rep_close_age_lost:
        _CA_BUCKETS = close_age_buckets or []
        for _l in ql:
            if _l.get("current_status") != "WON_LOSS":
                continue
            _rk = _rep_key(_l)
            _b  = _l.get("close_age_bucket")
            if not _b or _b not in _CA_BUCKETS:
                continue
            _outcome = _l.get("deal_outcome", "")
            if _outcome == "WON":
                rep_close_age_won.setdefault(_rk, {x: 0 for x in _CA_BUCKETS})
                rep_close_age_won[_rk][_b] += 1
            elif _outcome == "LOST":
                rep_close_age_lost.setdefault(_rk, {x: 0 for x in _CA_BUCKETS})
                rep_close_age_lost[_rk][_b] += 1

    # Fallback: builder may emit empty rep_response_hist / rep_cumulative_pct
    # (e.g. regenerating from a JSON snapshot that predates them). Recompute
    # from ql so Tab 3's Response Histogram + Cumulative % filters work.
    _RESP_BUCKETS_ORDER = ["Same day"] + [f"Day {i}" for i in range(1, 16)] + ["Day 15+"]
    if not rep_response_hist:
        rep_response_hist = {}
        for _l in ql:
            _rk = (_l.get("effective_rep_email") or _l.get("assigned_rep_email")
                   or _l.get("effective_rep_name") or _l.get("assigned_rep_name") or "Unassigned")
            _b = _l.get("response_bucket")
            if not _b and _l.get("first_response_at"):
                _b = "Same day"  # timing-anomaly same-day responses
            if not _b:
                continue
            rep_response_hist.setdefault(_rk, {x: 0 for x in _RESP_BUCKETS_ORDER})
            if _b in rep_response_hist[_rk]:
                rep_response_hist[_rk][_b] += 1

    if not rep_cumulative_pct:
        rep_cumulative_pct = {}
        for _rk, _hist in rep_response_hist.items():
            _tot = sum(_hist.values())
            if not _tot:
                continue
            _cum = 0
            _arr = []
            for _b in _RESP_BUCKETS_ORDER:
                _cum += _hist.get(_b, 0)
                _arr.append(round(_cum / _tot * 100, 1))
            rep_cumulative_pct[_rk] = _arr

    # # Create rep options for dropdowns — prefer per-rep histogram keys,
    # # fall back to effective_rep_email values from ql when those dicts are empty
    # # (e.g. when regenerating from a JSON snapshot that predates per-rep histograms).
    # all_reps = set()
    # for d in [rep_response_hist, rep_quote_age_hist, rep_gap_hist,
    #           rep_followup_age_hist, rep_cadence_week_sent,
    #           rep_close_age_won, rep_close_age_lost]:
    #     all_reps.update(d.keys())
    # # Always seed from ql so filters populate even when histogram dicts are empty
    # _EMAIL_RE = __import__('re').compile(r'^[^@]+@[^@]+\.[^@]+$')
    # for _l in ql:
    #     _rk = (_l.get("effective_rep_email") or _l.get("assigned_rep_email")
    #            or _l.get("effective_rep_name") or _l.get("assigned_rep_name") or "")
    #     if _rk and _rk not in ("Unassigned",) and _EMAIL_RE.match(_rk):
    #         all_reps.add(_rk)
    # all_reps = sorted([r for r in all_reps if r and r not in ("Unassigned",) and _EMAIL_RE.match(r)])
    # rep_options = "".join(f'<option value="{r}">{r}</option>' for r in all_reps)
        # Build email→name map and dropdown options (value=email, label=name).
    _EMAIL_RE = __import__('re').compile(r'^[^@]+@[^@]+\.[^@]+$')
    _rep_email_to_name = {}
    for _l in ql:
        _em = (_l.get("effective_rep_email") or _l.get("assigned_rep_email") or "")
        _nm = (_l.get("effective_rep_name") or _l.get("assigned_rep_name") or "")
        if _em and _EMAIL_RE.match(_em) and _em not in ("Unassigned",):
            _rep_email_to_name.setdefault(_em, _nm or _em)

    all_reps = set(_rep_email_to_name.keys())
    for d in [rep_response_hist, rep_quote_age_hist, rep_gap_hist,
              rep_followup_age_hist, rep_cadence_week_sent,
              rep_close_age_won, rep_close_age_lost]:
        for k in d.keys():
            if k and _EMAIL_RE.match(k) and k not in ("Unassigned",):
                all_reps.add(k)
                _rep_email_to_name.setdefault(k, k)

    all_reps = sorted(all_reps, key=lambda e: _rep_email_to_name.get(e, e))
    rep_options = "".join(
        f'<option value="{e}">{_rep_email_to_name.get(e, e)}</option>'
        for e in all_reps
    )

    # ── Per-rep KPIs for Tab 3 / 4 / 5 filters ──────────────────────────────
    _rep_escalations = data.get("rep_escalations_sent", {})
    _rep_lead_groups = defaultdict(list)
    for _l in ql:
        _rk = (_l.get("effective_rep_email") or _l.get("assigned_rep_email")
               or _l.get("effective_rep_name") or _l.get("assigned_rep_name") or "Unassigned")
        _rep_lead_groups[_rk].append(_l)

    # Tab 3 per-rep KPIs
    rep_t3_kpis = {"": {"engaged": total_engaged, "day01_pct": day01_pct,
                        "avg_resp": avg_resp, "no_action": quip_no_action_count}}
    for _rk, _rleads in _rep_lead_groups.items():
        _eng  = sum(1 for l in _rleads if l.get("current_status") in ("ENGAGED","QUOTED","FOLLOW_UP","WON_LOSS"))
        _hist = rep_response_hist.get(_rk, {})
        _d01  = _hist.get("Same day", 0) + _hist.get("Day 1", 0)
        _d01p = round(_d01 / _eng * 100) if _eng else 0
        _rdys = [l["days_to_response"] for l in _rleads if l.get("days_to_response") is not None]
        _ar   = round(sum(_rdys) / len(_rdys), 1) if _rdys else None
        _na   = sum(1 for l in _rleads if l.get("current_status") == "NO_ACTION")
        rep_t3_kpis[_rk] = {"engaged": _eng, "day01_pct": _d01p, "avg_resp": _ar, "no_action": _na}

    # Tab 4 per-rep KPIs
    rep_t4_kpis = {"": {"followup": unique_followup, "reminders": total_reminders,
                        "escalations": escalations_sent, "avg_rem": avg_rem_per_lead}}
    for _rk, _rleads in _rep_lead_groups.items():
        _fu_leads = [l for l in _rleads if l.get("current_status") == "FOLLOW_UP"]
        _fu_cos   = {(l.get("company_name") or l.get("contact_name") or "").strip().lower()
                     for l in _fu_leads if (l.get("company_name") or l.get("contact_name") or "").strip()}
        _fu_count = len(_fu_cos)
        _rems     = sum(rep_cadence_week_sent.get(_rk, {}).values())
        _escs     = _rep_escalations.get(_rk, 0)
        _avg_r    = round(_rems / _fu_count, 1) if _fu_count else 0
        rep_t4_kpis[_rk] = {"followup": _fu_count, "reminders": _rems,
                             "escalations": _escs, "avg_rem": _avg_r}

    # Tab 5 per-rep KPIs
    rep_t5_kpis = {"": {"won": won_count, "lost": loss_count, "win_rate": win_rate,
                        "avg_close": avg_close_age, "active": quip_active_count,
                        "no_action": q_no_action_n + q_rejected_n}}
    for _rk, _rleads in _rep_lead_groups.items():
        _wl    = [l for l in _rleads if l.get("current_status") == "WON_LOSS"]
        _w     = sum(1 for l in _wl if l.get("deal_outcome") == "WON")
        _l_cnt = len(_wl) - _w
        _wr    = round(_w / len(_wl) * 100) if _wl else None
        _closes = [l["days_to_close"] for l in _wl if l.get("days_to_close") is not None]
        _ac    = round(sum(_closes) / len(_closes), 1) if _closes else None
        _act   = sum(1 for l in _rleads if l.get("current_status") in ("ENGAGED","QUOTED","FOLLOW_UP"))
        _no_act = sum(1 for l in _rleads if l.get("current_status") in ("NO_ACTION","REJECTED","PENDING"))
        rep_t5_kpis[_rk] = {"won": _w, "lost": _l_cnt, "win_rate": _wr,
                             "avg_close": _ac, "active": _act, "no_action": _no_act}

    rep_t3_kpis_js = json.dumps(rep_t3_kpis)
    rep_t4_kpis_js = json.dumps(rep_t4_kpis)
    rep_t5_kpis_js = json.dumps(rep_t5_kpis)

    # Per-rep MOT grouped-bar chart data (overrides empty REP_MOT_RESP from builder)
    _MOT_COLORS = {"Air": "#3FAE2A", "Sea": "#00ABC7", "Overland": "#E67E22"}
    _rep_mot_chart = {}
    _rep_mot_avg_resp = {}
    for _rk, _rleads in _rep_lead_groups.items():
        _mot_buckets = defaultdict(lambda: defaultdict(int))
        _mot_days_list = defaultdict(list)
        _ENGAGED_STATUSES = {"ENGAGED", "QUOTED", "FOLLOW_UP", "WON_LOSS"}
        for _l in _rleads:
            if (_l.get("days_to_response") is not None
                    and _l.get("current_status") in _ENGAGED_STATUSES):
                _mot = (_l.get("mode_of_freight") or "Unknown").strip().title()

                
                _b   = _l.get("response_bucket")
                if _b:
                    _mot_buckets[_mot][_b] += 1
                _mot_days_list[_mot].append(_l["days_to_response"])
        # grouped-bar data
        _ds = {}
        for _mot, _bkts in _mot_buckets.items():
            _tot = sum(_bkts.values())
            if not _tot:
                continue
            _sd  = _bkts.get("Same day", 0)
            _d13 = sum(_bkts.get(f"Day {i}", 0) for i in range(1, 4))
            _d47 = sum(_bkts.get(f"Day {i}", 0) for i in range(4, 8))
            _d8p = sum(_bkts.get(f"Day {i}", 0) for i in range(8, 16)) + _bkts.get("Day 15+", 0)
            _ds[_mot] = {
                "data":  [round(_sd/_tot*100), round(_d13/_tot*100),
                          round(_d47/_tot*100), round(_d8p/_tot*100)],
                "color": _MOT_COLORS.get(_mot, "#9C27B0"),
            }
        if _ds:
            _rep_mot_chart[_rk] = _ds
        # avg-response summary data
        _avgs = {
            _mot: round(sum(_days)/_len, 1)
            for _mot, _days in _mot_days_list.items()
            if (_len := len(_days)) and _mot != "Unknown"
        }
        if _avgs:
            _sorted_avgs = sorted(_avgs.items(), key=lambda x: x[1])
            _rep_mot_avg_resp[_rk] = {
                "lbls": [m for m, _ in _sorted_avgs],
                "data": [d for _, d in _sorted_avgs],
            }

    # Override the (possibly empty) builder value with the lead-derived computation
    rep_mot_resp_js     = json.dumps(_rep_mot_chart)
    rep_mot_avg_resp_js = json.dumps(_rep_mot_avg_resp)

    # ── Tab 6 ────────────────────────────────────────────────────────────────
    rep_rows      = data.get("rep_rows", [])
    assigned_reps = [r for r in rep_rows if r.get("rep_email") not in ("Unassigned", "") and r.get("rep_name", "") != "Unassigned"]
    # rep email → list of countries they serve (for country-level cadence rollup)
    _rep_countries_map = {
        r["rep_email"]: r.get("countries") or []
        for r in assigned_reps if r.get("rep_email")
    }
    # country → list of rep emails that serve it
    _country_reps_map: dict = {}
    for _em, _cs in _rep_countries_map.items():
        for _c in _cs:
            _country_reps_map.setdefault(_c, []).append(_em)
    active_reps   = len(assigned_reps)
    avg_resp_str  = f"{avg_resp}d" if avg_resp is not None else "—"
    tot_responded = sum(r.get("responded", 0) for r in assigned_reps)
    tot_quoted2   = sum(r.get("quoted", 0)    for r in assigned_reps)
    quote_rate_pct = round(tot_quoted2 / tot_responded * 100) if tot_responded else 0
    reps_below_sla = sum(1 for r in assigned_reps
                         if (r.get("avg_response_days") or 0) > 5)

    # Per-rep status counts for volume chart (Quip leads only)
    # Include NO_ACTION so the full team portfolio is visible.
    rep_status: dict = {}
    for l in ql:
        rep = (l.get("effective_rep_email") or l.get("assigned_rep_email")
               or l.get("effective_rep_name") or l.get("assigned_rep_name")
               or "Unassigned")
        st  = (l.get("current_status", "") or "").strip().upper()
        if rep not in rep_status:
            rep_status[rep] = {"NO_ACTION": 0, "ENGAGED": 0, "QUOTED": 0, "FOLLOW_UP": 0}
        if st in rep_status[rep]:
            rep_status[rep][st] += 1

    # Country → ISO 3166-1 alpha-2 code for flagcdn.com flag images
    _FLAG_ISO = {
        "ksa": "sa", "saudi arabia": "sa", "kingdom of saudi arabia": "sa",
        "qatar": "qa",
        "uae": "ae", "united arab emirates": "ae",
        "oman": "om",
        "bahrain": "bh",
        "kuwait": "kw",
    }

    def _flag_img(iso: str, label: str = "") -> str:
        """Return an <img> tag for a country flag from flagcdn.com."""
        return (
            f'<img src="https://flagcdn.com/w20/{iso}.png" '
            f'width="18" height="13" '
            f'style="border-radius:2px;vertical-align:middle;margin-right:3px" '
            f'alt="{label}" title="{label}">'
        )

    def _flags(countries: list) -> str:
        """Return flag <img> tags for a list of country names, deduplicated."""
        seen, out = set(), []
        for c in countries:
            iso = _FLAG_ISO.get(c.lower().strip(), "")
            if iso and iso not in seen:
                seen.add(iso)
                out.append(_flag_img(iso, c))
        return "".join(out)

    chart_rep_labels: list = []
    chart_rep_na:     list = []
    chart_rep_eng:    list = []
    chart_rep_quot:   list = []
    chart_rep_fu:     list = []
    chart_rep_resp:   list = []
    for r in assigned_reps[:8]:
        raw_name = (r.get("rep_name") or r.get("rep_email") or "Unknown").split("@")[0]
        parts = raw_name.strip().split()
        short_name     = f"{parts[0][0]}. {parts[-1]}" if len(parts) > 1 else raw_name
        country_display = r.get("country_display", "—")
        # Chart.js renders array labels as multi-line ticks
        chart_rep_labels.append([short_name, country_display])
        rep_key = r.get("rep_email") or r.get("rep_name") or "Unassigned"
        rc = rep_status.get(rep_key, {})
        chart_rep_na.append(rc.get("NO_ACTION", 0))
        chart_rep_eng.append(rc.get("ENGAGED", 0))
        chart_rep_quot.append(rc.get("QUOTED", 0))
        chart_rep_fu.append(rc.get("FOLLOW_UP", 0))
        chart_rep_resp.append(r.get("avg_response_days") or 0)

    # ── Tab 7 ────────────────────────────────────────────────────────────────
    field_completeness = data.get("field_completeness", [])
    gap_patterns       = data.get("gap_patterns", [])
    avg_missing        = data.get("avg_missing_fields", 0)
    pct_complete       = data.get("pct_complete", 0)
    systemic_gaps      = sum(1 for f in field_completeness if f.get("pct", 100) < 20)
    sorted_fields      = sorted(field_completeness, key=lambda x: x["pct"])
    most_common_gap    = sorted_fields[0]["label"] if sorted_fields and sorted_fields[0]["pct"] < 80 else "—"

    mot_completeness, mot_counts, groups = _compute_mot_completeness(ql)

    # ── Tab 8 ────────────────────────────────────────────────────────────────
    t8          = data.get("notes_intelligence", {})
    t8_total    = t8.get("total_active", 0)
    t8_filled   = t8.get("notes_filled_count", 0)
    t8_empty    = t8.get("notes_empty_count", 0)
    t8_fill_rate   = t8.get("notes_fill_rate", 0)
    t8_avg_score   = t8.get("avg_score")
    t8_score_dist  = t8.get("score_dist", {})
    t8_len_bkts    = t8.get("length_buckets", [])
    t8_len_dist    = t8.get("length_dist", {})
    t8_group_pres  = t8.get("group_presence", {})
    t8_mot_scores  = t8.get("mot_avg_score", {})
    t8_country_sc  = t8.get("country_avg_score", [])
    t8_worst       = t8.get("worst_samples", [])
    t8_best        = t8.get("best_samples", [])
    t8_cov_dist    = t8.get("coverage_dist", {})
    t8_top_kw      = t8.get("top_keywords", [])
    t8_avg_s       = f"{t8_avg_score:.1f}/5" if t8_avg_score else "—"
    t8_poor_pct    = round((t8_score_dist.get(1,0) + t8_score_dist.get(2,0)) / t8_total * 100) if t8_total else 0
    t8_good_count  = t8_score_dist.get(4,0) + t8_score_dist.get(5,0)

    def _sc(v):  # score → CSS color
        if v is None: return "var(--light)"
        if v >= 4: return "var(--green)"
        if v >= 3: return "var(--blue)"
        if v >= 2: return "var(--warn)"
        return "var(--red)"

    # Worst notes table rows
    t8_worst_html = ""
    for s in t8_worst:
        sc = _sc(s.get("score", 0))
        t8_worst_html += (
            f'<tr><td><small style="color:#aaa">{s["gwc_id"]}</small><br>'
            f'<strong>{s["company"] or "—"}</strong></td>'
            f'<td>{_mot_pill(s["mot"])}</td>'
            f'<td>{s["to_country"] or "—"}</td>'
            f'<td style="font-style:italic;color:#555;max-width:300px">"{s["notes"]}"</td>'
            f'<td style="text-align:center"><span style="font-size:17px;font-weight:700;color:{sc}">{s["score"]}</span></td>'
            f'<td style="font-size:11px;color:var(--grey)">{s["feedback"]}</td></tr>\n'
        )
    if not t8_worst_html:
        t8_worst_html = '<tr><td colspan="6" style="text-align:center;color:#aaa;padding:18px">No score-1 leads with notes found yet</td></tr>'

    # Best notes table rows
    t8_best_html = ""
    for s in t8_best:
        sc = _sc(s.get("score", 0))
        t8_best_html += (
            f'<tr style="background:var(--green-t)">'
            f'<td><strong>{s["company"] or "—"}</strong></td>'
            f'<td style="font-style:italic;color:#333;max-width:360px">"{s["notes"]}"</td>'
            f'<td style="text-align:center"><span style="font-size:17px;font-weight:700;color:{sc}">{s["score"]}</span></td>'
            f'<td style="font-size:11px">{s["feedback"]}</td></tr>\n'
        )
    if not t8_best_html:
        t8_best_html = '<tr><td colspan="4" style="text-align:center;color:#aaa;padding:18px">No score 4–5 leads found yet</td></tr>'

    # Domain group presence bars
    t8_group_html = ""
    for gname, stats in t8_group_pres.items():
        pct = stats.get("pct", 0)
        col = _pct_color_var(pct)
        t8_group_html += (
            f'<div style="margin:10px 0">'
            f'<div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px">'
            f'<span style="font-weight:600">{gname}</span>'
            f'<span style="font-weight:700;color:{col}">{pct}% of notes</span></div>'
            f'<div style="background:#f0f0f0;border-radius:4px;height:10px;overflow:hidden">'
            f'<div style="width:{pct}%;background:{col};height:100%;border-radius:4px"></div></div>'
            f'</div>\n'
        )

    # Country score rows
    t8_country_html = ""
    for r in t8_country_sc:
        stars = "★" * int(r["avg"]) + "☆" * (5 - int(r["avg"]))
        t8_country_html += (
            f'<tr><td>{r["country"]}</td><td>{r["count"]}</td>'
            f'<td><span style="font-weight:700;color:{_sc(r["avg"])}">{r["avg"]}</span> '
            f'<small style="color:#ccc">{stars}</small></td></tr>\n'
        )
    if not t8_country_html:
        t8_country_html = '<tr><td colspan="3" style="color:#aaa;text-align:center">No data</td></tr>'

    # Keyword cloud HTML
    t8_kw_html = ""
    for kw, cnt in t8_top_kw[:16]:
        intensity = min(1.0, cnt / max(c for _, c in t8_top_kw[:1] or [(kw,1)]))
        t8_kw_html += (
            f'<span style="display:inline-block;background:var(--blue-t);border:1px solid var(--blue);'
            f'color:#005f75;border-radius:12px;padding:3px 11px;margin:3px 2px;'
            f'font-size:{10 + int(intensity*5)}px;font-weight:600">'
            f'{kw} <strong>({cnt})</strong></span>'
        )
    if not t8_kw_html:
        t8_kw_html = '<span style="color:#aaa">No keywords detected yet — add freight_domain_knowledge.md to lead-status-tracker/references/</span>'

    # Insight
    if t8_poor_pct > 50:
        t8_insight = (f'⚠ <strong>{t8_poor_pct}% of leads have low-quality notes (score 1–2).</strong> '
                      f'Share the "Worst Notes" samples below with Extensia as negative training examples in the next coaching session.')
    elif t8_fill_rate < 60:
        t8_insight = (f'⚠ <strong>Only {t8_fill_rate}% of leads have notes filled at all.</strong> '
                      f'Extensia agents must always capture ETD, customs preference, and any special requirements during the qualification call.')
    else:
        t8_insight = (f'✓ Notes fill rate is {t8_fill_rate}% with avg quality {t8_avg_s}. '
                      f'Focus coaching on upgrading score-2 leads to score-3+ by prompting agents to always mention ETD, customs status, and incoterms preference.')

    # JS data
    t8_score_lbl_js = json.dumps([f"Score {i}" for i in range(1, 6)])
    t8_score_dat_js = json.dumps([t8_score_dist.get(i, 0) for i in range(1, 6)])
    t8_len_lbl_js   = json.dumps(t8_len_bkts)
    t8_len_dat_js   = json.dumps([t8_len_dist.get(b, 0) for b in t8_len_bkts])
    t8_mot_lbl_js   = json.dumps(list(t8_mot_scores.keys()))
    t8_mot_dat_js   = json.dumps(list(t8_mot_scores.values()))
    t8_cov_lbl_js   = json.dumps([f"{i} group{'s' if i != 1 else ''}" for i in range(0, 7)])
    t8_cov_dat_js   = json.dumps([t8_cov_dist.get(i, 0) for i in range(0, 7)])

    # Marketing Insights chart data
    t8_scatter_age_js    = json.dumps(t8.get("scatter_age", []))
    t8_outcome_js        = json.dumps({
        str(i): t8.get("outcome_by_score", {}).get(i, {"Won": 0, "Lost": 0})
        for i in range(1, 6)
    })
    t8_missing_js        = json.dumps(t8.get("top_missing_fields", []))
    # t8_sentiment_js = json.dumps({
    #     str(i): t8.get("sentiment_by_score", {}).get(i, {"positive": 0, "needs_info": 0, "other": 0})
    #     for i in range(1, 6)
    # })
    sentiment_data = t8.get("sentiment_by_score", {})
    t8_sentiment_js = json.dumps({
        str(i): (sentiment_data.get(i) or sentiment_data.get(str(i)) or {"positive": 0, "technical_gap": 0, "mismatch": 0, "other": 0})
        for i in range(1, 6)
    })
    t8_radar_labels_js   = json.dumps(list(t8.get("group_presence", {}).keys()))
    t8_radar_data_js     = json.dumps([v.get("pct", 0) for v in t8.get("group_presence", {}).values()])


    # ── 2. Funnel bars HTML ──────────────────────────────────────────────────
    status_descriptions = {
        "NO ACTION": "Leads that have not received any response from reps yet",
        "ENGAGED": "Leads that have received at least one response from a rep",
        "QUOTED": "Leads that have been sent a formal quote or proposal",
        "FOLLOW UP": "Leads requiring additional follow-up or nurturing",
        "WON LOSS": "Leads that have been won or lost (closed deals)",
        "REJECTED": "Leads that were rejected as unqualified or incomplete"
    }
    
    funnel_spec = [
        ("NO ACTION",  q_no_action_n,  "#C0392B", "0.75"),
        ("ENGAGED",    q_engaged_n,    "#3FAE2A", "1"),
        ("QUOTED",     q_quoted_n,     "#1565C0", "1"),
        ("FOLLOW UP",  q_followup_n,   "#9C27B0", "0.8"),
        ("WON LOSS",   q_wonloss_n,    "#607D8B", "1"),
        ("REJECTED",   q_rejected_n,   "#cccccc", "1"),
    ]
    funnel_max = max((c for _, c, _, _ in funnel_spec), default=1) or 1
    funnel_html = ""
    for label, count, color, opacity in funnel_spec:
        pct_w = max(0, round(count / funnel_max * 90)) if count else 0
        op_style = f";opacity:{opacity}" if opacity != "1" else ""
        desc = status_descriptions.get(label, "")
        funnel_html += (
            f'      <div class="funnel-row">'
            f'<div class="f-label tooltip">{label}<span class="tooltip-text">{desc}</span></div>'
            f'<div class="f-bar-wrap"><div class="f-bar" style="width:{pct_w}%;background:{color}{op_style}"></div></div>'
            f'<div class="f-count">{count}</div>'
            f'</div>\n'
        )

    # ── 3. Country bars HTML (Tab 2) — grouped by rep, one row per country ──────
    max_country_count = max((r["count"] for r in country_rows), default=1) or 1
    country_bars_html = ""
    prev_rep = None
    for r in country_rows:
        rep   = r.get("rep_name", "Unroutable")
        dest  = r["country"]
        # Emit a rep group header whenever the rep changes
        if rep != prev_rep:
            if prev_rep is not None:
                country_bars_html += '      <div style="margin-bottom:6px"></div>\n'
            country_bars_html += (
                f'      <div class="rep-group-header" data-rep="{rep}" '
                f'style="font-size:11px;font-weight:700;color:#1B5E20;letter-spacing:.5px;'
                f'text-transform:uppercase;padding:6px 0 3px 4px;border-bottom:1px solid #e8f5e9;'
                f'margin-bottom:2px">{rep}</div>\n'
            )
            prev_rep = rep
        pct_w = max(5, round(r["count"] / max_country_count * 85))
        color = _age_color(r.get("avg_age_days"))
        cnt   = r["count"]
        label = f'{cnt} lead{"s" if cnt != 1 else ""}'
        avg_d = r.get("avg_age_days")
        avg_s = f"{avg_d} days" if avg_d is not None else "?"
        country_bars_html += (
            f'      <div class="country-bar-row" data-country="{rep}" data-dest="{dest}">'
            f'<div class="cbl tooltip" style="padding-left:14px">{dest}<span class="tooltip-text">{dest}: {cnt} unresponded lead{"s" if cnt != 1 else ""}<br>Avg age: {avg_s}<br>Assigned to: {rep}</span></div>'
            f'<div class="cbw"><div class="cbf" style="width:{pct_w}%;background:{color}">{label}</div></div>'
            f'<div class="cbc">avg <strong style="color:{color}">{avg_s}</strong></div>'
            f'</div>\n'
        )

    insight_country = ""  # Will be dynamically generated by JavaScript

    # ── 4. NO_ACTION leads table (Tab 2) ────────────────────────────────────
    no_action_sorted = sorted(
        [l for l in ql
         if l.get("current_status") == "NO_ACTION"
         and l.get("classification") != "REJECTED"],
        key=lambda x: -(x.get("lead_age_days") or 0),
    )

    def _age_style(age):
        if age is None:
            return ""
        if age >= 14:
            return 'style="color:var(--red)"'
        if age >= 7:
            return 'style="color:var(--warn)"'
        if age >= 3:
            return 'style="color:#F39C12"'
        return ""

    # Dropdown: reps present in the mapping, sorted alpha
    t2_countries = sorted(set(r["rep_name"] for r in country_rows if r.get("rep_name")))
    t2_country_opts = "".join(
        f'<option value="{c}">{c}</option>' for c in t2_countries
    )

    # Per-rep KPIs for Tab 2 cards
    t2_rep_kpis = {}
    for r in country_rows:
        rep = r["rep_name"]
        if rep not in t2_rep_kpis:
            t2_rep_kpis[rep] = {"total": 0, "overdue": 0, "ages": []}
        t2_rep_kpis[rep]["total"] += r["count"]
        t2_rep_kpis[rep]["overdue"] += r["overdue_count"]
        if r["avg_age_days"] is not None:
            t2_rep_kpis[rep]["ages"].append(r["avg_age_days"] * r["count"])  # weighted avg
    for rep, d in t2_rep_kpis.items():
        total = d["total"]
        if total > 0:
            avg_age = sum(d["ages"]) / total
            d["avg_age"] = round(avg_age, 1)
        else:
            d["avg_age"] = None

    # Add global totals for "All Reps Countries"
    t2_rep_kpis[""] = {
        "total": no_resp_count,
        "overdue": overdue_count,
        "avg_age": avg_nr_age
    }

    no_action_rows_html = ""
    # Build destination country → rep name lookup.
    # Each country_row has "country" = destination country, "rep_name" = rep.
    dest_to_rep = {
        row["country"]: row.get("rep_name", "Unroutable")
        for row in country_rows
        if row.get("country")
    }
    for l in no_action_sorted:
        age      = l.get("lead_age_days")
        age_str  = f"{age} day{'s' if age != 1 else ''}" if age is not None else "?"
        age_st   = _age_style(age)
        age_html = f"<strong {age_st}>{age_str}</strong>" if age_st else age_str
        company  = l.get("company_name", "") or l.get("contact_name", "") or "—"
        # Use canonical country (quip_country → assigned_country → to_country)
        # to avoid city names like "Riyadh" appearing instead of "KSA"
        dest = (
            l.get("quip_country") or
            l.get("assigned_country") or
            l.get("to_country") or
            "—"
        ).strip() or "—"
        mot      = l.get("mode_of_freight", "") or "—"
        cmode    = l.get("container_mode", "") or l.get("container_type", "") or ""
        clf      = l.get("classification", "") or "—"
        miss_raw = l.get("missing_fields_list", [])
        if isinstance(miss_raw, str):
            try:
                miss_raw = json.loads(miss_raw)
            except Exception:
                miss_raw = []
        miss_str = (
            ", ".join(miss_raw[:2]) + (f" +{len(miss_raw)-2}" if len(miss_raw) > 2 else "")
            if miss_raw else "—"
        )
        clf_pill = (_pill("PARTIAL", "warn") if "PARTIAL" in clf.upper()
                    else _pill("QUALIFIED", "green") if "QUALIFIED" in clf.upper()
                    else _pill(clf, "grey"))

        summary    = str(l.get("quip_updates_summary", "") or "").strip()
        summary_td = (f'<span style="font-size:11px;color:#555;font-style:italic">{summary}</span>'
                      if summary else '<span style="color:#ccc;font-size:11px">—</span>')

        
        
        no_action_rows_html += (
            f'        <tr data-country="{dest_to_rep.get(dest, "Unroutable")}">'
            f"<td>{l.get('gwc_id','')}</td>"
            f"<td>{company}</td>"
            f"<td>{dest}</td>"
            f"<td>{_mot_pill(mot, cmode)}</td>"
            f"<td>{age_html}</td>"
            f"<td>{clf_pill}</td>"
            f"<td>{miss_str}</td>"
            f"<td style='max-width:260px'>{summary_td}</td>"
            f"</tr>\n"
        )
    if not no_action_rows_html:
        no_action_rows_html = '<tr><td colspan="8" style="text-align:center;color:#aaa;padding:20px">No unresponded leads</td></tr>'
    
    
    # ── 5. WON_LOSS table (Tab 5) ────────────────────────────────────────────
    wl_rows_html = ""
    for d in won_loss_detail:
        outcome  = d.get("outcome", "")
        is_won   = outcome == "WON"
        out_pill = _pill("WON ✓", "green") if is_won else _pill("LOST ✗", "red")
        row_bg   = 'style="background:var(--green-t)"' if is_won else 'style="background:var(--red-t)"'
        days_c   = d.get("days_to_close")
        days_s   = f"{days_c} days" if days_c is not None else "—"
        mot      = d.get("mode", "") or "—"
        _wl_rep_key = d.get('rep_key', '') or ''
        wl_rows_html += (
            f"        <tr {row_bg} data-rep=\"{_wl_rep_key}\">"
            f"<td>{d.get('gwc_id','')}</td>"
            f"<td>{d.get('company','') or '—'}</td>"
            f"<td>{d.get('to_country','') or '—'}</td>"
            f"<td>{_mot_pill(mot)}</td>"
            f"<td>{out_pill}</td>"
            f"<td>{days_s}</td>"
            f"<td>{d.get('rep','') or '—'}</td>"
            f"</tr>\n"
        )

    # ── 6. Rep leaderboard table (Tab 6) ─────────────────────────────────────
    rep_table_html = ""
    for r in assigned_reps:
        name  = r.get("rep_name", "") or r.get("rep_email", "Unknown")
        avg_r = r.get("avg_response_days")
        avg_q = r.get("avg_quote_days")
        avg_q_s = f"{avg_q}d" if avg_q is not None else "—"
        if avg_r is None:
            resp_pill = _pill("—", "grey")
        elif avg_r <= 3:
            resp_pill = _pill(f"{avg_r}d", "green")
        elif avg_r <= 5:
            resp_pill = _pill(f"{avg_r}d", "blue")
        elif avg_r <= 7:
            resp_pill = _pill(f"{avg_r}d", "warn")
        else:
            resp_pill = _pill(f"{avg_r}d", "red")
        resp_c    = _resp_color(avg_r)
        rr_pct    = r.get("response_rate_pct", 0)
        resp_bar  = (
            f'<div style="background:#f0f0f0;border-radius:3px;height:8px;width:120px">'
            f'<div style="background:{resp_c};height:100%;border-radius:3px;width:{rr_pct}%"></div>'
            f'</div>'
        )

        poc_rows  = r.get("poc_rows", [])
        has_drill = len(poc_rows) > 0

        # Safe CSS class ID derived from rep email (letters + digits only)
        import re as _re
        poc_id = _re.sub(r"[^a-zA-Z0-9]", "_", r.get("rep_email", name))

        # ── Primary rep row ───────────────────────────────────────────────────
        drill_badge = (
            f' <span id="poc-btn-{poc_id}" data-count="{len(poc_rows)}"'
            f' style="font-size:10px;color:#1B5E20;font-weight:600;'
            f'background:#e8f5e9;border-radius:4px;padding:1px 5px;'
            f'cursor:pointer">▶ {len(poc_rows)} rep{"s" if len(poc_rows)>1 else ""}</span>'
        ) if has_drill else ""

        onclick = (
            f' onclick="togglePoc(\'{poc_id}\')" style="cursor:pointer"'
        ) if has_drill else ""

        countries     = r.get("countries", [])
        flag_str      = _flags(countries)
        country_disp  = r.get("country_display", "")
        country_sub   = (
            f'<br><span style="font-size:10px;color:#888;font-weight:400">'
            f'{flag_str} </span>'
        ) if country_disp and country_disp != "—" else ""

        rep_table_html += (
            f'        <tr{onclick}>'
            f'<td><strong>{name}</strong>{country_sub}{drill_badge}</td>'
            f'<td>{r.get("total",0)}</td>'
            f'<td>{r.get("responded",0)} ({rr_pct}%)</td>'
            f'<td>{r.get("quoted",0)}</td>'
            f'<td><strong style="color:var(--green)">{r.get("won",0)}</strong></td>'
            f'<td>{resp_pill}</td>'
            f'<td>{avg_q_s}</td>'
            f'<td>{resp_bar}</td>'
            f'</tr>\n'
        )

        # ── POC sub-rows (hidden by default, toggled by togglePoc JS) ─────────
        # Keys are rep_name / rep_email as stored by dashboard_builder.py
        for p in poc_rows:
            p_name  = p.get("rep_name", "") or p.get("rep_email", "—")
            p_email = p.get("rep_email", "")
            p_total = p.get("total", 0)
            p_resp  = p.get("responded", 0)
            p_avg_r = p.get("avg_response_days")
            p_avg_q = p.get("avg_quote_days")
            p_won   = p.get("won", 0)
            p_avg_r_s = f"{p_avg_r}d" if p_avg_r is not None else "—"
            p_avg_q_s = f"{p_avg_q}d" if p_avg_q is not None else "—"
            p_rr_pct  = p.get("response_rate_pct", 0)
            p_rc      = _resp_color(p_avg_r)
            if p_avg_r is None:
                p_resp_pill = _pill("—", "grey")
            elif p_avg_r <= 3:
                p_resp_pill = _pill(p_avg_r_s, "green")
            elif p_avg_r <= 5:
                p_resp_pill = _pill(p_avg_r_s, "blue")
            elif p_avg_r <= 7:
                p_resp_pill = _pill(p_avg_r_s, "warn")
            else:
                p_resp_pill = _pill(p_avg_r_s, "red")
            p_bar = (
                f'<div style="background:#f0f0f0;border-radius:3px;height:6px;width:100px">'
                f'<div style="background:{p_rc};height:100%;border-radius:3px;width:{p_rr_pct}%"></div>'
                f'</div>'
            )
            rep_table_html += (
                f'        <tr class="poc-row-{poc_id}" style="display:none;background:#f0faf3">'
                f'<td style="padding-left:26px;font-size:12px;color:#2e7d32">'
                f'↳ <em>{p_name}</em>'
                + (f'<br><span style="font-size:10px;color:#aaa">{p_email}</span>' if p_email else "")
                + f'</td>'
                f'<td style="font-size:12px">{p_total}</td>'
                f'<td style="font-size:12px">{p_resp} ({p_rr_pct}%)</td>'
                f'<td style="font-size:12px">{p.get("quoted",0)}</td>'
                f'<td style="font-size:12px"><strong style="color:var(--green)">{p_won}</strong></td>'
                f'<td style="font-size:12px">{p_resp_pill}</td>'
                f'<td style="font-size:12px">{p_avg_q_s}</td>'
                f'<td>{p_bar}</td>'
                f'</tr>\n'
            )

    # ── 7. Field completeness progress bars (Tab 7) ──────────────────────────
    field_bars_html = ""
    for f in field_completeness:
        pct   = f.get("pct", 0)
        color = _pct_color_var(pct)
        field_bars_html += (
            f'      <div class="prog-row">'
            f'<div class="prog-header"><span>{f["label"]}</span>'
            f'<span style="font-weight:700;color:{color}">{pct}%</span></div>'
            f'<div class="prog-bar"><div class="prog-fill" style="width:{pct}%;background:{color}"></div></div>'
            f'</div>\n'
        )

    # ── 8. Heatmap (Tab 7) ───────────────────────────────────────────────────
    mot_cols = ["Air", "Sea LCL", "Sea FCL", "Overland", "ALL"]
    heatmap_header = ""
    for col in mot_cols:
        cnt = mot_counts.get(col, 0)
        heatmap_header += (
            f'<th>{col}<br>'
            f'<small style="font-weight:400;opacity:.7">{cnt} leads</small></th>'
        )

    heatmap_rows_html = ""
    for fk, flabel in HEATMAP_FIELDS:
        cells = ""
        for col in mot_cols:
            pct_val = mot_completeness.get(col, {}).get(fk)
            na_set  = HEATMAP_NA.get(col, set()) if col != "ALL" else set()
            if pct_val is None or fk in na_set:
                cells += '<td class="h-na">N/A</td>'
            else:
                grp = groups.get(col, [])
                grp_total = len(grp)
                filled = sum(1 for l in grp if str(l.get(fk) or "").strip() not in ("", "None"))
                cells += f'<td class="{_hm_class(pct_val)}" data-pct="{pct_val}" data-count="{filled}" data-total="{grp_total}">{pct_val}%</td>'
        heatmap_rows_html += (
            f'          <tr>'
            f'<td style="text-align:left;padding:7px 10px;font-weight:600">{flabel}</td>'
            f'{cells}'
            f'</tr>\n'
        )

    # ── 9. Gap patterns table (Tab 7) ────────────────────────────────────────
    active_lead_count = len([l for l in leads if l.get("current_status") != "REJECTED"])
    gap_table_html = ""
    for gp in gap_patterns[:10]:
        pattern     = gp.get("pattern", [])
        count       = gp.get("count", 0)
        pct_all     = round(count / active_lead_count * 100) if active_lead_count else 0
        fields_str  = " · ".join(pattern) if pattern else "—"
        # Determine which MOTs share this exact missing-field pattern
        pattern_set = set(pattern)
        mots_with   = {
            l.get("mode_of_freight", "") or "Unknown"
            for l in leads
            if l.get("current_status") != "REJECTED"
            and set(l.get("fields_missing", [])) == pattern_set
        }
        mot_str     = ", ".join(sorted(mots_with)) if mots_with else "—"
        if pct_all >= 50:
            row_bg = 'style="background:var(--red-t)"'
        elif pct_all >= 20:
            row_bg = 'style="background:var(--warn-t)"'
        else:
            row_bg = ""
        gap_table_html += (
            f"        <tr {row_bg}>"
            f"<td><strong>{fields_str}</strong></td>"
            f"<td style=\"text-align:right\"><strong>{count}</strong></td>"
            f"<td style=\"text-align:right\">{pct_all}%</td>"
            f"<td>{mot_str}</td>"
            f"<td>{_pill('Form gap', 'red')}</td>"
            f"</tr>\n"
        )

    # ── 10. Insight strings ──────────────────────────────────────────────────
    # Tab 3
    if mot_datasets:
        fastest = max(mot_datasets, key=lambda m: mot_datasets[m]["data"][0])
        slowest = min(mot_datasets, key=lambda m: mot_datasets[m]["data"][0])
        t3_insight = (
            f'✓ {fastest} leads are responded to fastest '
            f'({mot_datasets[fastest]["data"][0]}% same-day). '
            f'{slowest} is slowest — consider reviewing the SLA for {slowest} shipments.'
        )
    else:
        t3_insight = "No response data yet. Run Phase 3 to track email thread activity."

    # Tab 5
    if won_loss_detail:
        won_mots = [d.get("mode", "") for d in won_loss_detail if d.get("outcome") == "WON" and d.get("mode")]
        t5_insight = f'✓ {win_rate or 0}% win rate on confirmed deals.'
        if won_mots:
            top_mot = Counter(won_mots).most_common(1)[0][0]
            t5_insight += f' {top_mot} leads account for the most wins.'
    else:
        t5_insight = "No WON_LOSS deals recorded yet."

    # Tab 7
    if systemic_gaps:
        t7_insight = (
            f'⚠ <strong>{systemic_gaps} field{"s" if systemic_gaps != 1 else ""} have &lt;20% '
            f'completion across all leads.</strong> These are likely not collected by the HubSpot '
            f'agency form — a systemic intake gap, not a rep issue. Recommend updating the '
            f'HubSpot form template to capture these fields before the lead arrives.'
        )
    else:
        t7_insight = "✓ No systemic field gaps detected. Data quality looks good."

    # ── 11. JS data (JSON-embedded) ──────────────────────────────────────────
    arrivals       = data.get("arrivals_series", [])
    arr_labels_js  = json.dumps([
        datetime.strptime(a["date"], "%Y-%m-%d").strftime("%d-%b") 
        for a in arrivals
    ])
    arr_data_js    = json.dumps([a["count"]    for a in arrivals])

    mode_counts_d  = data.get("mode_counts", {})
    mode_labels_js = json.dumps(list(mode_counts_d.keys()))
    mode_data_js   = json.dumps(list(mode_counts_d.values()))

    resp_hist_js   = json.dumps([resp_hist.get(b, 0) for b in resp_buckets])
    resp_bkts_js   = json.dumps(resp_buckets)
    cumul_js       = json.dumps(cumulative_pct)
    mot_chart_js   = json.dumps(mot_datasets)

    q_age_lbl_js   = json.dumps(quote_age_buckets)
    q_age_dat_js   = json.dumps([quote_age_hist.get(b, 0) for b in quote_age_buckets])
    gap_lbl_js     = json.dumps(gap_buckets)
    gap_dat_js     = json.dumps([gap_hist.get(b, 0) for b in gap_buckets])
    fu_lbl_js      = json.dumps(followup_age_buckets)
    fu_dat_js      = json.dumps([followup_age_hist.get(b, 0) for b in followup_age_buckets])

    cad_keys = ["Week 1", "Week 2", "Week 3", "Week 4+"]
    cad_lbl_js = json.dumps(["W1 (Days 1–7)", "W2 (Days 8–14)", "W3 (Day 21)", "W4+ (Escalation)"])
    cad_dat_js = json.dumps([cadence_week_sent.get(k, 0) for k in cad_keys])

    t2_rep_kpis_js = json.dumps(t2_rep_kpis)

    # Donut segments: Won | Lost | Active (engaged+) | Pending/Rejected (Bug 7 fix)
    wl_donut_js  = json.dumps([won_count, loss_count, q_engaged_n + q_quoted_n + q_followup_n, q_no_action_n + q_rejected_n])
    cl_lbl_js    = json.dumps(close_age_buckets)
    cl_won_js    = json.dumps([close_age_won.get(b, 0)  for b in close_age_buckets])
    cl_lost_js   = json.dumps([close_age_lost.get(b, 0) for b in close_age_buckets])

    rep_lbl_js   = json.dumps(chart_rep_labels)
    rep_na_js    = json.dumps(chart_rep_na)
    rep_eng_js   = json.dumps(chart_rep_eng)
    rep_quot_js  = json.dumps(chart_rep_quot)
    rep_fu_js    = json.dumps(chart_rep_fu)
    rep_resp_js  = json.dumps(chart_rep_resp)

    # Rep-specific data for filtering
    rep_resp_hist_js = json.dumps(rep_response_hist)
    rep_cumul_pct_js = json.dumps(rep_cumulative_pct)
    # rep_mot_resp_js = json.dumps(rep_mot_response)
    rep_q_age_hist_js = json.dumps(rep_quote_age_hist)
    rep_gap_hist_js = json.dumps(rep_gap_hist)
    rep_fu_age_hist_js = json.dumps(rep_followup_age_hist)
    rep_cad_week_js      = json.dumps(rep_cadence_week_sent)
    rep_countries_map_js = json.dumps(_rep_countries_map)
    country_reps_map_js  = json.dumps(_country_reps_map)
    rep_cl_won_js = json.dumps(rep_close_age_won)
    rep_cl_lost_js = json.dumps(rep_close_age_lost)

    # ── 12. Derived display strings ──────────────────────────────────────────
    avg_close_s   = f"{avg_close_age}d" if avg_close_age is not None else "—"
    win_rate_s    = f"{win_rate}%"       if win_rate      is not None else "—"
    avg_quote_s   = f"{avg_quote_age}d"  if avg_quote_age is not None else "—"
    avg_resp_s    = f"{avg_resp}d"       if avg_resp      is not None else "—"
    avg_miss_s    = str(avg_missing)
    pct_compl_s   = str(pct_complete)

    def _pct(num, den):
        return round(num / den * 100) if den else 0

    # ── 13. Build HTML ───────────────────────────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GWC Lead Dashboard — {now_str}</title>
<link rel="stylesheet" href="https://use.typekit.net/wtc0foh.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root {{
  --green:#3FAE2A; --blue:#00ABC7; --dark:#333; --grey:#555;
  --light:#B5B5B5; --bg:#F5F5F5; --red:#C0392B; --warn:#E67E22;
  --green-t:#E8F5E6; --blue-t:#E0F7FA; --red-t:#FDECEA; --warn-t:#FFF3CD;
  --purple:#9C27B0; --purple-t:#F3E5F5;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:"proxima-nova","Proxima Nova",Arial,sans-serif;background:var(--bg);color:var(--grey);font-size:14px}}
.topbar{{background:var(--green);color:#fff;padding:0 28px;display:flex;align-items:center;justify-content:space-between;height:52px;position:sticky;top:0;z-index:100;box-shadow:0 2px 8px rgba(0,0,0,.2)}}
.topbar .logo{{font-size:18px;font-weight:700;letter-spacing:-.3px}}
.topbar .logo span{{opacity:.75;font-weight:400;font-size:13px;margin-left:8px}}
.badge{{background:rgba(255,255,255,.2);border-radius:12px;padding:3px 12px;font-size:11px;font-weight:700}}
.tabs{{display:flex;background:#fff;border-bottom:2px solid #e0e0e0;padding:0 28px;overflow-x:auto;scrollbar-width:none}}
.tabs::-webkit-scrollbar{{display:none}}
.tab-btn{{padding:13px 18px;border:none;background:none;cursor:pointer;font-size:12px;font-weight:700;color:var(--light);border-bottom:3px solid transparent;margin-bottom:-2px;white-space:nowrap;transition:all .15s}}
.tab-btn:hover{{color:var(--grey)}}
.tab-btn.active{{color:var(--green);border-bottom-color:var(--green)}}
.tab-btn.purple.active{{color:var(--purple);border-bottom-color:var(--purple)}}
.content{{padding:24px 28px;max-width:1400px; margin:0 auto}}
.tab-panel{{display:none}}.tab-panel.active{{display:block}}
.info-bar{{font-size:12px;margin-bottom:18px;padding:10px 14px;border-radius:4px;line-height:1.6}}
.info-bar.blue{{background:var(--blue-t);border-left:4px solid var(--blue);color:#006064}}
.info-bar.red{{background:var(--red-t);border-left:4px solid var(--red);color:#7B1818}}
.info-bar.purple{{background:var(--purple-t);border-left:4px solid var(--purple);color:#4A148C}}
.info-bar.green{{background:var(--green-t);border-left:4px solid var(--green);color:#1B5E20}}
.metric-row{{display:flex;gap:14px;margin-bottom:22px;flex-wrap:wrap}}
.metric-card{{flex:1;min-width:130px;background:#fff;border-radius:8px;padding:16px 18px;box-shadow:0 1px 4px rgba(0,0,0,.07);border-top:4px solid var(--green)}}
.metric-card.blue{{border-top-color:var(--blue)}}
.metric-card.warn{{border-top-color:var(--warn)}}
.metric-card.red{{border-top-color:var(--red)}}
.metric-card.purple{{border-top-color:var(--purple)}}
.metric-card.grey{{border-top-color:var(--light)}}
.metric-card .val{{font-size:30px;font-weight:700;color:var(--green);line-height:1}}
.metric-card.blue .val{{color:var(--blue)}}
.metric-card.warn .val{{color:var(--warn)}}
.metric-card.red .val{{color:var(--red)}}
.metric-card.purple .val{{color:var(--purple)}}
.metric-card.grey .val{{color:var(--grey)}}
.metric-card .label{{font-size:11px;color:var(--light);margin-top:5px;text-transform:uppercase;letter-spacing:.5px;font-weight:600}}
.metric-card .sub{{font-size:11px;color:#aaa;margin-top:3px}}
.section-hdr{{font-size:11px;font-weight:700;color:var(--green);text-transform:uppercase;letter-spacing:.7px;margin:26px 0 12px;padding-bottom:6px;border-bottom:2px solid var(--green)}}
.section-hdr.purple{{color:var(--purple);border-bottom-color:var(--purple)}}
.section-hdr.red{{color:var(--red);border-bottom-color:var(--red)}}
.section-hdr.blue{{color:var(--blue);border-bottom-color:var(--blue)}}
.chart-grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:20px}}
.chart-grid.three{{grid-template-columns:1fr 1fr 1fr}}
.chart-grid.single{{grid-template-columns:1fr}}
.chart-card{{background:#fff;border-radius:8px;padding:18px 20px;box-shadow:0 1px 4px rgba(0,0,0,.07)}}
.chart-card.new{{border:2px dashed #CE93D8}}
.chart-card h3{{font-size:12px;font-weight:700;color:var(--dark);margin-bottom:3px}}
.chart-card .sub{{font-size:11px;color:#aaa;margin-bottom:14px}}
.chart-wrap{{position:relative;height:230px}}
.chart-wrap.tall{{height:290px}}
.chart-wrap.short{{height:170px}}
.funnel-row{{display:flex;align-items:center;gap:10px;margin:7px 0}}
.funnel-row .f-label{{width:100px;font-size:11px;font-weight:700;color:var(--grey);text-align:right}}
.funnel-row .f-bar-wrap{{flex:1;background:#f0f0f0;border-radius:4px;overflow:hidden;height:22px}}
.funnel-row .f-bar{{height:100%;border-radius:4px;transition:width .5s ease}}
.funnel-row .f-count{{width:36px;font-size:13px;font-weight:700;color:var(--dark)}}
.prog-row{{margin:7px 0}}
.prog-header{{display:flex;justify-content:space-between;font-size:11px;margin-bottom:3px}}
.prog-bar{{width:100%;background:#f0f0f0;border-radius:4px;height:14px;overflow:hidden}}
.prog-fill{{height:100%;border-radius:4px;transition:width .4s}}
.data-table{{width:100%;border-collapse:collapse;font-size:12px}}
.data-table th{{background:var(--bg);padding:9px 11px;text-align:left;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.4px;border-bottom:2px solid #e0e0e0;color:var(--grey);white-space:nowrap}}
.data-table td{{padding:9px 11px;border-bottom:1px solid #f0f0f0;vertical-align:middle}}
.data-table tr:last-child td{{border-bottom:none}}
.data-table tr:hover td{{background:var(--green-t)}}
.pill{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:700}}
.p-green{{background:var(--green-t);color:#1B5E20}}
.p-blue{{background:var(--blue-t);color:#006064}}
.p-warn{{background:var(--warn-t);color:#7B5B00}}
.p-red{{background:var(--red-t);color:#7B1818}}
.p-grey{{background:#f0f0f0;color:#666}}
.p-purple{{background:var(--purple-t);color:#4A148C}}
.heatmap{{width:100%;border-collapse:collapse;font-size:11px;margin-top:8px}}
.heatmap th{{background:var(--bg);padding:7px 10px;text-align:center;font-weight:700;font-size:10px;border:1px solid #e0e0e0;color:var(--grey);text-transform:uppercase;letter-spacing:.3px}}
.heatmap th.row-hdr{{text-align:left}}
.heatmap td{{padding:7px 10px;text-align:center;border:1px solid #e8e8e8;font-weight:700;font-size:11px}}
.h-hi{{background:#C8E6C9;color:#1B5E20}}
.h-md{{background:#FFF9C4;color:#795548}}
.h-lo{{background:#FFCDD2;color:#B71C1C}}
.h-na{{background:#f5f5f5;color:#bbb;font-weight:400}}
.country-bar-row{{display:flex;align-items:center;gap:10px;margin:6px 0}}
.cbl{{width:100px;font-size:11px;font-weight:700;color:var(--grey);text-align:right;flex-shrink:0}}
.cbw{{flex:1;background:#f0f0f0;border-radius:4px;overflow:hidden;height:24px;position:relative}}
.cbf{{height:24px;border-radius:4px;display:flex;align-items:center;padding-left:8px;font-size:10px;font-weight:700;color:#fff;transition:width .5s ease}}

/* Tooltip Styles */
.tooltip{{position:relative;display:table-cell;cursor:help}}
.tooltip .tooltip-text{{
  visibility:hidden;
  width:280px;
  background-color:#000;
  color:#fff;
  text-align:left;
  border-radius:6px;
  padding:8px 12px;
  position:absolute;
  z-index:1000;
  bottom:125%;
  left:50%;
  margin-left:-140px;
  opacity:0;
  transition:opacity 0.3s;
  font-size:11px;
  line-height:1.4;
  font-weight:400;
  box-shadow:0 4px 8px rgba(0,0,0,0.15);
  white-space:normal;
}}
.tooltip .tooltip-text::after{{
  content:"";
  position:absolute;
  top:100%;
  left:50%;
  margin-left:-5px;
  border-width:5px;
  border-style:solid;
  border-color:#333 transparent transparent transparent;
}}
.tooltip:hover .tooltip-text{{
  visibility:visible;
  opacity:0.80;
}}
.tooltip.tooltip-bottom .tooltip-text{{
  bottom:auto;
  top:125%;
}}
.tooltip.tooltip-bottom .tooltip-text::after{{
  top:auto;
  bottom:100%;
  border-color:transparent transparent #333 transparent;
}}
.tooltip.tooltip-left .tooltip-text{{
  left:auto;
  right:125%;
  margin-left:0;
  margin-right:0;
}}
.tooltip.tooltip-left .tooltip-text::after{{
  left:auto;
  right:-5px;
  top:50%;
  margin-top:-5px;
  border-color:transparent #333 transparent transparent;
}}
.tooltip.tooltip-right .tooltip-text{{
  left:125%;
  margin-left:0;
}}
.tooltip.tooltip-right .tooltip-text::after{{
  left:-5px;
  top:50%;
  margin-top:-5px;
  border-color:transparent transparent transparent #333;
}}
.cbc{{width:80px;font-size:11px;color:var(--grey)}}
.legend{{display:flex;gap:16px;margin-top:10px;flex-wrap:wrap}}
.legend-item{{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--grey)}}
.legend-dot{{width:10px;height:10px;border-radius:2px;flex-shrink:0}}
.donut-wrap{{display:flex;align-items:center;justify-content: center;gap:24px;padding:10px 0}}
.donut-legend{{display:flex;flex-direction:column;gap:8px}}
.dl-row{{display:flex;align-items:center;gap:8px;font-size:12px}}
.dl-dot{{width:12px;height:12px;border-radius:3px;flex-shrink:0}}
.dl-val{{font-weight:700;color:var(--dark);margin-left:auto;padding-left:16px}}
.insight{{padding:10px 14px;border-radius:4px;font-size:12px;margin-top:12px;line-height:1.6}}
.insight.warn{{background:var(--warn-t);color:#7B5B00;border-left:3px solid var(--warn)}}
.insight.green{{background:var(--green-t);color:#1B5E20;border-left:3px solid var(--green)}}
.insight.red{{background:var(--red-t);color:#7B1818;border-left:3px solid var(--red)}}
.insight.purple{{background:var(--purple-t);color:#4A148C;border-left:3px solid var(--purple)}}

.toggle-btn{{
  padding:6px 14px;border:1px solid var(--green);background:#fff;color:var(--green);
  border-radius:4px;font-size:11px;font-weight:700;cursor:pointer;
  transition:all .15s;text-transform:uppercase;letter-spacing:.4px
}}
.toggle-btn:hover{{background:var(--green-t)}}
.toggle-btn.active{{background:var(--green);color:#fff}}


#repVolumeChart,
#repResponseChart {{
  width: 460px !important;
  height: 230px !important;
  display: block;
  margin: 0 auto;
  max-width: 500px !important;
}}





footer{{background:#fff;border-top:1px solid #e0e0e0;padding:12px 28px;font-size:11px;color:#aaa;text-align:center}}
@media(max-width:768px){{.chart-grid{{grid-template-columns:1fr}}.chart-grid.three{{grid-template-columns:1fr}}}}
</style>
</head>
<body>

<div class="topbar">
  <div class="logo">
  <svg viewBox="0 0 505.17 245.15" style="height:32px; width:auto;" xmlns="http://www.w3.org/2000/svg">
  <g>
  <title>Layer 1</title>
  <path id="svg_1" fill="#ffffff" d="m401.19,193.25l30.83,14.6c-8.96,17.92 -27.09,37.3 -60.64,37.3c-40.69,0 -72.1,-26.46 -75,-65.86l-18.15,64.36l-37.94,0l-21.05,-88.12l-21.04,88.12l-37.94,0l-30.13,-106.2l-25.51,13.29c-5.22,-9.18 -15.85,-17.72 -29.39,-17.72c-22.72,0 -38.77,17.5 -38.77,40.22s16.05,40.22 38.77,40.22c10.21,0 20.64,-4.17 25.64,-8.34l0,-10.83l-31.26,0l0,-31.48l67.09,0l0,55.23c-15,16.46 -35.22,27.09 -61.47,27.09c-41.46,0.02 -75.23,-27.71 -75.23,-71.88s33.77,-71.9 75.24,-71.9c23.5,0 39.97,9.44 50.73,21.54l-5.5,-20.04l40.22,0l21.05,93.54l23.75,-93.54l27.51,0l23.75,93.54l20.84,-93.54l40.44,0l-7.17,25.86c13.5,-17.2 35.22,-27.36 60.52,-27.36c33.55,0 51.48,18.97 60.64,37.3l-30.83,14.58c-4.59,-11.04 -16.47,-20.2 -29.81,-20.2c-22.72,0 -38.77,17.5 -38.77,40.22s16.05,40.22 38.77,40.22c13.35,0 25.22,-9.18 29.81,-20.22" class="cls-3"/>
  <path id="svg_2" fill-rule="evenodd" fill="#ffffff" d="m448.54,14.52l0,0c-3.32,-3.32 -3.32,-8.7 0,-12.03c3.32,-3.32 8.7,-3.32 12.03,0s3.32,8.7 0,12.03c-3.32,3.32 -8.7,3.32 -12.03,0" class="cls-1"/>
  <path id="svg_3" fill-rule="evenodd" fill="#ffffff" d="m462.57,28.55l0,0c-3.32,-3.32 -3.32,-8.71 0,-12.03c3.32,-3.32 8.71,-3.32 12.03,0c3.32,3.32 3.32,8.71 0,12.03c-3.32,3.32 -8.71,3.32 -12.03,0" class="cls-1"/>
  <path id="svg_4" fill="#ffffff" fill-rule="evenodd" d="m476.61,42.59l0,0c-3.32,-3.32 -3.32,-8.7 0,-12.03c3.32,-3.32 8.7,-3.32 12.03,0c3.32,3.32 3.32,8.71 0,12.03c-3.32,3.32 -8.7,3.32 -12.03,0" class="cls-2"/>
  <path id="svg_5" fill="#ffffff" fill-rule="evenodd" d="m490.65,56.63l0,0c-3.32,-3.32 -3.32,-8.7 0,-12.03c3.32,-3.32 8.71,-3.32 12.03,0c3.32,3.32 3.32,8.71 0,12.03c-3.32,3.32 -8.71,3.32 -12.03,0" class="cls-2"/>
  <path id="svg_6" fill-rule="evenodd" fill="#ffffff" d="m434.51,28.55l0,0c-3.32,-3.32 -3.32,-8.71 0,-12.03c3.32,-3.32 8.71,-3.32 12.03,0c3.32,3.32 3.32,8.71 0,12.03c-3.32,3.32 -8.71,3.32 -12.03,0" class="cls-1"/>
  <path id="svg_7" fill-rule="evenodd" fill="#ffffff" d="m448.54,42.58l0,0c-3.32,-3.32 -3.32,-8.7 0,-12.03c3.32,-3.32 8.71,-3.32 12.03,0c3.32,3.32 3.32,8.71 0,12.03c-3.32,3.32 -8.7,3.32 -12.03,0" class="cls-1"/>
  <path id="svg_8" fill="#ffffff" fill-rule="evenodd" d="m462.58,56.62l0,0c-3.32,-3.32 -3.32,-8.7 0,-12.03c3.32,-3.32 8.71,-3.32 12.03,0c3.32,3.32 3.32,8.71 0,12.03c-3.32,3.32 -8.71,3.32 -12.03,0" class="cls-2"/>
  <path id="svg_9" fill="#ffffff" fill-rule="evenodd" d="m476.62,70.66l0,0c-3.32,-3.32 -3.32,-8.71 0,-12.03s8.71,-3.32 12.03,0c3.32,3.32 3.32,8.71 0,12.03c-3.32,3.32 -8.71,3.32 -12.03,0" class="cls-2"/>
  <path id="svg_10" fill-rule="evenodd" fill="#ffffff" d="m448.55,70.65l0,0c-3.32,-3.32 -3.32,-8.71 0,-12.03c3.32,-3.32 8.71,-3.32 12.03,0c3.32,3.32 3.32,8.7 0,12.03c-3.32,3.32 -8.7,3.32 -12.03,0" class="cls-1"/>
  <path id="svg_11" fill-rule="evenodd" fill="#ffffff" d="m462.59,84.69l0,0c-3.32,-3.32 -3.32,-8.71 0,-12.03c3.32,-3.32 8.71,-3.32 12.03,0c3.32,3.32 3.32,8.71 0,12.03c-3.32,3.32 -8.7,3.32 -12.03,0" class="cls-1"/>
  <path id="svg_12" fill-rule="evenodd" fill="#ffffff" d="m434.52,84.69l0,0c-3.32,-3.32 -3.32,-8.71 0,-12.03c3.32,-3.32 8.71,-3.32 12.03,0c3.32,3.32 3.32,8.71 0,12.03c-3.32,3.32 -8.71,3.32 -12.03,0" class="cls-1"/>
  <path id="svg_13" fill-rule="evenodd" fill="#ffffff" d="m448.56,98.72l0,0c-3.32,-3.32 -3.32,-8.7 0,-12.03c3.32,-3.32 8.71,-3.32 12.03,0c3.32,3.32 3.32,8.71 0,12.03c-3.32,3.32 -8.71,3.32 -12.03,0" class="cls-1"/>
  <path id="svg_14" fill="#ffffff" fill-rule="evenodd" d="m429.53,230.63c-3.32,-3.32 -8.71,-3.32 -12.03,0c-3.32,3.32 -3.32,8.71 0,12.03c3.32,3.32 8.71,3.32 12.03,0s3.32,-8.71 0,-12.03zm-1.02,11.02c-2.76,2.76 -7.24,2.76 -10,0c-2.76,-2.76 -2.76,-7.24 0,-10c2.76,-2.76 7.24,-2.76 10,0c2.76,2.76 2.76,7.24 0,10z" class="cls-2"/>
  <path id="svg_15" fill="#ffffff" d="m424.56,240.65l-1.34,-2.71l-1.06,0l0,2.71l-2.06,0l0,-8l4.01,0c1.77,0 2.78,1.18 2.78,2.66c0,1.39 -0.85,2.13 -1.59,2.4l1.63,2.94l-2.36,0l-0.01,0zm-0.76,-6.26l-1.63,0l0,1.8l1.63,0c0.54,0 1,-0.35 1,-0.9s-0.46,-0.9 -1,-0.9z" class="cls-3"/>
 </g>
</svg>

</div>
  
  <span style="font-size: 20px;font-family: 'proxima-nova';">Marketing Leads Tracking Dashboard</span>
  <div  style="display: flex;flex-direction: column;    align-items: end;">
  <span class="badge">{quip_total} Leads</span>
  <span style="font-size: 10px;">Last updated: {now_str}</span>
  
  </div>
  
</div>

<div class="tabs">
  <button class="tab-btn active"  onclick="show('t1',this)">Overview</button>
  <button class="tab-btn"         onclick="show('t2',this)">No Response </button>
  <button class="tab-btn"         onclick="show('t3',this)">Engagement </button>
  <button class="tab-btn"         onclick="show('t4',this)">Quoting &amp; Follow-Up </button>
  <button class="tab-btn purple"  onclick="show('t5',this)">Won / Loss </button>
  <button class="tab-btn"         onclick="show('t6',this)">Rep Performance</button>
  <button class="tab-btn purple"  onclick="show('t7',this)">Data Quality</button>
</div>

<div class="content">

<!-- ══ TAB 1 · PIPELINE OVERVIEW ══ -->
<div class="tab-panel active" id="tab-t1">
  <div class="metric-row">
     <div class="metric-card"><div class="tooltip"><div class="val">{quip_total}</div><span class="tooltip-text">Total emails received: {quip_total}<br>Total rejected emails:{rejected}<br><br>All qualified leads in the system received from Hubspot emails regardless of status — includes Quip-matched leads.</span></div><div class="label">Total Leads</div><div class="sub">Qualified Leads based on Knowledge and expriance</div></div>
    <div class="metric-card red"><div class="tooltip"><div class="val">{quip_no_action_count}</div><span class="tooltip-text">Unresponded leads: {quip_no_action_count}<br><br>leads still in NO ACTION — rep has been notified but has not yet replied to the customer.</span></div><div class="label">Unresponded</div><div class="sub">No response on email received by agent</div></div>
    <div class="metric-card"><div class="tooltip"><div class="val">{resp_rate_pct}%</div><span class="tooltip-text">Response Rate: {resp_rate_pct}% (all leads)<br>({quip_active_count + q_no_action_n} total eligible leads)<br><br>Percentage of ALL eligible leads that received at least one rep response.</span></div><div class="label">Response Rate</div><div class="sub">All leads a rep replied to</div></div>
    <div class="metric-card blue"><div class="tooltip"><div class="val">{quip_active_count}</div><span class="tooltip-text">Active leads<br>ENGAGED ({q_engaged_n}) + QUOTED ({q_quoted_n}) + FOLLOW UP ({q_followup_n}) = {quip_active_count}<br><br>Leads currently being worked by reps.</span></div><div class="label">Active Pipeline</div><div class="sub">Engaged + Quoted + Follow-up</div></div>
  </div>

  <div class="section-hdr">Pipeline Funnel</div>
  <div class="chart-card" style="margin-bottom:18px">
    <div style="padding:6px 0">
{funnel_html}    </div>
  </div>

  <div class="chart-grid">
    <div class="chart-card">
      <h3 class="tooltip">Lead Arrivals — Last 60 Days<span class="tooltip-text">Shows the daily volume of new leads received from HubSpot over the past 60 days<br><br>This helps identify lead volume patterns and seasonality in inbound lead generation.</span></h3>
      <p class="sub">Daily count of new HubSpot leads received</p>
      <div class="chart-wrap"><canvas id="arrivalsChart"></canvas></div>
    </div>
    <div class="chart-card">
      <h3 class="tooltip">Mode of Freight Distribution<span class="tooltip-text">Breakdown of leads matched in Quip by transportation mode (Air, Sea, Overland)<br><br>Excludes REJECTED leads.</span></h3>
      <p class="sub">excluding REJECTED</p>
      <div class="donut-wrap">
        <div style="position:relative;height:200px;width:200px;flex-shrink:0"><canvas id="modeChart"></canvas></div>
        <div class="donut-legend" id="modeLegend"></div>
      </div>
    </div>
  </div>
</div>


<!-- ══ TAB 2 · NO RESPONSE ══ -->
<div class="tab-panel" id="tab-t2">
  <div class="info-bar red">🔴 Every lead where GWC has not yet responded, broken down by rep country and coloured by age urgency. Use the country filter to drill down. Answers: <em>"Where is inaction concentrated, and how old is it?"</em></div>

  <div style="margin-left:auto;display:flex;align-items:center;gap:8px;margin-bottom: 12px; margin-top: 12px;">
        <label for="t2CountryFilter" style="font-size:12px;font-weight:600;color:#555">Filter by responsible rep of routed country:</label>
        <select id="t2CountryFilter" onchange="t2FilterRows()"
          style="font-size:12px;padding:5px 10px;border:1px solid #ddd;border-radius:6px;background:#fff;cursor:pointer">
          <option value="">All Reps Countries</option>
          {t2_country_opts}
        </select>
        <span id="t2RowCount" style="font-size:11px;color:#888"></span>
      </div>


  <div class="metric-row">
    <div class="metric-card red">
      <div class="tooltip"><div class="val" id="t2KpiTotal">{no_resp_count}</div><span class="tooltip-text">Total unresponded leads: {no_resp_count}<br><br>These are all leads still in NO_ACTION status that have not received any response from GWC reps.</span></div>
      <div class="label">No GWC Response</div>
      <div class="sub">NO_ACTION · Quip-matched leads only</div>
    </div>
    <div class="metric-card warn">
      <div class="tooltip"><div class="val" id="t2KpiOverdue">{overdue_count}</div><span class="tooltip-text">Overdue leads: {overdue_count}<br><br>NO_ACTION leads that are more than 3 days old and past the early warning threshold for response.</span></div>
      <div class="label">Overdue &gt; 3 days</div>
      <div class="sub">Past early warning threshold</div>
    </div>

    
    <div class="metric-card blue">
      <div class="tooltip"><div class="val" id="t2KpiAge">{avg_nr_age}d</div><span class="tooltip-text">Average age: {avg_nr_age} days<br><br>The mean age of all unresponded leads. Higher numbers indicate slower overall response times.</span></div>
      <div class="label">Avg Lead Age</div>
      <div class="sub">Unresponded leads</div>
    </div>
  </div>

  <div class="section-hdr red">Inaction by Responsible Rep</div>
  <div class="chart-card">
    <h3 class="tooltip">Unresponded Leads by Rep<span class="tooltip-text">Shows unresponded leads grouped by responsible rep and routing country<br><br>Bar length represents lead count, color represents age urgency tier.</span></h3>
    <p class="sub">Bar length = count · Colour = age tier (green &lt;3d · orange 3–6d · dark orange 7–13d · red ≥14d)</p>    <div style="margin-top:12px">
{country_bars_html}    </div>
    <div class="legend" style="margin-top:14px">
      <div class="legend-item tooltip"><div class="legend-dot" style="background:#27AE60"></div>&lt; 3 days (fresh)<span class="tooltip-text">Fresh leads: Less than 3 days old<br><br>These leads are still within acceptable response time and can be handled normally.</span></div>
      <div class="legend-item tooltip"><div class="legend-dot" style="background:#F39C12"></div>3–6 days<span class="tooltip-text">Warning zone: 3-6 days old<br><br>These leads are approaching the critical threshold and need priority attention.</span></div>
      <div class="legend-item tooltip"><div class="legend-dot" style="background:#E67E22"></div>7–13 days<span class="tooltip-text">Critical zone: 7-13 days old<br><br>These leads are significantly overdue and require immediate escalation.</span></div>
      <div class="legend-item tooltip"><div class="legend-dot" style="background:#C0392B"></div>≥ 14 days (critical)<span class="tooltip-text">Emergency zone: 14+ days old<br><br>These leads are critically overdue and need urgent management intervention.</span></div>
    </div>
    <div id="t2Insight" class="insight red" style="margin-top:14px;display:none"></div>
    {insight_country}
  </div>

  <div class="section-hdr red" style="margin-top:24px">Unresponded Lead Detail</div>
  <div class="chart-card">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap">
      <h3 style="margin:0">All NO_ACTION Leads — sorted oldest first</h3>
      
      
    </div>
    <table class="data-table" id="t2Table" style="margin-top:4px">
      <thead><tr>
        <th class="tooltip">GWC ID<span class="tooltip-text">GWC's internal lead identifier<br><br>Unique ID assigned to each lead in the system for tracking purposes.</span></th>
        <th class="tooltip">Company<span class="tooltip-text">Company name from the lead<br><br>The organization that submitted the freight inquiry.</span></th>
        <th class="tooltip">Routed to Rep Country<span class="tooltip-text">Routing country<br><br>The canonical country used to assign the lead to a rep — sourced from Quip BD POC column or Phase 2 routing decision.</span></th>
        <th class="tooltip">MOT<span class="tooltip-text">Mode of Transport<br><br>Air, Sea, or Overland freight transportation method.</span></th>
        <th class="tooltip">Age<span class="tooltip-text">Lead age in days<br><br>How long ago this lead was received (since creation date).</span></th>
        <th class="tooltip">Classification<span class="tooltip-text">Lead quality classification<br><br>FULLY_QUALIFIED, PARTIAL, or UNQUALIFIED based on completeness.</span></th>
        <th class="tooltip">Missing Fields<span class="tooltip-text">Required fields not provided<br><br>List of mandatory information missing from the lead form.</span></th>
        <th class="tooltip">Marketing Updates Summary<span class="tooltip-text">Recent marketing activity<br><br>Summary of any marketing emails, updates, or communications sent to this lead.</span></th>
      </tr></thead>
      <tbody>
{no_action_rows_html}      </tbody>
    </table>
    <div id="t2EmptyMsg" style="display:none;text-align:center;color:#aaa;padding:20px;font-size:13px">No leads match the selected country.</div>
  </div>
</div>


<!-- ══ TAB 3 · ENGAGEMENT ══ -->
<div class="tab-panel" id="tab-t3">
  <div class="info-bar blue">⚡Shows a Day 1–15 histogram and a cumulative % line and a per-MOT response speed breakdown.</div>

  <div style="margin-left:auto;display:flex;align-items:center;gap:8px;margin-bottom: 12px; margin-top: 12px;">
        <label for="t3RepFilter" style="font-size:12px;font-weight:600;color:#555">Filter by rep:</label>
        <select id="t3RepFilter" onchange="t3FilterRows()"
          style="font-size:12px;padding:5px 10px;border:1px solid #ddd;border-radius:6px;background:#fff;cursor:pointer">
          <option value="">All Reps</option>
          {rep_options}
        </select>
      </div>

  <div class="metric-row">
    <div class="metric-card"><div class="tooltip"><div class="val" id="t3KpiEngaged">{total_engaged}</div><span class="tooltip-text">Total engaged: {total_engaged} leads<br><br>Leads that have received at least one response from GWC reps, regardless of how long it took.</span></div><div class="label">Total Engaged</div><div class="sub">Replied at least once</div></div>
    <div class="metric-card blue"><div class="tooltip"><div class="val" id="t3KpiDay01">{day01_pct}%</div><span class="tooltip-text">Same-day/next-day: {day01_pct}%<br><br>Percentage of leads receiving a response within the first day of arrival. Higher % indicates faster response culture.</span></div><div class="label">Engaged Day 0–1</div><div class="sub">Same-day or next-day response</div></div>
    <div class="metric-card"><div class="tooltip"><div class="val" id="t3KpiAvgResp">{avg_resp_s}</div><span class="tooltip-text">Average response time: {avg_resp_s}<br><br>Mean time from lead arrival to first GWC reply. Target: <5 days for sea freight, <2 days for air.</span></div><div class="label">Avg Response Time</div><div class="sub">Days to first GWC reply</div></div>
    <div class="metric-card warn"><div class="tooltip"><div class="val" id="t3KpiNoAction">{quip_no_action_count}</div><span class="tooltip-text">Still waiting: {quip_no_action_count} Quip leads<br><br>Quip-matched leads that have not yet received a rep response. Rep has been notified but has not replied to the customer.</span></div><div class="label">Still Waiting</div><div class="sub">Quip · NO_ACTION (no reply yet)</div></div>
  </div>

  <div class="section-hdr">New Leads Engaged — Response Day (Day 0 to Day 15+)</div>
  <div class="chart-grid">
    <div class="chart-card">
      <h3 class="tooltip">Response Speed Histogram<span class="tooltip-text">Shows the distribution of leads by how many days after arrival they received their first GWC response.<br><br>The total engaged count ({total_engaged}) includes all Quip leads with status "ENGAGED", "QUOTED", or "FOLLOW_UP".<br><br>Orphan leads (leads recieved before the pipeline starts and appear in the pipeline, so AI clssify their status) are not represented in this chart as email_received_at or first_response_at fields were not detected in the pipeline<br><br>Higher bars on the left indicate faster response times, which is ideal for customer satisfaction.</span></h3>
      <p class="sub">Count of leads first engaged on each day after HubSpot arrival</p>
      <div class="chart-wrap"><canvas id="responseHistChart"></canvas></div>
    </div>
    <div class="chart-card new">
      <h3 class="tooltip">Cumulative % Engaged by Day N<span class="tooltip-text">Shows what percentage of all leads have received at least one response by each day<br><br>50% line: Target is 50% of leads replied to by Day 5 · 80% line: Target is 80% by Day 10</span></h3>
      <p class="sub">What % of all leads had a GWC response by Day N? Reference lines at 50% and 80%</p>
      <div class="chart-wrap"><canvas id="cumulativeChart"></canvas></div>
    </div>
  </div>

  <div class="section-hdr blue">Response Speed by Mode of Freight</div>
  <div class="chart-grid">
  <div class="chart-card  new">
    <h3 class="tooltip">Average Response Time — Fastest to Slowest<span class="tooltip-text">Ranked comparison of average response time by mode of freight<br><br>Air freight typically responds fastest, Sea LCL/FCL slower. Helps identify bottlenecks in specific MOT workflows.</span></h3>
    <p class="sub">Ordered comparison of average days to first response by mode of freight</p>
    <div class="chart-wrap" style="height:350px"><canvas id="motResponseSummaryChart"></canvas></div>
  </div>

  <div class="chart-card">
    <h3 class="tooltip">% of Leads Engaged Within Each Day Bucket — by MOT<span class="tooltip-text">Shows response consistency by freight mode and day bucket<br><br>Compares how each MOT performs across time windows to identify slow modes.</span></h3>
    <p class="sub">Identifies whether a specific freight mode is consistently slower to respond to</p>
    <div class="chart-wrap tall"><canvas id="motResponseChart"></canvas></div>
  </div>

  
  </div>
</div>


<!-- ══ TAB 4 · QUOTING & FOLLOW-UP ══ -->
<div class="tab-panel" id="tab-t4">
  <div class="info-bar blue">📋 Shows quotation charts and a "Customer Follow-Up" section powered by the activity log.</div>

  <div style="margin-left:auto;display:flex;align-items:center;gap:8px;margin-bottom: 12px; margin-top: 12px;">
        <label for="t4RepFilter" style="font-size:12px;font-weight:600;color:#555">Filter by rep:</label>
        <select id="t4RepFilter" onchange="t4FilterRows()"
          style="font-size:12px;padding:5px 10px;border:1px solid #ddd;border-radius:6px;background:#fff;cursor:pointer">
          <option value="">All Reps</option>
          {rep_options}
        </select>
      </div>

  <div class="section-hdr">New Leads Quoted — Lead Age at Quotation</div>
  <div class="chart-grid">
    <div class="chart-card">
      <h3 class="tooltip">Lead Age at Quote (from arrival date)<span class="tooltip-text">Shows how long leads wait from arrival to quote submission<br><br>Left bars indicate faster quoting. Typical targets: Air <5 days, Sea <7 days. Longer delays indicate proposal backlogs.</span></h3>
      <p class="sub">How old was the lead when the quotation was sent?</p>
      <div class="chart-wrap"><canvas id="quoteAgeChart"></canvas></div>
    </div>
    <div class="chart-card">
      <h3 class="tooltip">Engagement → Quote Gap<span class="tooltip-text">Time elapsed between first GWC response and when the quote was sent<br><br>Shows quote preparation/internal processing time. Shorter gaps = faster proposal turnaround and better customer experience.</span></h3>
      <p class="sub">Days from first GWC response to quotation being sent</p>
      <div class="chart-wrap"><canvas id="engQuoteGapChart"></canvas></div>
    </div>
  </div>

  <div class="section-hdr blue">Customer Follow-Up Cadence</div>
  <div class="metric-row">
    <div class="metric-card blue"><div class="tooltip"><div class="val" id="t4KpiFollowup">{unique_followup}</div><span class="tooltip-text">Follow-up leads: {unique_followup}<br><br>Unique clients currently in FOLLOW_UP status, waiting for decision or requiring nurturing on pending quotes.</span></div><div class="label">Unique Clients in Follow-Up</div><div class="sub">Currently FOLLOW_UP status</div></div>
    <div class="metric-card purple"><div class="tooltip"><div class="val" id="t4KpiReminders">{total_reminders}</div><span class="tooltip-text">Total reminders: {total_reminders}<br><br>Total number of follow-up reminder events sent across all leads. Higher = more active cadence management.</span></div><div class="label">Reminders Sent</div><div class="sub">Total CLOSE_REMINDER events</div></div>
    <div class="metric-card warn"><div class="tooltip"><div class="val" id="t4KpiEscalations">{escalations_sent}</div><span class="tooltip-text">Escalations: {escalations_sent}<br><br>Leads reaching Day 28 without closure, automatically escalated to manager. Indicates leads at risk of being lost.</span></div><div class="label">Escalations</div><div class="sub">Day 28 — manager notified</div></div>
    <div class="metric-card grey"><div class="tooltip"><div class="val" id="t4KpiAvgRem">{avg_rem_per_lead}</div><span class="tooltip-text">Average reminders: {avg_rem_per_lead} per lead<br><br>Mean number of follow-up touches per lead before resolution. Typical range: 2-4 reminders indicates healthy cadence.</span></div><div class="label">Avg Reminders / Lead</div><div class="sub">Before resolution</div></div>
  </div>

  <div class="chart-grid">
    <div class="chart-card new">
      <h3 class="tooltip">Follow-Up Reminders Sent by Cadence Week<span class="tooltip-text">Breakdown of reminder touches by customer engagement stage<br><br>W1 (Days 1-7): daily reminders · W2 (Days 10&14): twice-weekly · W3 (Day 21): weekly check · W4+ (Day 28): escalation trigger</span></h3>
      <p class="sub">W1 = daily (Days 1–7) · W2 = Days 10 &amp; 14 · W3 = Day 21 · W4+ = Day 28 (escalation)</p>
      <div class="chart-wrap"><canvas id="cadenceChart"></canvas></div>
    </div>
    <div class="chart-card new">
      <h3 class="tooltip">Days in FOLLOW_UP — Lead Age Distribution<span class="tooltip-text">Shows how long leads are sitting in follow-up status waiting for customer decision<br><br>Long bars on the right indicate leads stuck in pipeline. Supports effectiveness of reminder cadence and escalation timing.</span></h3>
      <p class="sub">Current age of leads sitting in FOLLOW_UP status</p>
      <div class="chart-wrap"><canvas id="followUpAgeChart"></canvas></div>
    </div>
  </div>
</div>


<!-- ══ TAB 5 · WON / LOSS ══ -->
<div class="tab-panel" id="tab-t5">
  <div class="info-bar purple">🏆 Deals confirmed Won or Lost. Lead aging from arrival to close. </div>

  <div style="margin-left:auto;display:flex;align-items:center;gap:8px;margin-bottom: 12px; margin-top: 12px;">
        <label for="t5RepFilter" style="font-size:12px;font-weight:600;color:#555">Filter by rep:</label>
        <select id="t5RepFilter" onchange="t5FilterRows()"
          style="font-size:12px;padding:5px 10px;border:1px solid #ddd;border-radius:6px;background:#fff;cursor:pointer">
          <option value="">All Reps</option>
          {rep_options}
        </select>
      </div>

  <div class="metric-row">
    <div class="metric-card"><div class="tooltip"><div class="val" id="t5KpiWon" style="color:var(--green)">{won_count}</div><span class="tooltip-text">Won deals: {won_count}<br><br>Leads that reached WON_LOSS status and were confirmed as closed won. These represent completed, successful deals.</span></div><div class="label">Won</div><div class="sub">Deals confirmed</div></div>
    <div class="metric-card red"><div class="tooltip"><div class="val" id="t5KpiLost">{loss_count}</div><span class="tooltip-text">Lost deals: {loss_count}<br><br>Leads that reached WON_LOSS status but were confirmed as closed lost. Indicates customer went with competitor or chose not to proceed.</span></div><div class="label">Lost</div><div class="sub">Deals closed lost</div></div>
    <div class="metric-card blue"><div class="tooltip"><div class="val" id="t5KpiWinRate">{win_rate_s}</div><span class="tooltip-text">Win rate: {win_rate_s}<br><br>Percentage of closed deals (won + lost) that resulted in a win. Excludes active pipeline and rejected leads. Target: >40%</span></div><div class="label">Win Rate</div><div class="sub">Won ÷ (Won + Lost)</div></div>
    <div class="metric-card grey"><div class="tooltip"><div class="val" id="t5KpiAvgClose">{avg_close_s}</div><span class="tooltip-text">Average close time: {avg_close_s}<br><br>Mean days from lead arrival to deal confirmation (won or lost). Indicates sales cycle length. Typical: 14-30 days.</span></div><div class="label">Avg Days to Close</div><div class="sub">Arrival → deal confirmed</div></div>
    <div class="metric-card purple"><div class="tooltip"><div class="val" id="t5KpiActive">{quip_active_count}</div><span class="tooltip-text">Still active (Quip): {quip_active_count}<br><br>Quip-matched leads currently in ENGAGED, QUOTED, or FOLLOW_UP status. Not yet closed (won/lost) or rejected.</span></div><div class="label">Still Active</div><div class="sub">Quip · Not yet won / lost</div></div>
  </div>

  <div class="chart-grid">
    <div class="chart-card new">
      <h3 class="tooltip">Win / Loss / Active Distribution<span class="tooltip-text">Pie chart showing the outcome distribution across all {quip_total} leads<br><br>Won (green) = closed won sales · Lost (red) = closed lost sales · Active (cyan) = currently in pipeline · Rejected (grey) = unqualified/no action</span></h3>
      <p class="sub">Overall outcome across all {quip_total} leads</p>
      <div class="donut-wrap">
        <div style="position:relative;height:170px;width:170px;flex-shrink:0"><canvas id="wonLossDonut"></canvas></div>
        <div class="donut-legend">
          <div class="dl-row tooltip"><div class="dl-dot" style="background:#3FAE2A"></div>Won<span class="tooltip-text">Won: {won_count} deals ({_pct(won_count,quip_total)}%)<br><br>Deals successfully closed and confirmed as won by GWC.</span><div class="dl-val" id="wlLeg0">{won_count} ({_pct(won_count,quip_total)}%)</div></div>
          <div class="dl-row tooltip"><div class="dl-dot" style="background:#C0392B"></div>Lost<span class="tooltip-text">Lost: {loss_count} deals ({_pct(loss_count,quip_total)}%)<br><br>Deals that closed but were lost to competitor or customer declined.</span><div class="dl-val" id="wlLeg1">{loss_count} ({_pct(loss_count,quip_total)}%)</div></div>
          <div class="dl-row tooltip"><div class="dl-dot" style="background:#00ABC7"></div>Active Pipeline<span class="tooltip-text">Active: {quip_active_count} leads ({_pct(quip_active_count,quip_total)}%)<br><br>Quip-matched leads currently in ENGAGED, QUOTED, or FOLLOW_UP status. Still being worked toward closure.</span><div class="dl-val" id="wlLeg2">{quip_active_count} ({_pct(quip_active_count,quip_total)}%)</div></div>
          <div class="dl-row tooltip"><div class="dl-dot" style="background:#B5B5B5"></div>Rejected / No Action<span class="tooltip-text">Not pursued (Quip scope): {q_no_action_n+q_rejected_n} leads ({_pct(q_no_action_n+q_rejected_n,quip_total)}%)<br><br>Quip-matched leads in NO_ACTION or REJECTED status.</span><div class="dl-val" id="wlLeg3">{q_no_action_n+q_rejected_n} ({_pct(q_no_action_n+q_rejected_n,quip_total)}%)</div></div>
        </div>
      </div>
    </div>
    <div class="chart-card new">
      <h3 class="tooltip">Lead Age at Close (Won vs Lost)<span class="tooltip-text">Compares how long it takes to close won vs lost deals<br><br>Shows whether wins happen faster than losses. Can indicate sales process effectiveness or customer urgency differences.</span></h3>
      <p class="sub">Days from arrival to deal confirmation — grouped by outcome</p>
      <div class="chart-wrap"><canvas id="closeAgeChart"></canvas></div>
    </div>
  </div>

  <div class="section-hdr purple">Confirmed Deals Detail</div>
  <div class="chart-card new">
    <h3>All WON_LOSS Leads</h3>
    <table class="data-table" id="t5Table" style="margin-top:10px">
      <thead><tr>
        <th class="tooltip">GWC ID<span class="tooltip-text">GWC's internal deal identifier<br><br>Unique ID assigned for tracking this closed deal.</span></th>
        <th class="tooltip">Company<span class="tooltip-text">Company name<br><br>The customer organization for this deal.</span></th>
        <th class="tooltip">Destination<span class="tooltip-text">Destination country<br><br>Target country for the freight shipment.</span></th>
        <th class="tooltip">MOT<span class="tooltip-text">Mode of Transport<br><br>Air, Sea (FCL/LCL), or Overland. Impacts sales cycle length and quote complexity.</span></th>
        <th class="tooltip">Outcome<span class="tooltip-text">Deal result: Won or Lost<br><br>Won = closed successfully · Lost = customer selected competitor or did not proceed.</span></th>
        <th class="tooltip">Days to Close<span class="tooltip-text">Sales cycle length<br><br>Days from lead arrival to deal confirmation. Indicates speed of sales process for this deal.</span></th>
        <th class="tooltip">Rep<span class="tooltip-text">Sales rep who closed the deal<br><br>The account owner responsible for this customer relationship.</span></th>
      </tr></thead>
      <tbody>
{wl_rows_html}      </tbody>
    </table>
    <div id="t5EmptyMsg" style="display:none;text-align:center;color:#aaa;padding:20px;font-size:13px">No deals match the selected rep.</div>
    <div class="insight green" style="margin-top:12px">{t5_insight}</div>
  </div>
</div>


<!-- ══ TAB 6 · REP PERFORMANCE ══ -->
<div class="tab-panel" id="tab-t6">
  <div class="info-bar green">👤 A sortable rep leaderboard and avg response bar chart.</div>

  <div class="metric-row">
    <div class="metric-card"><div class="tooltip"><div class="val">{active_reps}</div><span class="tooltip-text">Active reps: {active_reps}<br><br>Number of sales reps currently assigned leads in the system. Only includes reps with active pipeline.</span></div><div class="label">Active Reps</div><div class="sub">Assigned leads this period</div></div>
    <div class="metric-card blue"><div class="tooltip"><div class="val">{avg_resp_s}</div><span class="tooltip-text">Average response: {avg_resp_s} days<br><br>Mean time for all reps to send first response. Target: <5 days. Red SLA line indicates reps above this threshold.</span></div><div class="label">Avg Response</div><div class="sub">Days to first reply</div></div>
    <div class="metric-card"><div class="tooltip"><div class="val">{quote_rate_pct}%</div><span class="tooltip-text">Quote rate: {quote_rate_pct}%<br><br>Percentage of engaged leads that received a formal quote. Higher = more active sales conversion.</span></div><div class="label">Quote Rate</div><div class="sub">% engaged leads quoted</div></div>
    <div class="metric-card warn"><div class="tooltip"><div class="val">{reps_below_sla}</div><span class="tooltip-text">Reps below SLA: {reps_below_sla}<br><br>Number of reps averaging >5 days to first response. These reps need coaching on response speed.</span></div><div class="label">Reps Below SLA</div><div class="sub">Avg response &gt; 5 days</div></div>
  </div>

  <div class="chart-grid three">
    <div class="chart-card" style="min-width:220px;max-width:320px;flex:0 1 280px">
      <h3 class="tooltip">Pipeline Status Breakdown<span class="tooltip-text">Quip-matched leads by current pipeline stage<br><br>Shows the total split across No Response, Engaged, Quoted and Follow-Up. A healthy pipeline should see the grey slice shrinking over time as reps engage leads.</span></h3>
      <p class="sub">Quip-matched leads · {quip_total} total</p>
      <div class="chart-wrap" style="height:220px"><canvas id="leadStatusDonut"></canvas></div>
      <div style="display:flex;flex-wrap:wrap;gap:6px 12px;margin-top:10px;font-size:11px">
        <span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:{GY_HEX};margin-right:4px"></span>No Response <strong>{q_no_action_n}</strong></span>
        <span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#3FAE2A;margin-right:4px"></span>Engaged <strong>{q_engaged_n}</strong></span>
        <span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#00ABC7;margin-right:4px"></span>Quoted <strong>{q_quoted_n}</strong></span>
        <span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#E67E22;margin-right:4px"></span>Follow-Up <strong>{q_followup_n}</strong></span>
      </div>
    </div>
    <div class="chart-card">
      <h3 class="tooltip">Lead Volume by Rep<span class="tooltip-text">Lead distribution by rep and status<br><br>Shows how many leads each rep has in each pipeline stage. Helps identify workload balance and rep specialization.</span></h3>
      <p class="sub">Leads currently in each status per rep</p>
      <div class="chart-wrap"><canvas id="repVolumeChart"></canvas></div>
    </div>
    <div class="chart-card">
      <h3 class="tooltip">Avg Response Days by Rep<span class="tooltip-text">Individual rep response speed performance<br><br>Red line shows 5-day SLA threshold. Bars above line indicate reps needing coaching on response speed.</span></h3>
      <p class="sub">Lower is better · Red line = 5-day SLA threshold</p>
      <div class="chart-wrap"><canvas id="repResponseChart"></canvas></div>
    </div>
  </div>

  <div class="section-hdr">Rep Leaderboard</div>
  <div class="chart-card">
    <table class="data-table">
      <thead><tr>
        <th class="tooltip">Rep<span class="tooltip-text">Sales representative name<br><br>The account owner responsible for these leads.</span></th>
        <th class="tooltip">Leads<span class="tooltip-text">Total leads assigned<br><br>All leads currently assigned to this rep, regardless of status.</span></th>
        <th class="tooltip">Responded<span class="tooltip-text">Response percentage<br><br>Percentage of assigned leads that have received at least one response from this rep.</span></th>
        <th class="tooltip">Quoted<span class="tooltip-text">Number of quotes sent<br><br>How many leads this rep has sent formal proposals to.</span></th>
        <th class="tooltip">Won<span class="tooltip-text">Number of deals won<br><br>Closed deals where this rep was the account owner.</span></th>
        <th class="tooltip">Avg Response<span class="tooltip-text">Average response time in days<br><br>Mean time for this rep to send first response. Color-coded: green <3d, blue 3-5d, orange 5-7d, red >7d.</span></th>
        <th class="tooltip">Avg Quote Gap<span class="tooltip-text">Average quote preparation time<br><br>Days between first response and quote submission. Lower = faster proposal turnaround.</span></th>
        <th class="tooltip">Response Rate<span class="tooltip-text">Response rate percentage<br><br>Progress bar showing what percentage of assigned leads have been contacted.</span></th>
      </tr></thead>
      <tbody>
{rep_table_html}      </tbody>
    </table>
  </div>
</div>


<!-- ══ TAB 7 · DATA QUALITY ══ -->
<div class="tab-panel" id="tab-t7">
  <div class="info-bar purple">📐 Field completeness and a heatmap of missing data by mode of freight.</div>

  <div class="metric-row">
    <div class="metric-card red"><div class="tooltip"><div class="val">{avg_miss_s}</div><span class="tooltip-text">Average fields missing: {avg_miss_s}<br><br>Mean number of required fields not provided per lead on arrival. Higher = more incomplete submissions.</span></div><div class="label">Avg Fields Missing</div><div class="sub">Per lead on arrival</div></div>
    <div class="metric-card warn"><div class="tooltip"><div class="val">{pct_compl_s}%</div><span class="tooltip-text">Fully complete: {pct_compl_s}%<br><br>Percentage of leads with all required fields present. Lower percentage indicates form or data quality issues.</span></div><div class="label">Fully Complete</div><div class="sub">Leads with all required fields</div></div>
    <div class="metric-card purple"><div class="tooltip"><div class="val" style="font-size:18px;padding-top:4px">{most_common_gap}</div><span class="tooltip-text">Most common gap: {most_common_gap}<br><br>The field with the lowest completeness rate across all leads. Indicates the biggest data quality issue.</span></div><div class="label">Most Common Gap</div><div class="sub">Lowest completeness field</div></div>
    <div class="metric-card blue"><div class="tooltip"><div class="val">{systemic_gaps}</div><span class="tooltip-text">Systemic gaps: {systemic_gaps}<br><br>Number of fields missing in >80% of leads. These are form-level issues requiring HubSpot updates.</span></div><div class="label">Systemic Gaps</div><div class="sub">Fields missing in &gt;80% of leads</div></div>
  </div>

  <div class="section-hdr purple">Field Completeness — All {non_rejected} Active Leads</div>
  <div class="chart-card new">
    <h3 class="tooltip">% of Leads Where Each Field Is Present<span class="tooltip-text">Field completeness across all active leads<br><br>Green >80% = healthy · Orange 50-80% = partial · Red <50% = systemic gap. These fields come from the HubSpot agency form.</span></h3>
    <p class="sub">Green &gt;80% · Orange 50–80% · Red &lt;50% · These are fields GWC receives from the HubSpot agency form</p>
    <div style="display:flex;flex-direction:column;gap:9px;margin-top:10px">
{field_bars_html}    </div>
    <div class="insight warn" style="margin-top:14px">{t7_insight}</div>
  </div>

  <div class="section-hdr purple" style="margin-top:24px">Field Completeness Heatmap — by Mode of Freight</div>
  <div class="chart-card new">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <h3 class="tooltip">MOT × Required Field Completeness<span class="tooltip-text">Field completeness by mode of freight<br><br>Shows which fields are missing for specific transportation modes. Grey = field not required for that MOT. Toggle between percentages and counts.</span></h3>
      <button id="heatmapToggle" class="toggle-btn" onclick="toggleHeatmap()">Show Counts</button>
    </div>
    <p class="sub">Green &gt;80% · Yellow 50–80% · Red &lt;50% · Grey = field not required for this MOT</p>
    <div style="overflow-x:auto">
      <table class="heatmap" id="heatmapTable">
        <thead><tr>
          <th class="row-hdr" style="width:160px">Field</th>
          {heatmap_header}
        </tr></thead>
        <tbody>
{heatmap_rows_html}        </tbody>
      </table>
    </div>
    <div class="insight red" style="margin-top:12px">🔴 Fields shown in red (&lt;20% fill rate) are systematically missing — the HubSpot intake form does not collect them regardless of shipment type. Orange fields (20–80%) are partially covered. A single form update could resolve the majority of PARTIALLY_QUALIFIED classifications.</div>
  </div>

  <div class="section-hdr purple" style="margin-top:24px">Most Common Missing Field Combinations</div>
  <div class="chart-card new">
    <h3>Gap Pattern Frequency</h3>
    <p class="sub">Groups of leads sharing the same missing-field combination (top 10)</p>
    <table class="data-table" style="margin-top:10px">
      <thead><tr>
        <th class="tooltip">Missing Fields (combination)<span class="tooltip-text">Combination of fields missing from these leads<br><br>Shows which fields are commonly missing together, indicating form design issues.</span></th>
        <th class="tooltip" style="text-align:right">Leads<span class="tooltip-text">Number of leads with this exact missing field combination<br><br>How many leads share this same data gap pattern.</span></th>
        <th class="tooltip" style="text-align:right">% of All<span class="tooltip-text">Percentage of total leads with this gap pattern<br><br>What portion of all leads have this specific combination of missing fields.</span></th>
        <th class="tooltip">MOTs<span class="tooltip-text">Mode of freight distribution for these leads<br><br>Which transportation modes are affected by this missing field combination.</span></th>
        <th class="tooltip">Root Cause<span class="tooltip-text">Why these fields are missing<br><br>Form gap = HubSpot form doesn't collect this field. Rep gap = sales reps not gathering this information.</span></th>
      </tr></thead>
      <tbody>
{gap_table_html}      </tbody>
    </table>
    <div class="insight purple" style="margin-top:12px">💡 <strong>Recommendation:</strong> If missing-field combinations share the same root cause, a single HubSpot form update would eliminate PARTIALLY_QUALIFIED classification for the majority of leads.</div>
  </div>
</div>


<!-- ══ TAB 8 · NOTES INTELLIGENCE ══ -->
<div class="tab-panel" id="tab-t8">
  <div class="info-bar purple">✍ <strong>Notes Intelligence</strong> — deep-dives the Extensia free-text "Notes" field using AI quality scores (1–5) and freight domain keyword detection. Identifies where agents need coaching and surfaces model examples for training. Enriched by <code>freight_domain_knowledge.md</code> when present in <code>lead-status-tracker/references/</code>.</div>

  <div class="metric-row">
    <div class="metric-card purple"><div class="tooltip"><div class="val">{t8_avg_s}</div><span class="tooltip-text">Average notes score: {t8_avg_s}/5<br><br>Mean quality score across all notes. 1=empty/useless, 5=excellent with ETD, customs, incoterms, and cargo details.</span></div><div class="label">Avg Notes Score</div><div class="sub">1 = empty · 5 = excellent</div></div>
    <div class="metric-card blue"><div class="tooltip"><div class="val">{t8_fill_rate}%</div><span class="tooltip-text">Notes fill rate: {t8_fill_rate}%<br><br>Percentage of leads with any content in the notes field. {t8_filled} filled, {t8_empty} empty.</span></div><div class="label">Notes Fill Rate</div><div class="sub">{t8_filled} filled · {t8_empty} empty</div></div>
    <div class="metric-card red"><div class="tooltip"><div class="val">{t8_score_dist.get(1,0)}</div><span class="tooltip-text">Score 1 (useless): {t8_score_dist.get(1,0)}<br><br>Notes that are empty or contain no actionable context. Highest priority for coaching.</span></div><div class="label">Score 1 (Useless)</div><div class="sub">Empty or context-free</div></div>
    <div class="metric-card"><div class="tooltip"><div class="val">{t8_good_count}</div><span class="tooltip-text">Score 4-5 (useful): {t8_good_count}<br><br>Notes with actionable details for sales. These are model examples for training.</span></div><div class="label">Score 4–5 (Useful)</div><div class="sub">Actionable for salesperson</div></div>
    <div class="metric-card warn"><div class="tooltip"><div class="val">{t8_poor_pct}%</div><span class="tooltip-text">Poor notes rate: {t8_poor_pct}%<br><br>Percentage of notes scoring 1-2 (poor quality). Indicates training opportunity.</span></div><div class="label">Poor Notes Rate</div><div class="sub">Score 1–2 combined</div></div>
  </div>

  <div class="section-hdr purple">Score &amp; Length Distribution</div>
  <div class="chart-grid">
    <div class="chart-card new">
      <div class="tooltip"><h3>Notes Quality Score Distribution</h3><span class="tooltip-text">Notes Quality Score Distribution<br><br>Distribution of AI-assigned quality scores (1-5) across all notes. Shows overall quality of documentation and identifies training needs.</span></div>
      <p class="sub">1 = useless / empty context · 5 = ETD + customs + incoterms + cargo details all captured</p>
      <div class="chart-wrap"><canvas id="t8ScoreChart"></canvas></div>
    </div>
    <div class="chart-card new">
      <div class="tooltip"><h3>Notes Length Distribution</h3><span class="tooltip-text">Notes Length Distribution<br><br>Word count distribution of notes. Empty and very short notes (1-3 words) typically provide insufficient context for quoting.</span></div>
      <p class="sub">Word count buckets — empty notes and 1–3 word notes are near-useless for quoting</p>
      <div class="chart-wrap"><canvas id="t8LenChart"></canvas></div>
    </div>
  </div>

  <div class="chart-grid">
    <div class="chart-card new">
      <div class="tooltip"><h3>Avg Notes Score by Mode of Freight</h3><span class="tooltip-text">Avg Notes Score by Mode of Freight<br><br>Average quality scores segmented by freight mode. Identifies if agents perform better with familiar freight types and need training on others.</span></div>
      <p class="sub">Agents may capture better context for familiar freight modes — identifies training gaps by MOT</p>
      <div class="chart-wrap"><canvas id="t8MotChart"></canvas></div>
    </div>
    <div class="chart-card new">
      <div class="tooltip"><h3>Domain Keyword Group Coverage per Note</h3><span class="tooltip-text">Domain Keyword Group Coverage per Note<br><br>How many freight knowledge domains (0-6) are mentioned in each note. Higher coverage indicates more comprehensive documentation.</span></div>
      <p class="sub">How many freight knowledge domains appear in each note (0 = none · 6 = all groups mentioned)</p>
      <div class="chart-wrap"><canvas id="t8CovChart"></canvas></div>
    </div>
  </div>

  <div class="section-hdr purple">Freight Domain Knowledge Coverage</div>
  <div class="chart-card new">
    <div class="tooltip"><h3>% of Notes Mentioning Each Domain Group</h3><span class="tooltip-text">% of Notes Mentioning Each Domain Group<br><br>Percentage of filled notes that mention each freight domain keyword group. Low percentages indicate consistent gaps in call documentation.</span></div>
    <p class="sub">Keyword groups sourced from <code>freight_domain_knowledge.md</code> — low % = agents consistently skip this domain during calls</p>
{t8_group_html}    <div class="insight purple" style="margin-top:14px">💡 <strong>Coaching priority:</strong> Focus training on the lowest-scoring groups first. If "Incoterms" or "Cargo Type" are consistently absent, add explicit prompts to the Extensia call script for those questions.</div>
  </div>

  <div class="section-hdr blue" style="margin-top:24px">Top Freight Terms Detected in Notes</div>
  <div class="chart-card">
    <div class="tooltip"><h3>Keyword Frequency Cloud</h3><span class="tooltip-text">Keyword Frequency Cloud<br><br>Most frequently mentioned freight terms across all notes. Font size indicates frequency of mention.</span></div>
    <p class="sub">Most-mentioned freight terms across all notes — size scales with frequency</p>
    <div style="margin:14px 0 4px">{t8_kw_html}</div>
  </div>

  <div class="section-hdr" style="margin-top:24px">Notes Quality by Destination Country</div>
  <div class="chart-card">
    <div class="tooltip"><h3>Avg Notes Score per Country</h3><span class="tooltip-text">Avg Notes Score per Country<br><br>Average note quality scores by destination country. Low-scoring countries may indicate specific campaigns or teams needing targeted coaching.</span></div>
    <p class="sub">Low-scoring countries may indicate a specific campaign or Extensia team cohort needing targeted coaching</p>
    <table class="data-table" style="margin-top:10px;max-width:420px">
      <thead><tr><th class="tooltip">Destination Country<span class="tooltip-text">Destination Country<br><br>The country the freight shipment is destined for.</span></th><th class="tooltip">Leads Scored<span class="tooltip-text">Leads Scored<br><br>Number of leads from this country that have been evaluated for note quality.</span></th><th class="tooltip">Avg Score<span class="tooltip-text">Avg Score<br><br>Average AI quality score (1-5) for notes from leads to this country.</span></th></tr></thead>
      <tbody>
{t8_country_html}      </tbody>
    </table>
  </div>

  <div class="section-hdr red" style="margin-top:24px">⚠ Worst Notes — Extensia Negative Training Samples</div>
  <div class="chart-card new">
    <div class="tooltip"><h3>Score 1 Leads With Notes Submitted — share with Extensia as "do not submit like this" examples</h3><span class="tooltip-text">Score 1 Leads With Notes Submitted<br><br>Non-empty notes that scored 1 (useless). These are misleading because they appear to have content but provide no actionable context. Highest priority for coaching.</span></div>
    <p class="sub">Non-empty notes scored 1 are misleading — the agent wrote something but it provides zero actionable context. These are the highest-priority coaching targets.</p>
    <table class="data-table" style="margin-top:10px">
      <thead><tr>
        <th class="tooltip">Lead<span class="tooltip-text">Lead<br><br>The company name or lead identifier.</span></th><th class="tooltip">MOT<span class="tooltip-text">MOT<br><br>Mode of Transport (Air, Sea, Overland, etc.).</span></th><th class="tooltip">Dest.<span class="tooltip-text">Dest.<br><br>Destination country or port.</span></th>
        <th class="tooltip">Note Submitted (exact)<span class="tooltip-text">Note Submitted (exact)<br><br>The exact text entered in the Extensia notes field.</span></th><th class="tooltip">Score<span class="tooltip-text">Score<br><br>AI quality score (1-5) assigned to this note.</span></th><th class="tooltip">AI Feedback<span class="tooltip-text">AI Feedback<br><br>AI analysis explaining why this note scored poorly.</span></th>
      </tr></thead>
      <tbody>
{t8_worst_html}      </tbody>
    </table>
    <div class="insight red" style="margin-top:12px">{t8_insight}</div>
  </div>

  <div class="section-hdr" style="margin-top:24px">✓ Best Notes — Extensia Positive Training Samples</div>
  <div class="chart-card">
    <div class="tooltip"><h3>Score 4–5 Notes — model answers to use in Extensia training packs</h3><span class="tooltip-text">Score 4–5 Notes — Model Examples<br><br>High-quality notes (score 4-5) that demonstrate excellent documentation. Use these as training examples for Extensia agents.</span></div>
    <p class="sub">These demonstrate the note quality GWC needs: ETD, customs preference, cargo specifics, and urgency all captured in one short note.</p>
    <table class="data-table" style="margin-top:10px">
      <thead><tr>
        <th class="tooltip">Company<span class="tooltip-text">Company<br><br>The company name or lead identifier.</span></th><th class="tooltip">Note Submitted (exact)<span class="tooltip-text">Note Submitted (exact)<br><br>The exact text entered in the Extensia notes field.</span></th><th class="tooltip">Score<span class="tooltip-text">Score<br><br>AI quality score (4-5) for this high-quality note.</span></th><th class="tooltip">What Makes It Good<span class="tooltip-text">What Makes It Good<br><br>AI analysis explaining the specific strengths of this note.</span></th>
      </tr></thead>
      <tbody>
{t8_best_html}      </tbody>
    </table>
  </div>

<!-- ── Marketing Insights Section ── -->
  <div class="section-hdr purple" style="margin-top:28px">📊 Marketing Insights</div>

  <!-- A: Radar + B: Scatter Age -->
  <div class="chart-grid">
    <div class="chart-card new">
      <div class="tooltip"><h3>A · Domain Knowledge Coverage Radar</h3><span class="tooltip-text">Domain Knowledge Coverage Radar<br><br>Radar chart showing percentage of notes that mention each freight domain area. Low values indicate gaps that should be addressed in call scripts.</span></div>
      <p class="sub">How often Extensia notes mention each freight domain area (% of filled notes). Low axes = gaps to fix in call scripts.</p>
      <div class="chart-wrap" style="height:260px"><canvas id="t8RadarChart"></canvas></div>
    </div>
    <div class="chart-card new">
      <div class="tooltip"><h3>B · Notes Quality vs. Lead Age (days)</h3><span class="tooltip-text">Notes Quality vs. Lead Age<br><br>Scatter plot showing lead age (days) vs. note quality score. High-quality notes should correlate with faster pipeline movement.</span></div>
      <p class="sub">Each dot = one lead. Lower points = faster pipeline. Score 4–5 leads should cluster at the bottom.</p>
      <div class="chart-wrap" style="height:260px"><canvas id="t8ScatterAgeChart"></canvas></div>
    </div>
  </div>

  <!-- C: Scatter Outcome + E: Sentiment Bar -->
  <div class="chart-grid" style="margin-top:16px">
    <div class="chart-card new">
      <div class="tooltip"><h3>C · Notes Quality vs. Deal Outcome</h3><span class="tooltip-text">Notes Quality vs. Deal Outcome<br><br>Comparison of won vs. lost deals by note quality score. Higher quality notes should correlate with higher win rates.</span></div>
      <p class="sub">Won vs. Lost count per quality score. Higher scores should yield more wins.</p>
      <div class="chart-wrap" style="height:260px"><canvas id="t8OutcomeChart"></canvas></div>
    </div>
    <div class="chart-card new">
      <div class="tooltip"><h3>E · Sales Feedback vs. Notes Quality Score</h3><span class="tooltip-text">Sales Feedback vs. Notes Quality Score<br><br>Extensia feedback sentiment grouped by note quality score. Positive feedback indicates sufficient context; negative feedback suggests missing information.</span></div>
      <p class="sub">Extensia feedback sentiment grouped by score. Positive = rep had enough context; Needs Info = rep had to chase missing data.</p>
      <div class="chart-wrap" style="height:260px"><canvas id="t8SentimentChart"></canvas></div>
    </div>
  </div>

  <!-- D: Technical Gap Bubbles -->
  <div class="section-hdr red" style="margin-top:24px">D · Technical Gap — Most Frequently Missing Fields (Partial Leads)</div>
  <div class="chart-card">
    <h3>Top Missing Fields in Partially Qualified Leads</h3>
    <p class="sub">Red bubbles = most common gaps. These are exactly the fields Marketing should add as checkboxes or prompts in HubSpot forms / Extensia call scripts.</p>
    <div id="t8BubbleWrap" style="display:flex;flex-wrap:wrap;gap:10px;padding:18px 8px;min-height:80px;align-items:center"></div>
    <div class="insight red" style="margin-top:10px">💡 <strong>Action:</strong> Take the top 3 fields above and add them as mandatory fields to the next Extensia call script version. Share the list with Sameh / Najam.</div>
  </div>

</div><!-- /content -->

<footer>GWC Lead Maturity Dashboard · Generated {now_str} · Live pipeline data</footer>

<script>
/* ── Tab switching ── */
function show(tab, btn) {{
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + tab).classList.add('active');
  btn.classList.add('active');
  window.dispatchEvent(new Event('resize'));
}}

/* ── Rep leaderboard drill-down ── */
function togglePoc(id) {{
  var rows = document.querySelectorAll('.poc-row-' + id);
  var btn  = document.getElementById('poc-btn-' + id);
  if (!rows.length) return;
  var hidden = rows[0].style.display === 'none';
  rows.forEach(function(r) {{ r.style.display = hidden ? '' : 'none'; }});
  if (btn) {{
    var num = btn.getAttribute('data-count') || '';
    btn.textContent = hidden ? ('▼ ' + num + ' reps') : ('▶ ' + num + ' reps');
  }}
}}

const G = '#3FAE2A', B = '#00ABC7', O = '#E67E22', R = '#C0392B',
      P = '#9C27B0', GY = '#B5B5B5', D = '#333';
const a80 = c => c + 'CC';

/* ── Embedded live data ── */
const ARR_LABELS   = {arr_labels_js};
const ARR_DATA     = {arr_data_js};
const MODE_LABELS  = {mode_labels_js};
const MODE_DATA    = {mode_data_js};
const RESP_BKTS    = {resp_bkts_js};
const RESP_HIST    = {resp_hist_js};
const CUMUL_PCT    = {cumul_js};
const MOT_CHART    = {mot_chart_js};
const MOT_RESP_LBLS = {mot_response_labels_js};
const MOT_RESP_DATA = {mot_response_data_js};
const Q_AGE_LBLS   = {q_age_lbl_js};
const Q_AGE_DATA   = {q_age_dat_js};
const GAP_LBLS     = {gap_lbl_js};
const GAP_DATA     = {gap_dat_js};
const FU_LBLS      = {fu_lbl_js};
const FU_DATA      = {fu_dat_js};
const CAD_LBLS     = {cad_lbl_js};
const CAD_DATA     = {cad_dat_js};
const WL_DATA      = {wl_donut_js};
const CL_LBLS      = {cl_lbl_js};
const CL_WON       = {cl_won_js};
const CL_LOST      = {cl_lost_js};
const REP_LBLS     = {rep_lbl_js};
const REP_NA       = {rep_na_js};
const REP_ENG      = {rep_eng_js};
const REP_QUOT     = {rep_quot_js};
const REP_FU       = {rep_fu_js};
const REP_RESP     = {rep_resp_js};
const STATUS_DONUT = [{q_no_action_n}, {q_engaged_n}, {q_quoted_n}, {q_followup_n}];

const T8_SCORE_LBLS = {t8_score_lbl_js};
const T8_SCORE_DATA = {t8_score_dat_js};
const T8_LEN_LBLS   = {t8_len_lbl_js};
const T8_LEN_DATA   = {t8_len_dat_js};
const T8_MOT_LBLS   = {t8_mot_lbl_js};
const T8_MOT_DATA   = {t8_mot_dat_js};
const T8_COV_LBLS   = {t8_cov_lbl_js};
const T8_COV_DATA   = {t8_cov_dat_js};

const T8_SCATTER_AGE  = {t8_scatter_age_js};
const T8_OUTCOME      = {t8_outcome_js};
const T8_MISSING      = {t8_missing_js};
const T8_SENTIMENT    = {t8_sentiment_js};
const T8_RADAR_LBLS   = {t8_radar_labels_js};
const T8_RADAR_DATA   = {t8_radar_data_js};

// Rep-specific data for filtering
const REP_RESP_HIST = {rep_resp_hist_js};
const REP_CUMUL_PCT = {rep_cumul_pct_js};
const REP_MOT_RESP  = {rep_mot_resp_js};
const REP_MOT_AVG_RESP = {rep_mot_avg_resp_js};
const REP_Q_AGE_HIST = {rep_q_age_hist_js};
const REP_GAP_HIST  = {rep_gap_hist_js};
const REP_FU_AGE_HIST = {rep_fu_age_hist_js};
const REP_CAD_WEEK       = {rep_cad_week_js};
const REP_COUNTRIES_MAP  = {rep_countries_map_js};
const COUNTRY_REPS_MAP   = {country_reps_map_js};
const REP_CL_WON    = {rep_cl_won_js};
const REP_CL_LOST   = {rep_cl_lost_js};



/* ── Tab 1: Arrivals bar ── */
new Chart(document.getElementById('arrivalsChart'), {{
  type: 'bar',
  data: {{ labels: ARR_LABELS, datasets: [{{
    label: 'Leads', data: ARR_DATA,
    backgroundColor: a80(B), borderColor: B, borderWidth: 1, borderRadius: 2
  }}] }},
  options: {{ responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ ticks: {{ maxTicksLimit: 10, font: {{ size: 10 }} }}, grid: {{ display: false }} }},
      y: {{ ticks: {{ stepSize: 1, font: {{ size: 10 }} }}, grid: {{ color: '#f0f0f0' }} }}
    }}
  }}
}});

/* ── Tab 1: Mode donut ── */
const MODE_COLORS = MODE_LABELS.map(lbl =>
  lbl === 'Unknown' ? GY : (
    lbl === 'Sea'    ? B :
    lbl === 'Overland'  ? G :
    lbl === 'Air'   ? O :
                        P
  )
);
``


new Chart(document.getElementById('modeChart'), {{
  type: 'doughnut',
  data: {{ labels: MODE_LABELS, datasets: [{{
    data: MODE_DATA,
    backgroundColor: MODE_COLORS.slice(0, MODE_LABELS.length),
    borderWidth: 2,
    borderColor: '#fff'
  }}] }},
  options: {{ 
    responsive: true,
    maintainAspectRatio: false,
    cutout: '65%',
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          label: ctx => ` ${{ctx.label}}: ${{ctx.parsed}}`
        }}
      }}
    }}
  }}
}});



(function() {{
  const el = document.getElementById('modeLegend');
  if (!el) return;
  MODE_LABELS.forEach((lbl, i) => {{
    el.innerHTML += `<div class="dl-row"><div class="dl-dot" style="background:${{MODE_COLORS[i % MODE_COLORS.length]}}"></div> ${{lbl}}<div class="dl-val">${{MODE_DATA[i]}}</div></div>`;
  }});
}})();






/* ── Tab 2: Country filter ── */
// ── Tab 2 country filter — updates the whole tab, not just the table ──────
const T2_COUNTRY = {t2_js_country_data};
const T2_TOTALS  = {t2_js_totals};
const T2_REP_KPIS = {t2_rep_kpis_js};
const T3_REP_KPIS = {rep_t3_kpis_js};
const T4_REP_KPIS = {rep_t4_kpis_js};
const T5_REP_KPIS = {rep_t5_kpis_js};

function t2FilterRows() {{
  const sel = document.getElementById('t2CountryFilter');
  const val = sel ? sel.value : '';

  // 1. Filter table rows
  const tbody  = document.querySelector('#t2Table tbody');
  const empty  = document.getElementById('t2EmptyMsg');
  let visible  = 0;
  if (tbody) {{
    tbody.querySelectorAll('tr').forEach(function(row) {{
      const country = row.getAttribute('data-country') || '';
      const show    = !val || country === val;
      row.style.display = show ? '' : 'none';
      if (show) visible++;
    }});
  }}
  const countEl = document.getElementById('t2RowCount');
  if (countEl) countEl.textContent = val ? visible + ' lead' + (visible !== 1 ? 's' : '') : '';
  if (empty) empty.style.display = (visible === 0 && val) ? 'block' : 'none';

  // 2. Filter bars by data-country (rep name) and show/hide rep headers
  document.querySelectorAll('.country-bar-row[data-country]').forEach(function(bar) {{
    const countryAttr = bar.getAttribute('data-country') || '';
    const match = !val || countryAttr === val;
    bar.style.display = match ? '' : 'none';
  }});
  // Show a rep group header only if it has at least one visible bar underneath it
  document.querySelectorAll('.rep-group-header[data-rep]').forEach(function(hdr) {{
    if (!val) {{ hdr.style.display = ''; return; }}
    // Walk siblings until the next header
    let sib = hdr.nextElementSibling;
    let hasVisible = false;
    while (sib && !sib.classList.contains('rep-group-header')) {{
      if (sib.classList.contains('country-bar-row') && sib.style.display !== 'none') {{
        hasVisible = true; break;
      }}
      sib = sib.nextElementSibling;
    }}
    hdr.style.display = hasVisible ? '' : 'none';
  }});

  // 3. Update KPI cards from precomputed lookup
  const d = T2_REP_KPIS[val] || T2_REP_KPIS[""];
  if (d) {{
    const el = function(id) {{ return document.getElementById(id); }};
    if (el('t2KpiTotal'))     el('t2KpiTotal').textContent    = d.total || 0;
    if (el('t2KpiOverdue'))   el('t2KpiOverdue').textContent   = d.overdue || 0;
    if (el('t2KpiAge'))       el('t2KpiAge').textContent      = d.avg_age ? `${{d.avg_age}}d` : '—';
  }}

  // 4. Update insight message for selected rep
  const insightEl = document.getElementById('t2Insight');
  if (insightEl) {{
    if (val && d && d.total > 0 && d.avg_age !== null && d.avg_age !== undefined && d.avg_age > 14) {{
      const cnt = d.total;
      const days = d.avg_age;
      insightEl.innerHTML = `⚠ <strong>${{val}} has ${{cnt}} unresponded lead${{cnt !== 1 ? 's' : ''}} averaging ${{days}} days old</strong>. Check their pipeline in country_rep_mapping.csv.`;
      insightEl.style.display = 'block';
    }} else {{
      insightEl.style.display = 'none';
    }}
  }}
}}
document.addEventListener('DOMContentLoaded', function() {{ t2FilterRows(); }});

function t3FilterRows() {{
  const sel = document.getElementById('t3RepFilter');
  const rep = sel ? sel.value : '';

  // Update response histogram chart — show zeros for a selected rep with no data
  // rather than silently falling back to global numbers.
  if (window.responseHistChartInstance) {{
    let histData;
    if (rep) {{
      const repHist = REP_RESP_HIST[rep] || {{}};
      histData = RESP_BKTS.map(b => repHist[b] || 0);
    }} else {{
      histData = RESP_HIST;
    }}
    window.responseHistChartInstance.data.datasets[0].data = histData;
    window.responseHistChartInstance.update();
  }}

  // Update cumulative chart — same rule: zeros for selected rep with no data.
  if (window.cumulativeChartInstance) {{
    let cumulData;
    if (rep) {{
      cumulData = REP_CUMUL_PCT[rep] || Array(RESP_BKTS.length).fill(0);
    }} else {{
      cumulData = CUMUL_PCT;
    }}
    window.cumulativeChartInstance.data.datasets[0].data = cumulData;
    window.cumulativeChartInstance.update();
  }}

  // Update MOT response grouped-bar chart — show empty datasets for rep with no data.
  if (window.motResponseChartInstance) {{
    const motData = rep ? (REP_MOT_RESP[rep] || {{}}) : MOT_CHART;
    const mots = Object.keys(motData).filter(m => m !== 'Unknown');
    const motColors = {{'Air':'#3FAE2A','Sea':'#00ABC7','Overland':'#E67E22'}};
    window.motResponseChartInstance.data.datasets = mots.map(mot => ({{
      label: mot,
      data: motData[mot]?.data || [0, 0, 0, 0],
      backgroundColor: motColors[mot] || '#9C27B0',
      borderRadius: 3,
      borderWidth: 0
    }}));
    window.motResponseChartInstance.update();
  }}

  // Update MOT avg-response summary chart — show empty for rep with no data.
  if (window.motResponseSummaryChartInstance) {{
    const motColors2 = {{'Air':'#3FAE2A','Sea':'#00ABC7','Overland':'#E67E22'}};
    let sumLbls, sumData;
    if (rep) {{
      const repAvg = REP_MOT_AVG_RESP[rep];
      sumLbls = repAvg ? repAvg.lbls : [];
      sumData = repAvg ? repAvg.data : [];
    }} else {{
      sumLbls = MOT_RESP_LBLS.filter(l => l !== 'Unknown');
      sumData = MOT_RESP_DATA;
    }}
    window.motResponseSummaryChartInstance.data.labels = sumLbls;
    window.motResponseSummaryChartInstance.data.datasets[0].data = sumData;
    window.motResponseSummaryChartInstance.data.datasets[0].backgroundColor = sumLbls.map(m => motColors2[m] || '#9C27B0');
    window.motResponseSummaryChartInstance.update();
  }}

  // Update KPI cards
  const kd3 = T3_REP_KPIS[rep] || T3_REP_KPIS[''];
  if (kd3) {{
    const el3 = id => document.getElementById(id);
    if (el3('t3KpiEngaged'))  el3('t3KpiEngaged').textContent  = kd3.engaged ?? 0;
    if (el3('t3KpiDay01'))    el3('t3KpiDay01').textContent    = (kd3.day01_pct ?? 0) + '%';
    if (el3('t3KpiAvgResp'))  el3('t3KpiAvgResp').textContent  = kd3.avg_resp != null ? kd3.avg_resp + 'd' : '—';
    if (el3('t3KpiNoAction')) el3('t3KpiNoAction').textContent = kd3.no_action ?? 0;
  }}
}}

function t4FilterRows() {{
  const sel = document.getElementById('t4RepFilter');
  const rep = sel ? sel.value : '';

  // REP_*[rep] are bucket→count dicts; global vars are positional arrays.
  // Use dict lookup for per-rep; show zeros (not global) when rep has no data.
  const repQAge = rep ? (REP_Q_AGE_HIST[rep] || {{}}) : null;
  const repGap  = rep ? (REP_GAP_HIST[rep]  || {{}}) : null;
  const repFu   = rep ? (REP_FU_AGE_HIST[rep] || {{}}) : null;
  const repCad  = rep ? (REP_CAD_WEEK[rep]  || {{}}) : null;

  // Update quote age chart
  if (window.quoteAgeChartInstance) {{
    const qData = repQAge ? Q_AGE_LBLS.map(b => repQAge[b] || 0) : Q_AGE_DATA;
    window.quoteAgeChartInstance.data.datasets[0].data = qData;
    window.quoteAgeChartInstance.update();
  }}

  // Update gap chart
  if (window.engQuoteGapChartInstance) {{
    const gData = repGap ? GAP_LBLS.map(b => repGap[b] || 0) : GAP_DATA;
    window.engQuoteGapChartInstance.data.datasets[0].data = gData;
    window.engQuoteGapChartInstance.update();
  }}

  // Update followup age chart
  if (window.followupAgeChartInstance) {{
    const fuData = repFu ? FU_LBLS.map(b => repFu[b] || 0) : FU_DATA;
    window.followupAgeChartInstance.data.datasets[0].data = fuData;
    window.followupAgeChartInstance.update();
  }}

  // Update cadence chart — aggregate across all reps in the selected rep's countries.
  if (window.cadenceChartInstance) {{
    const cad_keys = ["Week 1", "Week 2", "Week 3", "Week 4+"];
    let cadData;
    if (rep) {{
      // Collect all reps that share at least one country with the selected rep.
      const repCountries = REP_COUNTRIES_MAP[rep] || [];
      const siblingReps = new Set([rep]);
      repCountries.forEach(c => (COUNTRY_REPS_MAP[c] || []).forEach(r => siblingReps.add(r)));
      // Sum cadence buckets across all sibling reps.
      cadData = cad_keys.map(k =>
        [...siblingReps].reduce((sum, r) => sum + ((REP_CAD_WEEK[r] || {{}})[k] || 0), 0)
      );
    }} else {{
      cadData = CAD_DATA;
    }}
    window.cadenceChartInstance.data.datasets[0].data = cadData;
    window.cadenceChartInstance.update();
  }}

  // Update KPI cards
  const kd4 = T4_REP_KPIS[rep] || T4_REP_KPIS[''];
  if (kd4) {{
    const el4 = id => document.getElementById(id);
    if (el4('t4KpiFollowup'))    el4('t4KpiFollowup').textContent    = kd4.followup ?? 0;
    if (el4('t4KpiReminders'))   el4('t4KpiReminders').textContent   = kd4.reminders ?? 0;
    if (el4('t4KpiEscalations')) el4('t4KpiEscalations').textContent = kd4.escalations ?? 0;
    if (el4('t4KpiAvgRem'))      el4('t4KpiAvgRem').textContent      = kd4.avg_rem ?? 0;
  }}
}}

function t5FilterRows() {{
  const sel = document.getElementById('t5RepFilter');
  const rep = sel ? sel.value : '';

  // REP_CL_WON/LOST[rep] are bucket→count dicts; CL_WON/LOST are positional arrays.
  const repClWon  = rep ? (REP_CL_WON[rep]  || {{}}) : null;
  const repClLost = rep ? (REP_CL_LOST[rep] || {{}}) : null;

  // Update close age chart
  if (window.closeAgeChartInstance) {{
    const wonData  = repClWon  ? CL_LBLS.map(b => repClWon[b]  || 0) : CL_WON;
    const lostData = repClLost ? CL_LBLS.map(b => repClLost[b] || 0) : CL_LOST;
    window.closeAgeChartInstance.data.datasets[0].data = wonData;
    window.closeAgeChartInstance.data.datasets[1].data = lostData;
    window.closeAgeChartInstance.update();
  }}

  // Update won/loss donut + legend
  if (window.wonLossDonutInstance) {{
    const kd5d = T5_REP_KPIS[rep] || T5_REP_KPIS[''];
    const donutData = kd5d
      ? [kd5d.won || 0, kd5d.lost || 0, kd5d.active || 0, kd5d.no_action || 0]
      : WL_DATA;
    window.wonLossDonutInstance.data.datasets[0].data = donutData;
    window.wonLossDonutInstance.update();
    // Sync legend values to match the chart
    const total = donutData.reduce((a, b) => a + b, 0) || 1;
    const legIds = ['wlLeg0', 'wlLeg1', 'wlLeg2', 'wlLeg3'];
    donutData.forEach((v, i) => {{
      const el = document.getElementById(legIds[i]);
      if (el) el.textContent = v + ' (' + Math.round(v / total * 100) + '%)';
    }});
  }}

  // Filter WON_LOSS table rows by rep
  const t5tbody = document.querySelector('#t5Table tbody');
  const t5empty = document.getElementById('t5EmptyMsg');
  let t5visible = 0;
  if (t5tbody) {{
    t5tbody.querySelectorAll('tr').forEach(function(row) {{
      const rowRep = row.getAttribute('data-rep') || '';
      const show = !rep || rowRep === rep;
      row.style.display = show ? '' : 'none';
      if (show) t5visible++;
    }});
  }}
  if (t5empty) t5empty.style.display = (t5visible === 0 && rep) ? 'block' : 'none';

  // Update KPI cards
  const kd5 = T5_REP_KPIS[rep] || T5_REP_KPIS[''];
  if (kd5) {{
    const el5 = id => document.getElementById(id);
    if (el5('t5KpiWon'))      el5('t5KpiWon').textContent      = kd5.won ?? 0;
    if (el5('t5KpiLost'))     el5('t5KpiLost').textContent     = kd5.lost ?? 0;
    if (el5('t5KpiWinRate'))  el5('t5KpiWinRate').textContent  = kd5.win_rate != null ? kd5.win_rate + '%' : '—';
    if (el5('t5KpiAvgClose')) el5('t5KpiAvgClose').textContent = kd5.avg_close != null ? kd5.avg_close + 'd' : '—';
    if (el5('t5KpiActive'))   el5('t5KpiActive').textContent   = kd5.active ?? 0;
  }}
}}

/* ── Tab 3: Response histogram ── */


/* ── Tab 3: Response histogram ── */
const histColors = RESP_BKTS.map((b, i) => {{
  if (i <= 1) return G;
  if (i <= 4) return a80(G);
  if (i <= 8) return a80(O);
  return a80(R);
}});
window.responseHistChartInstance = new Chart(document.getElementById('responseHistChart'), {{
  type: 'bar',
  data: {{ labels: RESP_BKTS, datasets: [{{
    label: 'Leads engaged', data: RESP_HIST,
    backgroundColor: histColors, borderRadius: 3
  }}] }},
  options: {{ responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ ticks: {{ font: {{ size: 9 }} }}, grid: {{ display: false }} }},
      y: {{ ticks: {{ stepSize: 1, font: {{ size: 10 }} }}, grid: {{ color: '#f0f0f0' }} }}
    }}
  }}
}});

/* ── Tab 3: Cumulative % line ── */
window.cumulativeChartInstance = new Chart(document.getElementById('cumulativeChart'), {{
  type: 'line',
  data: {{ labels: RESP_BKTS, datasets: [
    {{ label: 'Cumulative %', data: CUMUL_PCT, borderColor: B, backgroundColor: B+'22',
       fill: true, tension: .35, pointRadius: 3, pointBackgroundColor: B }},
    {{ label: '50% line', data: Array(RESP_BKTS.length).fill(50),
       borderColor: GY, borderDash: [4,4], borderWidth: 1.5, pointRadius: 0 }},
    {{ label: '80% line', data: Array(RESP_BKTS.length).fill(80),
       borderColor: O,  borderDash: [4,4], borderWidth: 1.5, pointRadius: 0 }}
  ] }},
  options: {{ responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ labels: {{ font: {{ size: 10 }}, boxWidth: 12 }} }} }},
    scales: {{
      x: {{ ticks: {{ font: {{ size: 9 }} }}, grid: {{ display: false }} }},
      y: {{ min: 0, max: 100,
           ticks: {{ callback: v => v + '%', font: {{ size: 10 }} }},
           grid: {{ color: '#f0f0f0' }} }}
    }}
  }}
}});

/* ── Tab 3: MOT response grouped bars ── */
(function() {{
  const motKeys = Object.keys(MOT_CHART).filter(mot => mot !== 'Unknown');
  if (!motKeys.length) return;
  const datasets = motKeys.map(mot => ({{
    label: mot,
    data: MOT_CHART[mot].data,
    backgroundColor: MOT_CHART[mot].color || P,
    borderRadius: 3
  }}));
  window.motResponseChartInstance = new Chart(document.getElementById('motResponseChart'), {{
    type: 'bar',
    data: {{ labels: ['Same day', 'Day 1–3', 'Day 4–7', 'Day 8+'], datasets }},
    options: {{ responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ labels: {{ font: {{ size: 10 }}, boxWidth: 12 }} }} }},
      scales: {{
        x: {{ ticks: {{ font: {{ size: 10 }} }}, grid: {{ display: false }} }},
        y: {{ max: 100, ticks: {{ callback: v => v + '%', font: {{ size: 10 }} }},
             grid: {{ color: '#f0f0f0' }} }}
      }}
    }}
  }});
}})();

/* ── Tab 3: MOT response summary (horizontal bars) ── */
(function() {{
  if (!MOT_RESP_LBLS || !MOT_RESP_LBLS.length) return;
  const colors = {{
    'Air': '#3FAE2A',
    'Sea': '#00ABC7',
    'Overland': '#E67E22'
  }};
  const bgColors = MOT_RESP_LBLS.map(m => colors[m] || P);
  window.motResponseSummaryChartInstance = new Chart(document.getElementById('motResponseSummaryChart'), {{
    type: 'bar',
    data: {{ labels: MOT_RESP_LBLS.filter(l => l !== "Unknown"), datasets: [{{
      label: 'Avg Days to Response',
      data: MOT_RESP_DATA,
      backgroundColor: bgColors,
      borderRadius: 3
    }}] }},
    options: {{ responsive: true, maintainAspectRatio: false, indexAxis: 'y',
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        x: {{ ticks: {{ font: {{ size: 10 }} }}, grid: {{ color: '#f0f0f0' }} }},
        y: {{ ticks: {{ font: {{ size: 10 }} }}, grid: {{ display: false }} }}
      }}
    }}
  }});
}})();

/* ── Tab 4: Quote age ── */
window.quoteAgeChartInstance = new Chart(document.getElementById('quoteAgeChart'), {{
  type: 'bar',
  data: {{ labels: Q_AGE_LBLS, datasets: [{{
    label: 'Leads quoted', data: Q_AGE_DATA,
    backgroundColor: [G, a80(G), a80(O), a80(O), R], borderRadius: 3
  }}] }},
  options: {{ responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ ticks: {{ font: {{ size: 10 }} }}, grid: {{ display: false }} }},
      y: {{ ticks: {{ stepSize: 1, font: {{ size: 10 }} }}, grid: {{ color: '#f0f0f0' }} }}
    }}
  }}
}});

/* ── Tab 4: Engagement → Quote gap ── */
window.engQuoteGapChartInstance = new Chart(document.getElementById('engQuoteGapChart'), {{
  type: 'bar',
  data: {{ labels: GAP_LBLS, datasets: [{{
    label: 'Leads', data: GAP_DATA,
    backgroundColor: GAP_LBLS.map((_, i) => i === 0 ? G : i <= 3 ? a80(G) : i <= 6 ? a80(O) : R),
    borderRadius: 3
  }}] }},
  options: {{ responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ ticks: {{ font: {{ size: 10 }} }}, grid: {{ display: false }} }},
      y: {{ ticks: {{ stepSize: 1, font: {{ size: 10 }} }}, grid: {{ color: '#f0f0f0' }} }}
    }}
  }}
}});

/* ── Tab 4: Cadence burndown ── */
window.cadenceChartInstance = new Chart(document.getElementById('cadenceChart'), {{
  type: 'bar',
  data: {{ labels: CAD_LBLS, datasets: [{{
    label: 'Reminders Sent', data: CAD_DATA, backgroundColor: P, borderRadius: 3
  }}] }},
  options: {{ responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ labels: {{ font: {{ size: 10 }}, boxWidth: 12 }} }} }},
    scales: {{
      x: {{ ticks: {{ font: {{ size: 9 }} }}, grid: {{ display: false }} }},
      y: {{ ticks: {{ stepSize: 1, font: {{ size: 10 }} }}, grid: {{ color: '#f0f0f0' }} }}
    }}
  }}
}});

/* ── Tab 4: Follow-up age histogram ── */
window.followupAgeChartInstance = new Chart(document.getElementById('followUpAgeChart'), {{
  type: 'bar',
  data: {{ labels: FU_LBLS, datasets: [{{
    label: 'Leads', data: FU_DATA,
    backgroundColor: [a80(G), a80(B), a80(O), a80(R), R], borderRadius: 3
  }}] }},
  options: {{ responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ ticks: {{ font: {{ size: 10 }} }}, grid: {{ display: false }} }},
      y: {{ ticks: {{ stepSize: 1, font: {{ size: 10 }} }}, grid: {{ color: '#f0f0f0' }} }}
    }}
  }}
}});

/* ── Tab 5: Won/Loss donut ── */
window.wonLossDonutInstance = new Chart(document.getElementById('wonLossDonut'), {{
  type: 'doughnut',
  data: {{ labels: ['Won', 'Lost', 'Active Pipeline', 'Rejected / No Action'],
    datasets: [{{ data: WL_DATA, backgroundColor: [G, R, B, GY], borderWidth: 2, borderColor: '#fff' }}] }},
  options: {{ responsive: true, maintainAspectRatio: false, cutout: '62%',
    plugins: {{ legend: {{ display: false }} }}
  }}
}});

/* ── Tab 5: Close age stacked bar ── */
window.closeAgeChartInstance = new Chart(document.getElementById('closeAgeChart'), {{
  type: 'bar',
  data: {{ labels: CL_LBLS, datasets: [
    {{ label: 'Won',  data: CL_WON,  backgroundColor: G, borderRadius: 3 }},
    {{ label: 'Lost', data: CL_LOST, backgroundColor: R, borderRadius: 3 }}
  ] }},
  options: {{ responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ labels: {{ font: {{ size: 10 }}, boxWidth: 12 }} }} }},
    scales: {{
      x: {{ ticks: {{ font: {{ size: 10 }} }}, grid: {{ display: false }} }},
      y: {{ ticks: {{ stepSize: 1, font: {{ size: 10 }} }}, grid: {{ color: '#f0f0f0' }} }}
    }}
  }}
}});

/* ── Tab 6: Rep volume stacked bars ── */
new Chart(document.getElementById('repVolumeChart'), {{
  type: 'bar',
  data: {{ labels: REP_LBLS, datasets: [
    {{ label: 'No Response', data: REP_NA,   backgroundColor: GY,           borderRadius: 3 }},
    {{ label: 'Engaged',     data: REP_ENG,  backgroundColor: G,            borderRadius: 3 }},
    {{ label: 'Quoted',      data: REP_QUOT, backgroundColor: B,            borderRadius: 3 }},
    {{ label: 'Follow-Up',   data: REP_FU,   backgroundColor: O,            borderRadius: 3 }}
  ] }},
  options: {{ responsive: true, maintainAspectRatio: true,
    plugins: {{ legend: {{ labels: {{ font: {{ size: 10 }}, boxWidth: 12 }} }} }},
    scales: {{
      x: {{ stacked: true, ticks: {{ font: {{ size: 10 }} }}, grid: {{ display: false }} }},
      y: {{ stacked: true, ticks: {{ font: {{ size: 10 }} }}, grid: {{ color: '#f0f0f0' }} }}
    }}
  }}
}});

/* ── Tab 6: Rep response days ── */
(function() {{
  const filtLabels = REP_LBLS.filter((_, i) => REP_RESP[i] > 0);
  const filtResp   = REP_RESP.filter(v => v > 0);
  if (!filtLabels.length) return;
  new Chart(document.getElementById('repResponseChart'), {{
    type: 'bar',
    data: {{ labels: filtLabels, datasets: [
      {{ label: 'Avg response (days)', data: filtResp,
         backgroundColor: filtResp.map(v => v <= 3 ? G : v <= 5 ? a80(B) : v <= 7 ? a80(O) : R),
         borderRadius: 3 }},
      {{ label: '5-day SLA', data: Array(filtLabels.length).fill(5),
         type: 'line', borderColor: R, borderDash: [5,5], borderWidth: 2,
         pointRadius: 0, fill: false }}
    ] }},
    options: {{ responsive: true, maintainAspectRatio: true,
      plugins: {{ legend: {{ labels: {{ font: {{ size: 10 }}, boxWidth: 12 }} }} }},
      scales: {{
        x: {{ ticks: {{ font: {{ size: 10 }} }}, grid: {{ display: false }} }},
        y: {{ ticks: {{ font: {{ size: 10 }} }}, grid: {{ color: '#f0f0f0' }} }}
      }}
    }}
  }});
}})();

/* ── Tab 6: Pipeline status donut ── */
(function() {{
  const total = STATUS_DONUT.reduce((a, b) => a + b, 0);
  new Chart(document.getElementById('leadStatusDonut'), {{
    type: 'doughnut',
    data: {{
      labels: ['No Response', 'Engaged', 'Quoted', 'Follow-Up'],
      datasets: [{{
        data: STATUS_DONUT,
        backgroundColor: [GY, G, B, O],
        borderWidth: 2,
        borderColor: '#fff',
        hoverOffset: 6,
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      cutout: '68%',
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          callbacks: {{
            label: ctx => ` ${{ctx.label}}: ${{ctx.parsed}} (${{total ? Math.round(ctx.parsed/total*100) : 0}}%)`
          }}
        }}
      }}
    }},
    plugins: [{{
      id: 'centerText',
      afterDraw(chart) {{
        if (!chart.chartArea) return;
        const {{ ctx }} = chart;
        const {{ top, bottom, left, right }} = chart.chartArea;
        const cx = (left + right) / 2, cy = (top + bottom) / 2;
        ctx.save();
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.font = 'bold 26px Inter, sans-serif';
        ctx.fillStyle = '#333';
        ctx.fillText(total, cx, cy - 10);
        ctx.font = '11px Inter, sans-serif';
        ctx.fillStyle = '#888';
        ctx.fillText('Leads', cx, cy + 12);
        ctx.restore();
      }}
    }}]
  }});
}})();

/* ── Tab 7: Data quality ── */

let showingPct = true;

function toggleHeatmap() {{
  showingPct = !showingPct;
  const btn = document.getElementById('heatmapToggle');
  const cells = document.querySelectorAll('#heatmapTable tbody td[data-pct]');
  
  btn.textContent = showingPct ? 'Show Counts' : 'Show %';
  btn.classList.toggle('active');
  
  cells.forEach(cell => {{
    cell.textContent = showingPct 
      ? cell.getAttribute('data-pct') + '%'
      : cell.getAttribute('data-count') + '/' + cell.getAttribute('data-total');
  }});
}}


/* ── Tab 8: Notes Intelligence charts ── */
(function() {{
  // Score distribution
  if (document.getElementById('t8ScoreChart')) {{
    new Chart(document.getElementById('t8ScoreChart'), {{
      type: 'bar',
      data: {{ labels: T8_SCORE_LBLS, datasets: [{{
        label: 'Leads', data: T8_SCORE_DATA,
        backgroundColor: [R, a80(O), a80(B), a80(G), G], borderRadius: 4
      }}] }},
      options: {{ responsive:true, maintainAspectRatio:false,
        plugins:{{ legend:{{display:false}},
          tooltip:{{ callbacks:{{ afterLabel: ctx => ['→ Useless','→ Minimal','→ Basic','→ Good','→ Excellent'][ctx.dataIndex] || '' }} }}
        }},
        scales:{{ x:{{ticks:{{font:{{size:11}}}},grid:{{display:false}}}}, y:{{ticks:{{stepSize:1,font:{{size:10}}}},grid:{{color:'#f0f0f0'}}}} }}
      }}
    }});
  }}

  // Length distribution
  if (document.getElementById('t8LenChart')) {{
    new Chart(document.getElementById('t8LenChart'), {{
      type: 'bar',
      data: {{ labels: T8_LEN_LBLS, datasets: [{{
        label: 'Leads', data: T8_LEN_DATA,
        backgroundColor: [GY, a80(R), a80(O), a80(G), G], borderRadius: 4
      }}] }},
      options: {{ responsive:true, maintainAspectRatio:false,
        plugins:{{ legend:{{display:false}} }},
        scales:{{ x:{{ticks:{{font:{{size:11}}}},grid:{{display:false}}}}, y:{{ticks:{{stepSize:1,font:{{size:10}}}},grid:{{color:'#f0f0f0'}}}} }}
      }}
    }});
  }}

  // Avg score by MOT
  if (document.getElementById('t8MotChart') && T8_MOT_LBLS.length) {{
    new Chart(document.getElementById('t8MotChart'), {{
      type: 'bar',
      data: {{ labels: T8_MOT_LBLS, datasets: [{{
        label: 'Avg Score', data: T8_MOT_DATA,
        backgroundColor: T8_MOT_DATA.map(v => v >= 4 ? G : v >= 3 ? a80(B) : v >= 2 ? a80(O) : R),
        borderRadius: 4
      }}] }},
      options: {{ responsive:true, maintainAspectRatio:false,
        plugins:{{ legend:{{display:false}} }},
        scales:{{ x:{{ticks:{{font:{{size:12}}}},grid:{{display:false}}}},
          y:{{ min:0, max:5, ticks:{{stepSize:1,font:{{size:10}},callback:v=>v+'/5'}}, grid:{{color:'#f0f0f0'}} }} }}
      }}
    }});
  }}

  // Domain coverage distribution
  if (document.getElementById('t8CovChart')) {{
    new Chart(document.getElementById('t8CovChart'), {{
      type: 'bar',
      data: {{ labels: T8_COV_LBLS, datasets: [{{
        label: 'Leads', data: T8_COV_DATA,
        backgroundColor: T8_COV_DATA.map((_,i) => i===0?R:i===1?a80(O):i<=2?a80(B):G),
        borderRadius: 4
      }}] }},
      options: {{ responsive:true, maintainAspectRatio:false,
        plugins:{{ legend:{{display:false}} }},
        scales:{{ x:{{ticks:{{font:{{size:11}}}},grid:{{display:false}}}}, y:{{ticks:{{stepSize:1,font:{{size:10}}}},grid:{{color:'#f0f0f0'}}}} }}
      }}
    }});
  }}




  /* ── A: Domain Coverage Radar ── */
  if (document.getElementById('t8RadarChart') && T8_RADAR_LBLS.length) {{
    new Chart(document.getElementById('t8RadarChart'), {{
      type: 'radar',
      data: {{
        labels: T8_RADAR_LBLS,
        datasets: [{{
          label: '% of notes covering domain',
          data: T8_RADAR_DATA,
          backgroundColor: 'rgba(0,171,199,0.15)',
          borderColor: B, borderWidth: 2,
          pointBackgroundColor: B, pointRadius: 4
        }}]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        scales: {{ r: {{ min: 0, max: 100,
          ticks: {{ stepSize: 25, font: {{ size: 10 }}, callback: v => v + '%' }},
          pointLabels: {{ font: {{ size: 11, weight: '600' }} }}
        }} }},
        plugins: {{ legend: {{ display: false }},
          tooltip: {{ callbacks: {{ label: ctx => ctx.dataset.label + ': ' + ctx.parsed.r + '%' }} }}
        }}
      }}
    }});
  }}

  /* ── B: Scatter — Quality vs. Lead Age ── */
  if (document.getElementById('t8ScatterAgeChart')) {{
    new Chart(document.getElementById('t8ScatterAgeChart'), {{
      type: 'scatter',
      data: {{ datasets: [{{
        label: 'Lead',
        data: T8_SCATTER_AGE,
        backgroundColor: T8_SCATTER_AGE.map(d => d.x >= 4 ? G + '99' : d.x >= 3 ? B + '99' : R + '99'),
        pointRadius: 6, pointHoverRadius: 8
      }}] }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{ legend: {{ display: false }},
          tooltip: {{ callbacks: {{ label: ctx => `Score ${{ctx.parsed.x}} → ${{ctx.parsed.y}} days old` }} }}
        }},
        scales: {{
          x: {{ min: 0.5, max: 5.5, title: {{ display: true, text: 'Notes Quality Score', font: {{ size: 11 }} }},
            ticks: {{ stepSize: 1, callback: v => 'Score ' + v }}, grid: {{ display: false }} }},
          y: {{ title: {{ display: true, text: 'Lead Age (days)', font: {{ size: 11 }} }},
            ticks: {{ font: {{ size: 10 }} }}, grid: {{ color: '#f0f0f0' }} }}
        }}
      }}
    }});
  }}

  /* ── C: Won/Lost Grouped Bar by Score ── */
  if (document.getElementById('t8OutcomeChart')) {{
    const scoreKeys = ['1','2','3','4','5'];
    new Chart(document.getElementById('t8OutcomeChart'), {{
      type: 'bar',
      data: {{
        labels: scoreKeys.map(s => 'Score ' + s),
        datasets: [
          {{ label: 'Won',  data: scoreKeys.map(s => (T8_OUTCOME[s]||{{}}).Won||0),  backgroundColor: G + 'CC', borderRadius: 3 }},
          {{ label: 'Lost', data: scoreKeys.map(s => (T8_OUTCOME[s]||{{}}).Lost||0), backgroundColor: R + 'CC', borderRadius: 3 }}
        ]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{ legend: {{ position: 'top', labels: {{ font: {{ size: 11 }} }} }} }},
        scales: {{
          x: {{ ticks: {{ font: {{ size: 11 }} }}, grid: {{ display: false }} }},
          y: {{ ticks: {{ stepSize: 1, font: {{ size: 10 }} }}, grid: {{ color: '#f0f0f0' }} }}
        }}
      }}
    }});
  }}

  /* ── E: Sentiment Grouped Bar by Score ── */
  if (document.getElementById('t8SentimentChart')) {{
    const sKeys = ['1','2','3','4','5'];
    new Chart(document.getElementById('t8SentimentChart'), {{
      type: 'bar',
      data: {{
        labels: sKeys.map(s => 'Score ' + s),
        datasets: [
          {{ label: 'Positive',   data: sKeys.map(s => (T8_SENTIMENT[s]||{{}}).positive||0),  backgroundColor: G + 'BB', borderRadius: 3 }},
          {{ label: 'Needs Info', data: sKeys.map(s => (T8_SENTIMENT[s]||{{}}).needs_info||0), backgroundColor: R + 'BB', borderRadius: 3 }},
          {{ label: 'Other',      data: sKeys.map(s => (T8_SENTIMENT[s]||{{}}).other||0),      backgroundColor: GY + 'BB', borderRadius: 3 }}
        ]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{ legend: {{ position: 'top', labels: {{ font: {{ size: 11 }} }} }} }},
        scales: {{
          x: {{ ticks: {{ font: {{ size: 11 }} }}, grid: {{ display: false }} }},
          y: {{ ticks: {{ stepSize: 1, font: {{ size: 10 }} }}, grid: {{ color: '#f0f0f0' }} }}
        }}
      }}
    }});
  }}

  /* ── D: Technical Gap Bubble render (pure DOM, no Chart.js needed) ── */
  (function() {{
    const wrap = document.getElementById('t8BubbleWrap');
    if (!wrap || !T8_MISSING.length) {{
      if (wrap) wrap.innerHTML = '<span style="color:#aaa;font-size:13px">No partial lead data yet</span>';
      return;
    }}
    const maxCount = T8_MISSING[0].count || 1;
    T8_MISSING.forEach(item => {{
      const intensity = item.count / maxCount;
      const size = Math.round(28 + intensity * 36);          // 28px – 64px font
      const alpha = Math.round(60 + intensity * 195);          // 60–255 range
      const el = document.createElement('div');
      el.innerHTML = `<div>${{item.field}}</div><div style="font-size:10px;opacity:.85">${{item.count}}</div>`;
      el.style.cssText = `display:inline-flex;flex-direction:column;align-items:center;justify-content:center;\
width:${{size}}px;height:${{size}}px;border-radius:50%;background:rgba(211,84,0,${{alpha/255}});\
color:#fff;margin:6px;cursor:default;font-size:${{Math.round(size*0.28)}}px;font-weight:700;text-align:center`;
      el.title = `${{item.field}}: missing in ${{item.count}} leads`;
      wrap.appendChild(el);
    }});
  }})();

}})(); /* ── close outer Tab 8 IIFE ── */

</script>
</body>
</html>
"""
