"""One-off backfill: assign niche_tag to existing leads without re-scraping.

Existing rows have no google_types (that field didn't exist when they were
scraped), so this can only use tier 2 of assign_niche_tag — case-insensitive
keyword matching against the lead's name. Every row processed here is
necessarily matched_via "keyword" or "none"; it will never be "types". New
leads scraped after this change will get a types-based match where
available, going forward.

Usage: python scripts/backfill_niche_tags.py --config config/sources.yaml
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leadgen.config_loader import load_config  # noqa: E402
from leadgen.niche import assign_niche_tag  # noqa: E402
from leadgen.storage import get_leads_missing_niche_tag, update_niche_tag  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill niche_tag for existing leads using name-keyword matching only"
    )
    parser.add_argument(
        "--config", required=True, help="Path to the vertical's sources.yaml"
    )
    args = parser.parse_args()

    config = load_config(args.config)

    if not config.niche_keywords:
        print(f"No niche_keywords defined in {args.config}. Nothing to backfill.")
        return

    leads = get_leads_missing_niche_tag(config.category)
    if not leads:
        print(f"No leads with category='{config.category}' are missing a niche_tag.")
        return

    print(
        f"Backfilling niche_tag for {len(leads)} '{config.category}' lead(s). "
        f"NOTE: google_types was not captured for these pre-existing rows, so "
        f"this backfill uses name-keyword matching only (tier 2) — no row here "
        f"can be matched via Google types (tier 1)."
    )

    matched_via_counts: Counter = Counter()
    sample_rows = []

    for lead in leads:
        niche_tag, matched_via = assign_niche_tag(
            google_types=None,
            primary_type=None,
            name=lead.get("name") or "",
            niche_keywords=config.niche_keywords,
        )
        update_niche_tag(lead["place_id"], niche_tag)
        matched_via_counts[matched_via] += 1
        sample_rows.append((lead.get("name"), niche_tag, matched_via))

    print("\n=== Backfill Summary ===")
    print(f"Total leads processed: {len(leads)}")
    for via in ("keyword", "none"):
        print(f"  matched_via={via}: {matched_via_counts.get(via, 0)}")

    print("\n=== Sample (first 15) ===")
    for name, niche_tag, matched_via in sample_rows[:15]:
        print(f"  [{matched_via:7}] {niche_tag:25} <- {name}")


if __name__ == "__main__":
    main()
