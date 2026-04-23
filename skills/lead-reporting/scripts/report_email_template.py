"""
report_email_template.py
-------------------------
GWC-branded HTML weekly lead report — designed to be sent as an email
body to the manager (hebah.yasin@gwclogistics.com) via OUTLOOK_SEND_EMAIL.

Usage:
    from report_email_template import build_report_email
    subject, html = build_report_email(report_data)
"""

from datetime import datetime

GWC_GREEN    = "#3FAE2A"
GWC_BLUE     = "#00ABC7"
GWC_DARK     = "#333333"
GWC_LIGHT    = "#F7F7F7"
GWC_BORDER   = "#E0E0E0"
GWC_WARN     = "#FFF3CD"
GWC_WARN_BD  = "#FFC107"
GWC_URGENT   = "#FDECEA"
GWC_RED      = "#D32F2F"
CURRENT_YEAR = datetime.utcnow().year

STATUS_COLOURS = {
    "NO_ACTION":  ("#E3F2FD", "#1565C0"),
    "ENGAGED":    ("#E8F5E9", "#2E7D32"),
    "QUOTED":     ("#FFF8E1", "#F57F17"),
    "FOLLOW_UP":  ("#F3E5F5", "#6A1B9A"),
    "WON_LOSS":   ("#EFEBE9", "#4E342E"),
    "REJECTED":   ("#FAFAFA", "#9E9E9E"),
}


def _pill(status: str) -> str:
    bg, fg = STATUS_COLOURS.get(status, ("#eee", "#333"))
    return (f'<span style="background:{bg}; color:{fg}; padding:2px 8px; '
            f'border-radius:12px; font-size:12px; font-weight:600;">{status}</span>')


def _shell(content: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    body {{ margin:0; padding:0; background:#f0f0f0;
           font-family:'Proxima Nova',Arial,sans-serif; color:{GWC_DARK}; }}
    .wrap {{ max-width:680px; margin:24px auto; background:#fff;
             border-radius:6px; overflow:hidden;
             box-shadow:0 2px 10px rgba(0,0,0,0.10); }}
    .hdr  {{ background:{GWC_GREEN}; padding:22px 32px; }}
    .hdr h1 {{ margin:0; font-size:22px; color:#fff; font-weight:700; }}
    .hdr p  {{ margin:4px 0 0; font-size:13px; color:rgba(255,255,255,0.85); }}
    .body {{ padding:28px 32px; }}
    h2 {{ font-size:15px; font-weight:700; color:{GWC_BLUE}; margin:28px 0 10px;
          text-transform:uppercase; letter-spacing:.5px; border-bottom:2px solid {GWC_BLUE};
          padding-bottom:4px; }}
    table.data {{ width:100%; border-collapse:collapse; font-size:13px; margin:0 0 8px; }}
    table.data th {{ background:{GWC_LIGHT}; text-align:left; padding:8px 10px;
                     border-bottom:2px solid {GWC_BORDER}; font-weight:700; color:#555; }}
    table.data td {{ padding:8px 10px; border-bottom:1px solid {GWC_BORDER};
                     vertical-align:middle; }}
    table.data tr:last-child td {{ border-bottom:none; }}
    .metric-row {{ display:flex; gap:12px; flex-wrap:wrap; margin:0 0 12px; }}
    .metric {{ flex:1; min-width:120px; background:{GWC_LIGHT};
               border-left:4px solid {GWC_GREEN}; border-radius:4px;
               padding:12px 16px; }}
    .metric .val {{ font-size:28px; font-weight:700; color:{GWC_GREEN}; line-height:1; }}
    .metric .lbl {{ font-size:12px; color:#777; margin-top:4px; }}
    .funnel-bar {{ margin:4px 0; }}
    .funnel-bar .label {{ display:inline-block; width:90px; font-size:13px; color:#555; }}
    .funnel-bar .bar-wrap {{ display:inline-block; width:360px; background:{GWC_LIGHT};
                              border-radius:4px; overflow:hidden; vertical-align:middle; }}
    .funnel-bar .bar {{ height:18px; border-radius:4px; }}
    .funnel-bar .num {{ display:inline-block; width:24px; text-align:right;
                        font-size:13px; font-weight:700; margin-left:6px; }}
    .alert-box {{ border-radius:4px; padding:12px 16px; margin:10px 0;
                  font-size:13px; line-height:1.6; }}
    .warn  {{ background:{GWC_WARN};   border-left:4px solid {GWC_WARN_BD}; }}
    .crit  {{ background:{GWC_URGENT}; border-left:4px solid {GWC_RED}; color:#7B1818; }}
    .ftr   {{ background:{GWC_LIGHT}; padding:14px 32px; font-size:11px;
               color:#999; border-top:1px solid {GWC_BORDER}; text-align:center; }}
  </style>
</head>
<body>
  <div class="wrap">
    {content}
    <div class="ftr">
      GWC Logistics · Lead Maturity Automation · Automated Weekly Report<br>
      © {CURRENT_YEAR} GWC. Do not reply to this email.
    </div>
  </div>
</body>
</html>"""


def _funnel_bar(label: str, count: int, max_count: int, colour: str) -> str:
    pct = int((count / max_count) * 100) if max_count else 0
    return (
        f'<div class="funnel-bar">'
        f'<span class="label">{label}</span>'
        f'<span class="bar-wrap">'
        f'<div class="bar" style="width:{pct}%; background:{colour};"></div>'
        f'</span>'
        f'<span class="num">{count}</span>'
        f'</div>'
    )


FUNNEL_COLOURS = {
    "NO_ACTION":  "#90CAF9",
    "ENGAGED":    GWC_GREEN,
    "QUOTED":     "#FFD54F",
    "FOLLOW_UP":  "#CE93D8",
    "WON_LOSS":   "#A1887F",
}


def build_report_email(data: dict) -> tuple[str, str]:
    """
    Build the weekly report email.

    Args:
        data: output of report_builder.build_report(store)

    Returns:
        (subject, html_body)
    """
    m        = data["meta"]
    funnel   = data["funnel"]
    by_rep   = data["by_rep"]
    by_mode  = data["by_mode"]
    dark     = data["dark_leads"]
    stale    = data["stale_leads"]
    activity = data["recent_activity"]

    subject = (
        f"[GWC Lead Report] Week of {m['week_end']} — "
        f"{m['total_leads']} leads, {data['new_this_period']} new"
    )

    # ── Key metrics ───────────────────────────────────────────────────────────
    active_count = sum(
        data["status_counts"].get(s, 0)
        for s in ("ENGAGED", "QUOTED", "FOLLOW_UP")
    )
    metrics_html = f"""
    <div class="metric-row">
      <div class="metric">
        <div class="val">{m['total_leads']}</div>
        <div class="lbl">Total Leads</div>
      </div>
      <div class="metric">
        <div class="val">{data['new_this_period']}</div>
        <div class="lbl">New This Week</div>
      </div>
      <div class="metric">
        <div class="val">{active_count}</div>
        <div class="lbl">Active Pipeline</div>
      </div>
      <div class="metric" style="border-left-color:{GWC_BLUE}">
        <div class="val" style="color:{GWC_BLUE}">{data['won']}</div>
        <div class="lbl">Won Deals</div>
      </div>
    </div>"""

    # ── Pipeline funnel ───────────────────────────────────────────────────────
    max_count = max((f["count"] for f in funnel), default=1) or 1
    funnel_html = "".join(
        _funnel_bar(f["status"], f["count"], max_count,
                    FUNNEL_COLOURS.get(f["status"], "#ccc"))
        for f in funnel
    )
    if data["rejected_count"]:
        funnel_html += (
            f'<p style="font-size:12px;color:#999;margin:8px 0 0">'
            f'{data["rejected_count"]} lead(s) rejected (not shown in funnel)</p>'
        )

    # ── By rep table ──────────────────────────────────────────────────────────
    rep_rows = ""
    for r in by_rep:
        name = r["rep_name"] or r["rep_email"]
        rep_rows += (
            f"<tr><td>{name}</td>"
            f"<td style='text-align:center'>{r['total']}</td>"
            f"<td style='text-align:center'>{r['engaged']}</td>"
            f"<td style='text-align:center'>{r['quoted']}</td>"
            f"<td style='text-align:center'>{r['follow_up']}</td>"
            f"<td style='text-align:center'>{r['won']}</td></tr>"
        )
    rep_table = f"""
    <table class="data">
      <tr>
        <th>Rep</th><th style="text-align:center">Total</th>
        <th style="text-align:center">Engaged</th><th style="text-align:center">Quoted</th>
        <th style="text-align:center">Follow-Up</th><th style="text-align:center">Won</th>
      </tr>
      {rep_rows or '<tr><td colspan="6" style="color:#999">No reps assigned yet.</td></tr>'}
    </table>"""

    # ── By mode ───────────────────────────────────────────────────────────────
    mode_rows = "".join(
        f"<tr><td>{m2['mode']}</td><td style='text-align:center'>{m2['count']}</td></tr>"
        for m2 in by_mode
    )
    mode_table = f"""
    <table class="data" style="max-width:320px">
      <tr><th>Mode of Freight</th><th style="text-align:center">Leads</th></tr>
      {mode_rows or '<tr><td colspan="2" style="color:#999">No data.</td></tr>'}
    </table>"""

    # ── Alerts ────────────────────────────────────────────────────────────────
    alerts_html = ""
    if dark:
        alerts_html += (
            f'<div class="alert-box crit">'
            f'<strong>🔴 {len(dark)} dark lead(s) detected</strong><br>'
            + ", ".join(f"{d['gwc_id']} ({d['company']}, {d['days_silent']}d silent)"
                        for d in dark)
            + "</div>"
        )
    if stale:
        alerts_html += (
            f'<div class="alert-box warn">'
            f'<strong>🟡 {len(stale)} stale lead(s) beyond threshold</strong><br>'
            + ", ".join(
                f"{s['gwc_id']} ({s['status']}, {s['days_in_status']}d)"
                for s in stale
            )
            + "</div>"
        )
    if not alerts_html:
        alerts_html = '<p style="color:#2E7D32;font-size:13px">✅ No dark or stale leads.</p>'

    # ── Recent activity ───────────────────────────────────────────────────────
    act_rows = ""
    for a in activity[:10]:
        ts_short = a["ts"][:16].replace("T", " ") if a["ts"] else "—"
        act_rows += (
            f"<tr><td style='color:#777;font-size:12px'>{ts_short}</td>"
            f"<td>{a['gwc_id']}</td>"
            f"<td><code style='font-size:11px'>{a['type']}</code></td></tr>"
        )
    act_table = f"""
    <table class="data">
      <tr><th>Time (UTC)</th><th>GWC ID</th><th>Event</th></tr>
      {act_rows or '<tr><td colspan="3" style="color:#999">No activity this week.</td></tr>'}
    </table>"""

    # ── Assemble ──────────────────────────────────────────────────────────────
    content = f"""
    <div class="hdr">
      <h1>📊 Weekly Lead Report</h1>
      <p>{m['week_start']} – {m['week_end']} · Generated {m['generated_at']}</p>
    </div>
    <div class="body">
      <h2>Summary</h2>
      {metrics_html}

      <h2>Pipeline Funnel</h2>
      {funnel_html}

      <h2>Alerts</h2>
      {alerts_html}

      <h2>Performance by Rep</h2>
      {rep_table}

      <h2>Leads by Mode of Freight</h2>
      {mode_table}

      <h2>Recent Activity (last {m['period_days']} days)</h2>
      {act_table}
    </div>"""

    return subject, _shell(content)
