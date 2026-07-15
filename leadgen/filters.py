"""Qualification and segmentation logic for scraped place details."""

from __future__ import annotations

from typing import Any, Dict

from leadgen.config_loader import SourceConfig


def _segment_tag(place_details: Dict[str, Any]) -> str:
    website = place_details.get("website")
    rating = place_details.get("rating")
    rating_count = place_details.get("rating_count")

    if not website:
        return "no_digital_presence"
    if rating is not None and rating < 4.0:
        return "reputation_angle"
    if not rating_count:
        return "visibility_angle"
    return "general_outreach"


def apply_filter(
    place_details: Dict[str, Any], config: SourceConfig
) -> Dict[str, Any]:
    """Qualify or drop a place, and attach a segment_tag.

    Returns a dict with the original place_details plus:
      - qualified: bool
      - drop_reason: Optional[str] ("closed" | "no_phone" | None)
      - segment_tag: str
    """
    result = dict(place_details)

    is_operational = place_details.get("business_status") == "OPERATIONAL"
    has_phone = place_details.get("phone") is not None

    if not is_operational:
        result["qualified"] = False
        result["drop_reason"] = "closed"
    elif not has_phone and config.require_phone:
        result["qualified"] = False
        result["drop_reason"] = "no_phone"
    else:
        result["qualified"] = True
        result["drop_reason"] = None

    result["segment_tag"] = _segment_tag(place_details)

    return result
