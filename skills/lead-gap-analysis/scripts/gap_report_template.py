"""
gap_report_template.py
-----------------------
GWC-branded HTML monthly gap analysis report — sent as an email body
to the manager (hebah.yasin@gwclogistics.com).

Usage:
    from gap_report_template import build_gap_email
    subject, html = build_gap_email(gaps)
"""

from datetime import datetime

GWC_GREEN  = "#3FAE2A"
GWC_BLUE   = "#00ABC7"
GWC_DARK   = "#333333"
GWC_LIGHT  = "#F7F7F7"
GWC_BORDER = "#E0E0E0"
GWC_WARN   = "#FFF3CD"
GWC_WARN_BD= "#FFC107"
GWC_RED    = "#D32F2F"
GWC_URGENT = "#FDECEA"
CURRENT_YEAR = datetime.utcnow().year


def _shell(content: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    body  {{ margin:0; padding:0; background:#f0f0f0;
             font-family:'Proxima Nova',Arial,sans-serif; color:{GWC_DARK}; }}
    .wrap {{ max-width:700px; margin:24px auto; background:#fff;
             border-radius:6px; overflow:hidden;
             box-shadow:0 2px 10px rgba(0,0,0,0.10); }}
    .hdr  {{ background:{GWC_RED}; padding:22px 32px; }}
    .hdr h1 {{ margin:0; font-size:22px; color:#fff; font-weight:700; }}
    .hdr p  {{ margin:4px 0 0; font-size:13px; color:rgba(255,255,255,0.85); }}
    .body {{ padding:28px 32px; }}
    h2 {{ font-size:14px; font-weight:700; color:{GWC_DARK}; margin:28px 0 10px;
          text-transform:uppercase; letter-spacing:.5px; border-bottom:2px solid {GWC_BORDER};
          padding-bottom:4px; }}
    h2 .icon {{ color:{GWC_RED}; }}
    table.data {{ width:100%; border-collapse:collapse; font-size:13px; margin:0 0 8px; }}
    table.data th {{ background:{GWC_LIGHT}; text-align:left; padding:8px 10px;
                     border-bottom:2px solid {GWC_BORDER}; font-weight:700; color:#555; }}
    table.data td {{ padding:8px 10px; border-bottom:1px solid {GWC_BORDER}; vertical-align:middle; }}
    table.data tr:last-child td {{ border-bottom:none; }}
    .badge {{ display:inline-block; padding:2px 8px; border-radius:12px;
               font-size:11px; font-weight:700; }}
    .badge-red  {{ background:{GWC_URGENT}; color:{GWC_RED}; }}
    .badge-warn {{ background:{GWC_WARN};   color:#7B5B00; }}
    .badge-ok   {{ background:#E8F5E9;      color:#2E7D32; }}
    .scorecard  {{ display:flex; flex-wrap:wrap; gap:10px; margin:0 0 16px; }}
    .sc-item    {{ flex:1; min-width:110px; border-radius:4px; padding:12px 14px;
                   text-align:center; }}
    .sc-item .val {{ font-size:26px; font-weight:700; line-height:1; }}
    .sc-item .lbl {{ font-size:11px; color:#777; margin-top:4px; }}
    .sc-red  {{ background:{GWC_URGENT}; border-top:3px solid {GWC_RED}; }}
    .sc-warn {{ background:{GWC_WARN};   border-top:3px solid {GWC_WARN_BD}; }}
    .sc-ok   {{ background:#E8F5E9;      border-top:3px solid {GWC_GREEN}; }}
    .sc-red .val  {{ color:{GWC_RED}; }}
    .sc-warn .val {{ color:#7B5B00; }}
    .sc-ok .val   {{ color:{GWC_GREEN}; }}
    .ftr {{ background:{GWC_LIGHT}; padding:14px 32px; font-size:11px;
             color:#999; border-top:1px solid {GWC_BORDER}; text-align:center; }}
    p {{ font-size:14px; line-height:1.6; margin:8px 0; }}
    .all-ok {{ background:#E8F5E9; border-left:4px solid {GWC_GREEN};
                border-radius:4px; padding:14px 18px; color:#2E7D32; font-weight:600; }}
  </style>
</head>
<body>
  <div class="wrap">
    {content}
    <div class="ftr">
      GWC Logistics · Lead Maturity Automation · Monthly Gap Analysis<br>
      © {CURRENT_YEAR} GWC. Do not reply to this email.
    </div>
  </div>
</body>
</html>"""


def _gap_table(rows: list, columns: list) -> str:
    """Render a simple gap table. rows is a list of dicts, columns is [(key, header)]."""
    if not rows:
        return '<p style="color:#2E7D32;font-size:13px">✅ No issues found.</p>'
    head = "".join(f"<th>{h}</th>" for _, h in columns)
    body = ""
    for r in rows:
        cells = "".join(f"<td>{r.get(k, '—')}</td>" for k, _ in columns)
        body += f"<tr>{cells}</tr>"
    return f'<table class="data"><tr>{head}</tr>{body}</table>'


def build_gap_email(gaps: dict) -> tuple[str, str]:
    """
    Build the monthly gap analysis email.

    Args:
        gaps: output of gap_detector.detect_gaps(store)

    Returns:
        (subject, html_body)
    """
    now_str = datetime.utcnow().strftime("%B %Y")
    total   = gaps["total_gaps"]
    severity = "🔴 Action Required" if total > 3 else ("🟡 Attention Needed" if total > 0 else "✅ All Clear")

    subject = f"[GWC Gap Analysis] {now_str} — {total} gap item(s) found · {severity}"

    counts = gaps["summary_counts"]

    def _cls(n):
        return "sc-red" if n > 0 else "sc-ok"

    # Scorecard
    scorecard = f"""
    <div class="scorecard">
      <div class="sc-item {_cls(counts['unroutable'])}">
        <div class="val">{counts['unroutable']}</div>
        <div class="lbl">Unroutable</div>
      </div>
      <div class="sc-item {_cls(counts['dark_leads'])}">
        <div class="val">{counts['dark_leads']}</div>
        <div class="lbl">Dark Leads</div>
      </div>
      <div class="sc-item {_cls(counts['stale_engaged'] + counts['stale_quoted'] + counts['stale_follow_up'])}">
        <div class="val">{counts['stale_engaged'] + counts['stale_quoted'] + counts['stale_follow_up']}</div>
        <div class="lbl">Stale Leads</div>
      </div>
      <div class="sc-item {_cls(counts['missing_fields'])}">
        <div class="val">{counts['missing_fields']}</div>
        <div class="lbl">Missing Fields</div>
      </div>
      <div class="sc-item {_cls(counts['long_age'])}">
        <div class="val">{counts['long_age']}</div>
        <div class="lbl">Aged 30d+</div>
      </div>
      <div class="sc-item {'sc-warn' if gaps['high_rejection'] else 'sc-ok'}">
        <div class="val">{gaps['rejection_rate_pct']}%</div>
        <div class="lbl">Rejection Rate</div>
      </div>
    </div>"""

    if total == 0:
        body_content = '<div class="all-ok">✅ Excellent — no pipeline gaps detected this month. All leads are on track.</div>'
    else:
        # Unroutable
        unreachable_section = f"""
        <h2><span class="icon">⚠️</span> Unroutable Leads ({counts['unroutable']})</h2>
        <p>These leads arrived but could not be assigned a rep because their destination country
           is not in the country-rep mapping. Manual assignment required.</p>
        {_gap_table(gaps['unroutable'], [
            ('gwc_id','GWC ID'), ('company','Company'), ('route','Route'),
            ('age_days','Age (days)'), ('notes','Notes')
        ])}"""

        # Stale
        stale_section = f"""
        <h2><span class="icon">🕐</span> Stale ENGAGED Leads ({counts['stale_engaged']})</h2>
        <p>No quote sent after 14+ days of engagement.</p>
        {_gap_table(gaps['stale_engaged'], [
            ('gwc_id','GWC ID'),('company','Company'),('route','Route'),
            ('rep','Rep'),('days','Days')
        ])}
        <h2><span class="icon">🕐</span> Stale QUOTED Leads ({counts['stale_quoted']})</h2>
        <p>No customer response after 10+ days since quote sent.</p>
        {_gap_table(gaps['stale_quoted'], [
            ('gwc_id','GWC ID'),('company','Company'),('route','Route'),
            ('rep','Rep'),('days','Days')
        ])}
        <h2><span class="icon">🕐</span> Stale FOLLOW_UP Leads ({counts['stale_follow_up']})</h2>
        <p>Deal not closed after 20+ days in follow-up.</p>
        {_gap_table(gaps['stale_follow_up'], [
            ('gwc_id','GWC ID'),('company','Company'),('route','Route'),
            ('rep','Rep'),('days','Days')
        ])}"""

        # Missing fields
        mf_rows = [
            {
                "gwc_id":  r["gwc_id"],
                "company": r["company"],
                "status":  r["status"],
                "rep":     r["rep"],
                "missing": ", ".join(r["missing"]),
            }
            for r in gaps["missing_fields"]
        ]
        missing_section = f"""
        <h2><span class="icon">📋</span> Missing Required Fields ({counts['missing_fields']})</h2>
        <p>These active leads cannot be fully quoted because required data is missing.
           Reps should request this information from the customer.</p>
        {_gap_table(mf_rows, [
            ('gwc_id','GWC ID'),('company','Company'),('status','Status'),
            ('rep','Rep'),('missing','Missing Fields')
        ])}"""

        # Dark leads
        dark_section = f"""
        <h2><span class="icon">🔕</span> Dark Leads ({counts['dark_leads']})</h2>
        <p>No email thread activity detected for 5+ days on active leads.</p>
        {_gap_table(gaps['dark_leads'], [
            ('gwc_id','GWC ID'),('company','Company'),('status','Status'),
            ('rep','Rep'),('days_silent','Days Silent')
        ])}"""

        # Long age
        long_section = f"""
        <h2><span class="icon">📅</span> Aged Leads — 30+ Days in Pipeline ({counts['long_age']})</h2>
        <p>These leads have been in the pipeline for over 30 days without closure.
           Consider escalating or closing.</p>
        {_gap_table(gaps['long_age'], [
            ('gwc_id','GWC ID'),('company','Company'),('status','Status'),
            ('rep','Rep'),('age','Age (days)')
        ])}"""

        # Rejection rate
        rej_section = ""
        if gaps["high_rejection"]:
            rej_section = f"""
            <h2><span class="icon">🚫</span> High Rejection Rate</h2>
            <p style="color:{GWC_RED};font-weight:600">
              {gaps['rejection_rate_pct']}% of leads are rejected ({gaps['rejected_count']} of {gaps['total_leads']}).
              This exceeds the 30% threshold. Review the HubSpot lead qualification form —
              too many leads are arriving with insufficient data.
            </p>"""

        body_content = (
            unreachable_section + stale_section + missing_section +
            dark_section + long_section + rej_section
        )

    content = f"""
    <div class="hdr">
      <h1>🔍 Monthly Gap Analysis — {now_str}</h1>
      <p>{gaps['total_leads']} total leads · {total} gap item(s) · {severity}</p>
    </div>
    <div class="body">
      <h2>Overview</h2>
      {scorecard}
      {body_content}
    </div>"""

    return subject, _shell(content)
