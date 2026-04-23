"""
followup_email_templates.py
----------------------------
GWC-branded HTML reminder email templates for the lead follow-up cadence skill.

Three reminder types, one per active status:
  ENGAGED   → "Please send a quotation" (rep hasn't quoted yet)
  QUOTED    → "Please chase the customer" (customer hasn't replied to the quote)
  FOLLOW_UP → "Please close this deal" (deal not confirmed after customer replied)

All emails:
  - Are sent FROM Sales.rfq@gwclogistics.com TO the assigned rep
  - Include the GWC ID, customer name, company, route, and days elapsed
  - Reinforce the CC rule so reps stay compliant
  - Use GWC brand colours (green #3FAE2A, blue #00ABC7)
"""

from datetime import datetime

# ── Brand constants ───────────────────────────────────────────────────────────
GWC_GREEN   = "#3FAE2A"
GWC_BLUE    = "#00ABC7"
GWC_DARK    = "#333333"
GWC_LIGHT   = "#F5F5F5"
GWC_WARN    = "#FFF3CD"
GWC_WARN_BD = "#FFC107"
GWC_URGENT  = "#FDECEA"
GWC_URGENT_BD = "#D32F2F"
SHARED_MAILBOX = "Sales.rfq@gwclogistics.com"
CURRENT_YEAR = datetime.utcnow().year


# ── Base HTML shell ───────────────────────────────────────────────────────────

def _html_shell(content: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    body {{ margin: 0; padding: 0; background: #f0f0f0;
           font-family: 'Proxima Nova', Arial, sans-serif; color: {GWC_DARK}; }}
    .wrapper {{ max-width: 620px; margin: 30px auto; background: #fff;
                border-radius: 6px; overflow: hidden;
                box-shadow: 0 2px 8px rgba(0,0,0,0.12); }}
    .header {{ background: {GWC_GREEN}; padding: 20px 30px; }}
    .header h1 {{ margin: 0; font-size: 20px; color: #fff; font-weight: 700; }}
    .header p  {{ margin: 4px 0 0; font-size: 13px; color: rgba(255,255,255,0.85); }}
    .body {{ padding: 28px 30px; }}
    .lead-card {{ background: {GWC_LIGHT}; border-left: 4px solid {GWC_BLUE};
                  border-radius: 4px; padding: 14px 18px; margin: 18px 0; }}
    .lead-card table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    .lead-card td {{ padding: 4px 0; vertical-align: top; }}
    .lead-card td:first-child {{ color: #777; width: 38%; }}
    .lead-card td:last-child {{ font-weight: 600; }}
    .alert {{ border-radius: 4px; padding: 14px 18px; margin: 18px 0;
              font-size: 14px; line-height: 1.6; }}
    .alert-warn   {{ background: {GWC_WARN};   border-left: 4px solid {GWC_WARN_BD}; }}
    .alert-urgent {{ background: {GWC_URGENT}; border-left: 4px solid {GWC_URGENT_BD}; color: #7B1818; }}
    .cc-box {{ background: #E8F5E9; border: 1px dashed {GWC_GREEN};
               border-radius: 4px; padding: 12px 16px; margin: 18px 0;
               font-size: 13px; color: #2E7D32; }}
    .cc-box strong {{ display: block; margin-bottom: 4px; }}
    p {{ font-size: 15px; line-height: 1.6; margin: 12px 0; }}
    .footer {{ background: #F5F5F5; padding: 14px 30px; font-size: 12px;
               color: #999; border-top: 1px solid #e0e0e0; text-align: center; }}
  </style>
</head>
<body>
  <div class="wrapper">
    {content}
    <div class="footer">
      GWC Logistics · Gulf Warehousing Company · Ras Bu Fontas, Doha, Qatar<br>
      © {CURRENT_YEAR} GWC. This is an automated reminder from the Lead Maturity System.
    </div>
  </div>
</body>
</html>"""


def _lead_card(lead: dict) -> str:
    route = f"{lead.get('from_country','?')} → {lead.get('to_country','?')}"
    mode  = lead.get("mode_of_freight", "") or "—"
    wt    = lead.get("weight_kg", "") or "—"
    wt_str = f"{float(wt):,.0f} KG" if wt and wt != "—" else "—"
    return f"""
    <div class="lead-card">
      <table>
        <tr><td>GWC ID</td><td>{lead.get('gwc_id','')}</td></tr>
        <tr><td>Customer</td><td>{lead.get('contact_name','')} — {lead.get('company_name','')}</td></tr>
        <tr><td>Route</td><td>{route}</td></tr>
        <tr><td>Mode</td><td>{mode}</td></tr>
        <tr><td>Weight</td><td>{wt_str}</td></tr>
        <tr><td>Product</td><td>{lead.get('product','') or '—'}</td></tr>
      </table>
    </div>"""


def _cc_reminder() -> str:
    return f"""
    <div class="cc-box">
      <strong>⚠️ Tracking reminder</strong>
      Always CC <strong>{SHARED_MAILBOX}</strong> on every email you send to this customer.
      Include <strong>{{}}</strong> in the subject line so we can track the lead.
    </div>"""


# ── Template 1: ENGAGED → remind rep to send a quotation ─────────────────────

def build_engaged_reminder(lead: dict, rep_name: str,
                           days_elapsed: int, threshold_day: int) -> tuple[str, str]:
    """
    Subject + HTML body for an ENGAGED lead where no quote has been sent yet.
    Fired at day 3, 7, 14.
    """
    gwc_id = lead["gwc_id"]
    urgency_class = "alert-urgent" if days_elapsed >= 14 else "alert-warn"
    urgency_label = "🔴 Overdue" if days_elapsed >= 14 else "🟡 Reminder"

    subject = f"[{urgency_label}] Please send a quote — {gwc_id} ({days_elapsed} days, no proposal sent)"

    body_content = f"""
    <div class="header">
      <h1>Quotation Reminder</h1>
      <p>This lead has been engaged for <strong>{days_elapsed} day{'s' if days_elapsed != 1 else ''}</strong> without a quotation sent.</p>
    </div>
    <div class="body">
      <p>Hi {rep_name.split()[0]},</p>
      <p>This is a reminder that the following lead is awaiting a <strong>quotation from you</strong>.
         The customer has not yet received a proposal and {days_elapsed} days have passed since you first engaged.</p>

      {_lead_card(lead)}

      <div class="alert {urgency_class}">
        <strong>{urgency_label} — Day {days_elapsed}</strong><br>
        Please send your quotation to the customer as soon as possible.
        If you need additional information from them first, send a polite request today.
      </div>

      <p><strong>What to do:</strong></p>
      <ul style="font-size:14px; line-height:1.8; padding-left:20px;">
        <li>Prepare and send a freight quotation or rate sheet to the customer</li>
        <li>If missing shipment details, request them clearly in one email</li>
        <li>CC <strong>{SHARED_MAILBOX}</strong> on all customer emails</li>
        <li>Include <strong>{gwc_id}</strong> in the subject line</li>
      </ul>

      {_cc_reminder().format(gwc_id)}

      <p>If this lead should be closed or reassigned, please inform your manager.</p>
      <p>Thank you,<br><strong>GWC Lead Maturity System</strong></p>
    </div>"""

    return subject, _html_shell(body_content)


# ── Template 2: QUOTED → remind rep to chase the customer ────────────────────

def build_quoted_reminder(lead: dict, rep_name: str,
                          days_elapsed: int, threshold_day: int) -> tuple[str, str]:
    """
    Subject + HTML body for a QUOTED lead with no customer reply.
    Fired at day 2, 5, 10.
    """
    gwc_id = lead["gwc_id"]
    urgency_class = "alert-urgent" if days_elapsed >= 10 else "alert-warn"
    urgency_label = "🔴 Urgent" if days_elapsed >= 10 else "🟡 Chaser"

    subject = f"[{urgency_label}] Follow up with customer — {gwc_id} ({days_elapsed} days since quote)"

    body_content = f"""
    <div class="header">
      <h1>Customer Chaser Reminder</h1>
      <p>Your quotation was sent <strong>{days_elapsed} day{'s' if days_elapsed != 1 else ''} ago</strong> — no customer reply yet.</p>
    </div>
    <div class="body">
      <p>Hi {rep_name.split()[0]},</p>
      <p>You sent a quotation to the customer below, but no reply has been received yet.
         A follow-up message today could make the difference.</p>

      {_lead_card(lead)}

      <div class="alert {urgency_class}">
        <strong>{urgency_label} — {days_elapsed} days since quote</strong><br>
        Customers often need a gentle nudge. A brief, professional follow-up email
        can increase conversion rates significantly.
      </div>

      <p><strong>Suggested follow-up action:</strong></p>
      <ul style="font-size:14px; line-height:1.8; padding-left:20px;">
        <li>Send a polite follow-up referencing your previous quotation</li>
        <li>Offer to answer any questions or adjust the proposal if needed</li>
        <li>CC <strong>{SHARED_MAILBOX}</strong> on all customer emails</li>
        <li>Include <strong>{gwc_id}</strong> in the subject line</li>
      </ul>

      {_cc_reminder().format(gwc_id)}

      <p>If the customer has already responded via another channel, please update
         the lead status accordingly.</p>
      <p>Thank you,<br><strong>GWC Lead Maturity System</strong></p>
    </div>"""

    return subject, _html_shell(body_content)


# ── Template 3: FOLLOW_UP → remind rep to close the deal ─────────────────────

def build_followup_reminder(lead: dict, rep_name: str,
                            days_elapsed: int, threshold_day: int) -> tuple[str, str]:
    """
    Subject + HTML body for a FOLLOW_UP lead that has not been closed.
    Fired at day 5, 10, 20.
    """
    gwc_id = lead["gwc_id"]
    urgency_class = "alert-urgent" if days_elapsed >= 20 else "alert-warn"
    urgency_label = "🔴 Critical" if days_elapsed >= 20 else "🟡 Action needed"

    subject = f"[{urgency_label}] Close this deal — {gwc_id} ({days_elapsed} days in follow-up)"

    body_content = f"""
    <div class="header">
      <h1>Deal Closure Reminder</h1>
      <p>This lead has been in follow-up for <strong>{days_elapsed} day{'s' if days_elapsed != 1 else ''}</strong>. Time to close.</p>
    </div>
    <div class="body">
      <p>Hi {rep_name.split()[0]},</p>
      <p>The customer below has been engaged in follow-up discussions for {days_elapsed} days.
         Please take action today to either confirm the deal or close it as won/lost.</p>

      {_lead_card(lead)}

      <div class="alert {urgency_class}">
        <strong>{urgency_label} — {days_elapsed} days in FOLLOW_UP</strong><br>
        Long follow-up periods reduce conversion. Please push to get a decision from the customer.
      </div>

      <p><strong>Action required:</strong></p>
      <ul style="font-size:14px; line-height:1.8; padding-left:20px;">
        <li>Confirm the deal is <strong>WON</strong> (booking confirmed, PO received) — reply to confirm</li>
        <li>Or mark as <strong>LOST</strong> if the customer has declined or gone silent</li>
        <li>If still negotiating, send an update so the system can track progress</li>
        <li>CC <strong>{SHARED_MAILBOX}</strong> on all customer emails</li>
        <li>Include <strong>{gwc_id}</strong> in the subject line</li>
      </ul>

      {_cc_reminder().format(gwc_id)}

      <p>Please reply to this email or contact your manager to update the lead status.</p>
      <p>Thank you,<br><strong>GWC Lead Maturity System</strong></p>
    </div>"""

    return subject, _html_shell(body_content)


# ── Dispatcher ────────────────────────────────────────────────────────────────

def build_reminder_email(task: dict) -> tuple[str, str]:
    """
    Dispatch to the correct template based on task["status"].

    Args:
        task: reminder task dict from cadence_rules.get_leads_needing_reminder()

    Returns:
        (subject, html_body)
    """
    status        = task["status"]
    lead          = task["lead"]
    rep_name      = task["rep_name"]
    days_elapsed  = task["days_elapsed"]
    threshold_day = task["threshold_day"]

    if status == "ENGAGED":
        return build_engaged_reminder(lead, rep_name, days_elapsed, threshold_day)
    elif status == "QUOTED":
        return build_quoted_reminder(lead, rep_name, days_elapsed, threshold_day)
    elif status == "FOLLOW_UP":
        return build_followup_reminder(lead, rep_name, days_elapsed, threshold_day)
    else:
        raise ValueError(f"No reminder template for status: {status!r}")
