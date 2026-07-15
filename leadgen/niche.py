"""Niche/sub-category detection for scraped leads.

Two-tier classification, checked in priority order:

  1. Google's own `types` / `primaryType` fields — authoritative when they
     match, but Google's type taxonomy is coarse for many sub-niches
     (some verticals have no distinct type for most of their sub-niches
     at all), so this tier often won't produce a match.
  2. Case-insensitive keyword matching against the lead's `name` — the
     fallback that does most of the real classification work, tuned to
     common Indian business-naming conventions per vertical.

`assign_niche_tag` returns both the tag and which tier produced it
("types" | "keyword" | "none"), so a wrong-looking niche_tag can be
traced back to its source rather than treated as an opaque black box.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# Best-effort mapping of niche name -> known Google Place types (New) that
# indicate that niche directly. This is deliberately conservative: only
# type strings we're confident are real, current Google Places types are
# included. Many niches have no corresponding Google type at all and are
# left with an empty list here, meaning they resolve via keyword fallback
# only. Expand this map once real API responses are observed to confirm
# additional type values — treat it as unverified until then, the same
# way enrichment.py's HTML selectors are treated as unverified until
# checked against a live page.
NICHE_GOOGLE_TYPES: Dict[str, List[str]] = {
    "residential": [],
    "commercial": [],
    "interior_design": ["interior_designer"],
    "structural": [],
    "landscape": ["landscape_architect"],
    "general_physician": ["doctor"],
    "dental": ["dentist", "dental_clinic"],
    "eye_care": ["eye_care_center"],
    "physiotherapy": ["physiotherapist"],
    "dermatology": ["skin_care_clinic"],
    "orthopedic": [],
    "pediatric": [],
    "gynecology": [],
    "ent": [],
    "steel_hardware": ["hardware_store"],
    "electrical": [],
    "grocery_kirana": ["grocery_store", "supermarket"],
    "textiles": [],
    "stationery": [],
    "agricultural_inputs": [],
    "construction_materials": [],
}


def assign_niche_tag(
    google_types: Optional[List[str]],
    primary_type: Optional[str],
    name: str,
    niche_keywords: Dict[str, List[str]],
) -> Tuple[str, str]:
    """Classify a lead into one of the niches defined in niche_keywords.

    Returns (niche_tag, matched_via), where matched_via is "types",
    "keyword", or "none". Only niches present in niche_keywords (i.e.
    the vertical's own config) are considered — NICHE_GOOGLE_TYPES may
    contain entries for other verticals too, those are ignored.
    """
    # Tier 1: Google types/primaryType match.
    candidate_types = set()
    if primary_type:
        candidate_types.add(primary_type.lower())
    if google_types:
        candidate_types.update(t.lower() for t in google_types)

    if candidate_types:
        for niche in niche_keywords:
            known_types = {t.lower() for t in NICHE_GOOGLE_TYPES.get(niche, [])}
            if candidate_types & known_types:
                return niche, "types"

    # Tier 2: case-insensitive keyword match against the business name.
    name_lower = (name or "").lower()
    for niche, keywords in niche_keywords.items():
        for keyword in keywords:
            if keyword.lower() in name_lower:
                return niche, "keyword"

    return "unspecified", "none"
