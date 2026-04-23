"""
analyze_thread.py
-----------------
Builds the AI analysis prompt for Claude to evaluate a CC'd email thread
and determine:
  1. The correct lead status transition (if any)
  2. Whether this is a "dark lead" (no activity from rep/customer in 5+ days)
  3. The deal outcome if WON_LOSS is detected

This is the ONE place in the entire system where AI tokens are intentionally spent.
All other classification is deterministic Python.

Status transition rules:
  NO_ACTION  → ENGAGED    : Rep has sent any response to the customer
  ENGAGED    → QUOTED     : Rep has sent pricing/quotation/proposal
  QUOTED     → FOLLOW_UP  : Customer has replied after receiving a quote
  FOLLOW_UP  → WON_LOSS   : Thread shows deal confirmed OR declined

Usage:
    from analyze_thread import build_analysis_prompt, parse_analysis_result
    prompt = build_analysis_prompt(thread_payload)
    # → feed prompt to Claude
    # → parse Claude's JSON response
    result = parse_analysis_result(claude_response_text)
"""

import json
import re
from typing import Optional


# ── Status transition map ──────────────────────────────────────────────────────

STATUS_ORDER = ["NO_ACTION", "ENGAGED", "QUOTED", "FOLLOW_UP", "WON_LOSS"]

def status_rank(status: str) -> int:
    try:
        return STATUS_ORDER.index(status)
    except ValueError:
        return -1


# ── Prompt builder ────────────────────────────────────────────────────────────

def build_analysis_prompt(thread_payload: dict) -> str:
    """
    Build the Claude analysis prompt for a single lead's email thread.

    The prompt asks Claude to:
    - Analyse the thread chronologically
    - Detect which status the lead should be in
    - Return a structured JSON response

    Args:
        thread_payload: Output from scan_cc_emails.build_thread_payload()

    Returns:
        str: The complete prompt to send to Claude
    """
    gwc_id       = thread_payload["gwc_id"]
    lead_summary = thread_payload["lead_summary"]
    thread       = thread_payload["thread"]
    current_status = lead_summary["current_status"]

    # Format thread messages for the prompt
    thread_text = ""
    for msg in thread:
        if msg.get("is_original_hubspot"):
            continue  # Skip the original HubSpot notification
        thread_text += f"""
--- Email {msg['index']} ---
Date:    {msg['date']}
From:    {msg['sender']} [{msg['role'].upper()}]
Subject: {msg['subject']}
Body:
{msg['body'][:1500]}
"""

    if not thread_text.strip():
        thread_text = "(No CC'd emails found beyond the original HubSpot notification)"

    prompt = f"""You are analysing a freight lead email thread for GWC Logistics.
Your job is to determine the correct lead status based on the email conversation.

## Lead Information
- GWC ID: {gwc_id}
- Company: {lead_summary['company_name']}
- Contact: {lead_summary['contact_name']}
- Route: {lead_summary['from_country']} → {lead_summary['to_country']}
- Mode: {lead_summary['mode_of_freight']}
- Current status in system: {current_status}

## Status Definitions
- NO_ACTION:  Lead arrived, no GWC rep response yet
- ENGAGED:    GWC rep has replied to the customer (any reply counts)
- QUOTED:     GWC rep has sent a price, quotation, rate, or proposal
- FOLLOW_UP:  Customer has replied after receiving a quotation
- WON_LOSS:   Deal is confirmed as won OR customer has declined/cancelled

## Status Transition Rules (one direction only — never go backwards)
NO_ACTION → ENGAGED:   Any email from a GWC rep to the customer
ENGAGED → QUOTED:      Rep email containing pricing, rates, a quotation, or an attached proposal
QUOTED → FOLLOW_UP:    Any customer reply after a quote has been sent
FOLLOW_UP → WON_LOSS:  Clear confirmation of deal won OR clear indication deal is lost

## Email Thread
{thread_text}

## Task
Read the thread carefully and determine:
1. What is the correct current status for this lead?
2. Has the status changed from "{current_status}"?
3. If WON_LOSS: is the outcome WON or LOSS?
4. What is your confidence level (high / medium / low)?
5. Brief reasoning (1-2 sentences)

IMPORTANT RULES:
- Status can only move FORWARD (NO_ACTION → ENGAGED → QUOTED → FOLLOW_UP → WON_LOSS)
- If the thread is empty or only has the original HubSpot email, keep current status
- If you're unsure, keep the current status and set confidence to "low"
- Look for GWC email domains (@gwclogistics.com) to identify rep emails
- The customer is anyone NOT from @gwclogistics.com

Respond ONLY with valid JSON in this exact format (no markdown, no explanation outside the JSON):
{{
  "recommended_status": "NO_ACTION|ENGAGED|QUOTED|FOLLOW_UP|WON_LOSS",
  "status_changed": true|false,
  "deal_outcome": "WON|LOSS|",
  "confidence": "high|medium|low",
  "reasoning": "Brief explanation of what you observed in the thread",
  "key_evidence": "The specific email or phrase that drove your decision",
  "dark_lead": true|false,
  "dark_lead_reason": "Why this is a dark lead, or empty string"
}}"""

    return prompt





def build_orphan_analysis_prompt(thread_payload: dict) -> str:
    """
    Like build_analysis_prompt() but for orphan/pre-pipeline leads where
    we have NO known starting status. Instead of asking for a transition,
    we ask Claude to determine the CURRENT status from scratch by reading
    the full thread.

    Used exclusively for leads backfilled via the pre-April-8 orphan path.
    """
    gwc_id  = thread_payload["gwc_id"]
    thread  = thread_payload["thread"]

    thread_text = ""
    for msg in thread:
        if msg.get("is_original_hubspot"):
            continue
        thread_text += f"""
--- Email {msg['index']} ---
Date:    {msg['date']}
From:    {msg['sender']} [{msg['role'].upper()}]
Subject: {msg['subject']}
Body:
{msg['body'][:1500]}
"""

    if not thread_text.strip():
        thread_text = "(No readable email content found)"

    return f"""You are classifying a GWC Logistics freight lead that predates the pipeline.
There is NO prior status in the system — determine the current status from scratch.

## Lead ID: {gwc_id}
## Context
This lead's original HubSpot email arrived before the tracking system went live (pre-April 8 2026).
It was never ingested. We have found email conversations referencing this GWC ID and need to
determine where in the pipeline this lead currently stands.

## Status Definitions (ordered: earliest → latest)
- NO_ACTION:  GWC received the inquiry but no rep has responded
- ENGAGED:    A GWC rep has sent at least one reply to the customer
- QUOTED:     A GWC rep has sent pricing, rates, a quotation, or a proposal
- FOLLOW_UP:  The customer has replied after receiving a quotation
- WON_LOSS:   Deal is confirmed won OR customer has explicitly declined/cancelled

## Classification Rules
- Assign the HIGHEST status supported by evidence in the thread
- If rep has quoted AND customer replied → FOLLOW_UP (not just QUOTED)
- If you see clear deal confirmation or cancellation → WON_LOSS
- If thread only shows rep replied but no quote → ENGAGED
- If no rep response found at all → NO_ACTION

## Full Email Thread (chronological)
{thread_text}

## Task
1. What is the current status of this lead based on the thread?
2. If WON_LOSS: is the outcome WON or LOSS?
3. What is your confidence (high / medium / low)?
4. Brief reasoning (2-3 sentences max)
5. Key evidence: the specific email/phrase that drove your decision

Respond ONLY with valid JSON — no markdown, no text outside the JSON:
{{
  "recommended_status": "NO_ACTION|ENGAGED|QUOTED|FOLLOW_UP|WON_LOSS",
  "status_changed": true,
  "deal_outcome": "WON|LOSS|",
  "confidence": "high|medium|low",
  "reasoning": "What you observed in the thread",
  "key_evidence": "Specific phrase or email that drove the decision",
  "dark_lead": false,
  "dark_lead_reason": ""
}}"""




# ── Response parser ───────────────────────────────────────────────────────────

def parse_analysis_result(claude_response: str, current_status: str) -> dict:
    """
    Parse Claude's JSON response from the thread analysis prompt.
    Applies safety guards:
    - Status can only move forward
    - Unknown statuses default to current_status
    - Malformed JSON returns a safe no-change result

    Args:
        claude_response: Raw text response from Claude
        current_status:  The lead's current status (safety check)

    Returns:
        dict with keys: recommended_status, status_changed, deal_outcome,
                        confidence, reasoning, key_evidence, dark_lead, dark_lead_reason
    """
    # Default safe response (no change)
    safe_default = {
        "recommended_status": current_status,
        "status_changed": False,
        "deal_outcome": "",
        "confidence": "low",
        "reasoning": "Could not parse analysis result — no change applied.",
        "key_evidence": "",
        "dark_lead": False,
        "dark_lead_reason": "",
    }

    try:
        # Extract JSON from the response (handle markdown code blocks)
        text = claude_response.strip()
        json_match = re.search(r"\{[\s\S]*\}", text)
        if not json_match:
            return safe_default

        data = json.loads(json_match.group())

        recommended = data.get("recommended_status", current_status)

        # Safety: status can only move forward
        if status_rank(recommended) < status_rank(current_status):
            data["recommended_status"] = current_status
            data["status_changed"] = False
            data["reasoning"] = (
                f"[Safety guard] Prevented backward status move from "
                f"{current_status} → {recommended}. " + data.get("reasoning", "")
            )

        # Ensure required keys are present
        for key, default in safe_default.items():
            if key not in data:
                data[key] = default

        # Recalculate status_changed based on final recommended vs current
        data["status_changed"] = (data["recommended_status"] != current_status)

        # Clean deal_outcome
        outcome = (data.get("deal_outcome") or "").upper().strip()
        data["deal_outcome"] = outcome if outcome in ("WON", "LOSS") else ""

        return data

    except (json.JSONDecodeError, ValueError, TypeError):
        return safe_default


# ── Status update applier ─────────────────────────────────────────────────────

def build_status_update(analysis: dict, lead: dict, now_iso: str) -> dict:
    """
    Build the field update dict to apply to leads_maturity.csv
    based on the analysis result.

    Returns a dict of fields to update (passed to store.update_lead_field).
    """
    import json as _json

    updates = {"last_email_scan_at": now_iso, "updated_at": now_iso}

    # ── for PRE_PIPELINE backfills, record the backfill source ───────────
    if lead.get("classification") == "PRE_PIPELINE":
        updates["notes"] = (lead.get("notes", "") +
                            f" | AI-classified {now_iso[:10]}: {analysis.get('recommended_status','')}"
                            f" ({analysis.get('confidence','')} confidence)")

    if not analysis["status_changed"]:
        return updates

    new_status = analysis["recommended_status"]

    # Append to status_history
    try:
        history = _json.loads(lead.get("status_history", "[]") or "[]")
    except Exception:
        history = []

    history.append({
        "status":     new_status,
        "timestamp":  now_iso,
        "changed_by": "SYSTEM",
        "reason":     analysis.get("reasoning", "")[:200],
        "confidence": analysis.get("confidence", ""),
        "evidence":   analysis.get("key_evidence", "")[:200],
    })

    updates["current_status"]   = new_status
    updates["status_history"]   = _json.dumps(history)

    # Set timestamp fields based on which status we're transitioning to
    if new_status == "ENGAGED" and not lead.get("first_response_at"):
        updates["first_response_at"] = now_iso
    elif new_status == "QUOTED" and not lead.get("quote_sent_at"):
        updates["quote_sent_at"] = now_iso
    elif new_status == "FOLLOW_UP" and not lead.get("follow_up_started_at"):
        updates["follow_up_started_at"] = now_iso
    elif new_status == "WON_LOSS":
        updates["deal_confirmed_at"] = now_iso
        if analysis.get("deal_outcome"):
            updates["deal_outcome"] = analysis["deal_outcome"]
        # Calculate lead age in days
        from datetime import datetime, timezone
        try:
            received = datetime.fromisoformat(
                lead.get("email_received_at", "").replace("Z", "+00:00")
            )
            age_days = (datetime.now(timezone.utc) - received).days
            updates["lead_age_days"] = str(age_days)
        except Exception:
            pass

    return updates
