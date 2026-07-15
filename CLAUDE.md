# CLAUDE.md

This is read by Claude Code at the start of every session on this project.

## Project purpose

leadgen is a config-driven, multi-vertical lead generation scraper using
the Google Places API (New). It searches local businesses by category and
sub-area, qualifies them as leads based on operational status and phone
availability, optionally enriches leads with no website via directory
scrape (JustDial/IndiaMART), and stores everything in a local SQLite
database that can be browsed and triaged through a Streamlit dashboard.

## Read these first

Reference [ARCHITECTURE.md](ARCHITECTURE.md) (system design),
[DECISIONS.md](DECISIONS.md) (why things are the way they are), and
[ROADMAP.md](ROADMAP.md) (what's deliberately not built yet) before
making structural changes.

## Non-negotiable constraints (check before writing any code)

- Never hardcode a category, location, or sub_area string in `.py` files —
  always read from `config/*.yaml`. Run
  `python scripts/verify_no_hardcoding.py` after any change that touches
  config-related code.
- Never remove or bypass the API budget check (`api_call_log` /
  `api_budget` config) when modifying `main.py`'s `run` command.
- Never remove the `scraped_subareas` skip-logic without explicit
  instruction.
- Field masks sent to the Places API must stay Essentials/Pro tier unless
  explicitly told otherwise — flag any change that would add
  rating/reviews/atmosphere fields, since this changes the billing tier.
- Enrichment scraping (JustDial/IndiaMART) has no ToS protection — keep
  retries capped at 1–2, keep random delays in place, never remove the
  try/except-and-log pattern.
- The qualifying filter is phone + operational status, NOT website
  presence — do not reintroduce "has no website" as a filter; see
  [DECISIONS.md](DECISIONS.md) for why.

## Before ending any session that changed schema or config

- Confirm SQLite migrations are additive (`ALTER TABLE`), never
  destructive.
- Update [COMMANDS.md](COMMANDS.md) if any new command/flag was added.
- Update [DECISIONS.md](DECISIONS.md) if any design choice was made or
  reversed.
- Update [ROADMAP.md](ROADMAP.md) if a previously-deferred item was
  built.
- Run relevant smoke tests before declaring a change verified.

## Known fragile points (expect these to break, not code bugs)

- JustDial/IndiaMART HTML selectors — will break when those sites change
  layout.
- Sub-area name resolution in Places Text Search — some locality names
  may return zero results if Google's geocoding doesn't recognize them as
  written.

## Current verticals in use

- architect (`config/sources.yaml`)
- clinic (`config/sources_clinic.yaml`)
- wholesaler (`config/sources_wholesaler.yaml`)

Update this section whenever a new vertical config is added.
