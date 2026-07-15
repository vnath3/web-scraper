"""CLI entrypoint for the lead-generation scraper."""

from __future__ import annotations

import argparse
import calendar
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any, Dict, List

from dotenv import load_dotenv

from leadgen.config_loader import ApiBudgetConfig, SourceConfig, load_config
from leadgen.enrichment import ENRICHMENT_SOURCES
from leadgen.exporter import export_to_csv
from leadgen.filters import apply_filter
from leadgen.niche import assign_niche_tag
from leadgen.places_client import ApiCallCounter, get_place_details, text_search
from leadgen.storage import (
    count_leads_needing_enrichment,
    get_calls_this_month,
    get_leads_needing_enrichment,
    get_subarea_last_scraped,
    insert_lead,
    mark_subarea_scraped,
    update_enrichment,
)


def _days_until_month_reset(today: date) -> int:
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    return days_in_month - today.day + 1


def _check_budget(budget: ApiBudgetConfig, warned: List[bool]) -> bool:
    """Returns False if the run must stop because the monthly budget is exhausted."""
    calls_so_far = get_calls_this_month()

    if calls_so_far >= budget.max_calls_per_month:
        days_left = _days_until_month_reset(date.today())
        print(
            f"\nAPI budget exceeded: {calls_so_far}/{budget.max_calls_per_month} "
            f"calls used this month. Budget resets in {days_left} day(s). "
            f"Stopping run — no more API calls will be made."
        )
        return False

    warn_threshold = budget.max_calls_per_month * budget.warn_at_percent / 100
    if calls_so_far >= warn_threshold and not warned[0]:
        print(
            f"\n*** WARNING: {calls_so_far}/{budget.max_calls_per_month} calls used "
            f"this month ({budget.warn_at_percent}%+ threshold). Continuing. ***\n"
        )
        warned[0] = True

    return True


def run(config_path: str, force_rescrape: bool = False) -> None:
    load_dotenv()
    config: SourceConfig = load_config(config_path)
    counter = ApiCallCounter()
    warned = [False]
    budget_exceeded = False
    subareas_skipped = 0
    subareas_scraped = 0

    # place_id -> (basic search result, sub_area it was found in)
    found: Dict[str, Dict[str, Any]] = {}

    for sub_area in config.sub_areas:
        last_scraped_at = get_subarea_last_scraped(config.category, sub_area)
        if last_scraped_at and not force_rescrape:
            print(f"Skipping {sub_area} — already scraped on {last_scraped_at}")
            subareas_skipped += 1
            continue

        if config.api_budget is not None and not _check_budget(config.api_budget, warned):
            budget_exceeded = True
            break

        query = f"{config.category} in {sub_area}, {config.location}"
        results = text_search(query, counter=counter)
        for place in results:
            place_id = place.get("place_id")
            if not place_id:
                continue
            if place_id not in found:
                found[place_id] = {**place, "sub_area": sub_area}

        mark_subarea_scraped(
            config.category, sub_area, datetime.now(timezone.utc).isoformat()
        )
        subareas_scraped += 1

    total_found = len(found)
    qualified_count = 0
    drop_reasons: Counter = Counter()

    if not budget_exceeded:
        for place_id, place in found.items():
            if config.api_budget is not None and not _check_budget(config.api_budget, warned):
                budget_exceeded = True
                break

            details = get_place_details(place_id, counter=counter)
            filtered = apply_filter(details, config)

            if filtered["qualified"]:
                qualified_count += 1

                niche_tag = "unspecified"
                if config.niche_keywords:
                    niche_tag, _matched_via = assign_niche_tag(
                        google_types=filtered.get("types"),
                        primary_type=filtered.get("primary_type"),
                        name=filtered.get("name") or "",
                        niche_keywords=config.niche_keywords,
                    )

                lead = {
                    "place_id": filtered.get("place_id"),
                    "name": filtered.get("name"),
                    "phone": filtered.get("phone"),
                    "address": filtered.get("address"),
                    "category": config.category,
                    "website": filtered.get("website"),
                    "rating": filtered.get("rating"),
                    "rating_count": filtered.get("rating_count"),
                    "business_status": filtered.get("business_status"),
                    "segment_tag": filtered.get("segment_tag"),
                    "sub_area": place.get("sub_area"),
                    "date_found": date.today().isoformat(),
                    "google_types": filtered.get("types"),
                    "niche_tag": niche_tag,
                }
                insert_lead(lead)
            else:
                drop_reasons[filtered["drop_reason"]] += 1

    dropped_count = total_found - qualified_count

    print("=== Run Summary ===")
    if budget_exceeded:
        print("(run stopped early — monthly API budget exceeded)")
    print(
        f"{subareas_skipped} sub-areas skipped (already scraped), "
        f"{subareas_scraped} sub-areas scraped this run"
    )
    print(f"Total places found:     {total_found}")
    print(f"Total qualified leads:  {qualified_count}")
    print(f"Total dropped:          {dropped_count}")
    if drop_reasons:
        for reason, count in drop_reasons.items():
            print(f"  - {reason}: {count}")
    print(f"Total API calls made:   {counter.count}")
    print(f"Total API calls this month: {get_calls_this_month()}")


def enrich(config_path: str) -> None:
    load_dotenv()
    config: SourceConfig = load_config(config_path)

    if config.enrichment is None or not config.enrichment.enabled:
        print("Enrichment is not enabled in this config. Nothing to do.")
        return

    search_fn = ENRICHMENT_SOURCES[config.enrichment.source]
    max_leads = config.enrichment.max_leads_per_run

    total_backlog = count_leads_needing_enrichment()
    leads = get_leads_needing_enrichment(max_leads)
    attempted = 0
    succeeded = 0
    failed = 0

    for lead in leads:
        attempted += 1
        result = search_fn(lead["name"], lead.get("address") or config.location)
        checked_at = datetime.now(timezone.utc).isoformat()

        if result.get("error"):
            failed += 1
        else:
            succeeded += 1

        update_enrichment(
            place_id=lead["place_id"],
            source=config.enrichment.source,
            phone=result.get("phone"),
            website_found=result.get("website"),
            checked_at=checked_at,
        )

    skipped = max(total_backlog - attempted, 0)

    print("=== Enrichment Summary ===")
    print(f"Attempted:               {attempted}")
    print(f"Succeeded:               {succeeded}")
    print(f"Failed:                  {failed}")
    print(f"Skipped (over run cap):  {skipped}")


def export(output_path: str) -> None:
    rows_written = export_to_csv(output_path)
    print(f"Exported {rows_written} leads to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local-business lead-generation scraper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Scrape leads per sources.yaml")
    run_parser.add_argument(
        "--config", default="config/sources.yaml", help="Path to sources.yaml"
    )
    run_parser.add_argument(
        "--force-rescrape",
        action="store_true",
        help="Re-scrape all sub-areas even if already scraped before",
    )

    enrich_parser = subparsers.add_parser(
        "enrich", help="Enrich no_digital_presence leads via directory scrape"
    )
    enrich_parser.add_argument(
        "--config", default="config/sources.yaml", help="Path to sources.yaml"
    )

    export_parser = subparsers.add_parser("export", help="Export leads table to CSV")
    export_parser.add_argument(
        "--output", default="leads.csv", help="Path to output CSV file"
    )

    args = parser.parse_args()

    if args.command == "run":
        run(args.config, force_rescrape=args.force_rescrape)
    elif args.command == "enrich":
        enrich(args.config)
    elif args.command == "export":
        export(args.output)


if __name__ == "__main__":
    main()
