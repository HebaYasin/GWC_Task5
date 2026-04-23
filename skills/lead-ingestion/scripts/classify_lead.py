"""
classify_lead.py
----------------
Deterministic (no AI) lead classification based on field completeness.
No LLM calls here — this is pure Python logic.

Classification outcomes:
  QUALIFIED           → All mandatory fields present & GWC ID present
  PARTIALLY_QUALIFIED → GWC ID present + some required fields present
  REJECTED            → GWC ID missing 

Actions downstream:
  QUALIFIED           → status = NO_ACTION, route to sales rep immediately
  PARTIALLY_QUALIFIED → status = NO_ACTION, notify rep to collect missing data
  REJECTED            → status = REJECTED, log, no routing

Usage:
    from classify_lead import classify_lead
    result = classify_lead(parsed_fields)
    # result: {"classification": "QUALIFIED"|"PARTIALLY_QUALIFIED"|"REJECTED",
    #          "missing_fields": [...],
    #          "current_status": "NO_ACTION"|"REJECTED"}
"""

import json
from typing import Optional


# ── Field definitions by Mode of Transport ────────────────────────────────────
#
# Based on the mandatory fields table in the build spec (Section 4).
# Fields marked as mandatory for each MOT.
#
# Note: The HubSpot email template only carries a subset of these fields.
# Fields NOT present in the template are tracked as "not_in_template" and
# treated as missing — they will always trigger PARTIALLY_QUALIFIED unless
# enriched by other means (e.g., rep updates, form submissions).
#
# Fields that exist in the HubSpot email template:
#   from_country, to_country, mode_of_freight, container_mode,
#   product (commodity), weight_kg, shipping_requirements, notes
#
# Fields NOT in the template (will always be missing from initial ingest):
#   incoterms, volume_m3, packages, chargeable_weight, dimension_lwh,
#   container_type (for FCL), perishable, stackable, dg_class, msds,
#   pickup_location (not a field in template)

# Fields present in the HubSpot email template that we can actually parse:
TEMPLATE_FIELDS = {
    "gwc_id", "contact_name", "company_name", "phone","whatsapp",
    "from_country", "to_country", "mode_of_freight", "container_mode",
    "product", "weight_kg", "shipping_requirements", "notes",
    "hubspot_create_date",
}

# Core required fields that MUST be present for any lead to be QUALIFIED.
# These are the fields we can actually check from the HubSpot email template.
CORE_REQUIRED = [
    "gwc_id",
    "from_country",
    "to_country",
    "mode_of_freight",
    "product",      # = commodity
    "weight_kg",
]

# Extended mandatory fields per MOT — these are NOT in the HubSpot template
# but ARE required for a freight quote. Their absence will always make a lead
# PARTIALLY_QUALIFIED at ingestion time (they must be collected by the sales rep).
EXTENDED_REQUIRED_ALL_MOT = [
    "incoterms",
    "packages",
    "dimension_lwh",
]

EXTENDED_REQUIRED_BY_MOT = {
    "Air": [
        "volume_m3",
        "chargeable_weight",
        "stackable",
    ],
    "Sea": {
        "LCL": ["volume_m3", "chargeable_weight", "stackable"],
        "FCL": ["container_type"],          # volume optional for FCL
        "default": [],
    },
    "Overland": [
        "volume_m3",
    ],
}

# Conditional required fields
CONDITIONAL_REQUIRED = {
    "perishable": "temperature_details",  # if perishable == Y → temperature_details required
    "dg_class":   "msds",                 # if dg_class == Y    → msds required
}


# ── Helper ────────────────────────────────────────────────────────────────────

def _is_empty(value) -> bool:
    """Return True if a field value is considered absent/empty.
    Case-insensitive — treats 'N/A', 'n/a', 'NA', etc. all as empty.
    Only leads with a missing GWC ID should be REJECTED; all other
    incomplete leads are PARTIALLY_QUALIFIED.
    """
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in ("", "n/a", "na", "-", "none", "null"):
        return True
    return False


def _get_mot_extended_required(mot: str, container_mode: str) -> list:
    """Return the extra required fields for the given MOT + container mode."""
    mot_upper = (mot or "").strip().capitalize()
    cmode = (container_mode or "").strip().upper()

    extras = list(EXTENDED_REQUIRED_ALL_MOT)  # copy

    if mot_upper == "Air":
        extras += EXTENDED_REQUIRED_BY_MOT.get("Air", [])
    elif mot_upper == "Sea":
        sea_rules = EXTENDED_REQUIRED_BY_MOT.get("Sea", {})
        if cmode == "LCL":
            extras += sea_rules.get("LCL", [])
        elif cmode == "FCL":
            extras += sea_rules.get("FCL", [])
        else:
            extras += sea_rules.get("default", [])
    elif mot_upper == "Overland":
        extras += EXTENDED_REQUIRED_BY_MOT.get("Overland", [])

    return extras


# ── Main classifier ───────────────────────────────────────────────────────────

def classify_lead(fields: dict) -> dict:
    """
    Classify a parsed lead dict.

    Args:
        fields: Output from parse_lead_email(), plus any enrichment fields.

    Returns:
        dict with keys:
          - classification:  "QUALIFIED" | "PARTIALLY_QUALIFIED" | "REJECTED"
          - missing_fields:  list of missing field names (empty for QUALIFIED)
          - current_status:  "NO_ACTION" | "REJECTED"
          - rejection_reason: human-readable string (only for REJECTED)
    """
    missing = []

    # ── Step 1: GWC ID check — immediate REJECTED if absent ───────────────────
    if _is_empty(fields.get("gwc_id")):
        return {
            "classification": "REJECTED",
            "missing_fields": ["gwc_id"],
            "current_status": "REJECTED",
            "rejection_reason": "GWC ID missing — cannot track this lead.",
        }

    # ── Step 2: Check if ALL required template fields are empty ───────────────
    # If contact info + shipment fields are ALL empty, this is a blank/spam lead.
    # template_data_fields = [f for f in CORE_REQUIRED if f != "gwc_id"]
    # all_template_empty = all(_is_empty(fields.get(f)) for f in template_data_fields)

    # if all_template_empty:
    #     return {
    #         "classification": "REJECTED",
    #         "missing_fields": template_data_fields,
    #         "current_status": "REJECTED",
    #         "rejection_reason": "All required shipment fields are empty — likely a blank/test submission.",
    #     }

    # ── Step 3: Check CORE required fields ────────────────────────────────────
    for field in CORE_REQUIRED:
        if _is_empty(fields.get(field)):
            missing.append(field)

    # ── Step 4: Check MOT-specific extended required fields ───────────────────
    mot = fields.get("mode_of_freight", "")
    container_mode = fields.get("container_mode", "")
    extended_required = _get_mot_extended_required(mot, container_mode)

    for field in extended_required:
        if _is_empty(fields.get(field)):
            missing.append(field)

    # ── Step 5: Check conditional required fields ─────────────────────────────
    for trigger_field, dependent_field in CONDITIONAL_REQUIRED.items():
        trigger_val = (fields.get(trigger_field) or "").strip().upper()
        if trigger_val == "Y":
            if _is_empty(fields.get(dependent_field)):
                missing.append(dependent_field)

    # ── Step 6: Deduplicate missing list (preserve order) ─────────────────────
    seen = set()
    unique_missing = []
    for f in missing:
        if f not in seen:
            seen.add(f)
            unique_missing.append(f)

    # ── Step 7: Determine classification ─────────────────────────────────────
    if not unique_missing:
        classification = "QUALIFIED"
    else:
        classification = "PARTIALLY_QUALIFIED"

    return {
        "classification": classification,
        "missing_fields": unique_missing,
        "current_status": "NO_ACTION",
        "rejection_reason": "",
    }


# ── Convenience: apply classification results back onto fields dict ───────────

def apply_classification(fields: dict, classification_result: dict) -> dict:
    """
    Merge classification results into a fields dict (modifies in place, returns it).
    Sets: classification, missing_fields (as JSON string), current_status,
    and appends the initial status_history entry.
    """
    import json
    from datetime import datetime

    now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    fields["classification"] = classification_result["classification"]
    fields["missing_fields"] = json.dumps(classification_result["missing_fields"])
    fields["current_status"] = classification_result["current_status"]
    fields["status_history"] = json.dumps([{
        "status": classification_result["current_status"],
        "timestamp": now_iso,
        "changed_by": "SYSTEM",
        "reason": classification_result.get("rejection_reason", "Initial ingestion"),
    }])
    return fields


# ── Quick test harness ────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Test 1: PARTIALLY_QUALIFIED (HubSpot template fields only — extended fields missing)
    test_partial = {
        "gwc_id": "GWC-741228136654",
        "from_country": "China",
        "to_country": "Qatar",
        "mode_of_freight": "Sea",
        "container_mode": "LCL",
        "product": "Electronics",
        "weight_kg": 1500,
        # Missing: incoterms, packages, dimension_lwh, volume_m3, chargeable_weight, stackable
    }
    r1 = classify_lead(test_partial)
    print("Test 1 — Partial (Sea LCL, missing extended fields):")
    print(json.dumps(r1, indent=2))

    # Test 2: REJECTED — no GWC ID
    test_rejected = {"from_country": "UAE", "to_country": "Qatar"}
    r2 = classify_lead(test_rejected)
    print("\nTest 2 — Rejected (no GWC ID):")
    print(json.dumps(r2, indent=2))

    # Test 3: QUALIFIED — all fields present
    test_qualified = {
        "gwc_id": "GWC-999",
        "from_country": "India",
        "to_country": "UAE",
        "mode_of_freight": "Air",
        "container_mode": "Loose Cargo",
        "product": "Pharmaceuticals",
        "weight_kg": 200,
        "incoterms": "CIF",
        "packages": 10,
        "dimension_lwh": "1.2x0.8x0.6",
        "volume_m3": 0.576,
        "chargeable_weight": 200,
        "stackable": "N",
        "perishable": "N",
        "dg_class": "N",
    }
    r3 = classify_lead(test_qualified)
    print("\nTest 3 — Qualified (Air, all fields):")
    print(json.dumps(r3, indent=2))

  