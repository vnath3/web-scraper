# Architecture

This describes the leadgen system's data flow as eight layers, top to bottom.
Each layer's file(s) and responsibility are listed, followed by an ASCII
diagram showing how they connect.

## 1. Config Layer

**Files:** `config/*.yaml` (one file per vertical), `.env` (secrets)

Each vertical config (e.g. `config/sources.yaml`, `config/sources_clinic.yaml`,
`config/sources_wholesaler.yaml`) defines:

- `category` — the business type to search for
- `location` — the city/region
- `sub_areas` — a list of localities searched individually
- `radius_meters` — search radius
- `require_phone` — whether phone presence is required to qualify
- `enrichment` — enabled flag, source (justdial/indiamart), max_leads_per_run
- `api_budget` — max_calls_per_month, warn_at_percent

`.env` holds `GOOGLE_PLACES_API_KEY` and any other secrets — never
committed, never read from anywhere but the environment.

## 2. Orchestrator

**File:** `main.py` (the `run` command)

Loads the config, then loops over `sub_areas`. For each sub-area, checks
`scraped_subareas` for existing skip-state before invoking the fetch layer.
Also checks the API budget (layer 8) before every fetch call, and drives
the filter layer (layer 4) and storage layer (layer 6) for each result.

## 3. Fetch Layer

**File:** `leadgen/places_client.py`

`text_search()` runs first per sub-area, then `get_place_details()` runs
per result returned. Field masks are locked to Essentials/Pro tier fields
only. Requests retry with exponential backoff on 429/5xx responses.

## 4. Filter Layer

**Files:** `leadgen/filters.py`, `leadgen/niche.py`

A place qualifies as a LEAD if `business_status == OPERATIONAL AND phone
exists`. Every place (qualified or not) is assigned a `segment_tag`:
`no_digital_presence`, `reputation_angle`, `visibility_angle`, or
`general_outreach`.

Qualified leads also get a `niche_tag` — a per-vertical sub-category
(e.g. `dental`, `physiotherapy` within the clinic vertical), derived by
`leadgen/niche.py`'s `assign_niche_tag`: Google's `types`/`primaryType`
fields are checked first, falling back to case-insensitive keyword
matching against the lead's `name` (keyword lists come from each config's
`niche_keywords` section) if no type match is found. See
[DECISIONS.md](DECISIONS.md) for why this is two-tier rather than
keyword-only.

## 5. Enrichment Layer (conditional)

**File:** `leadgen/enrichment.py`

Runs only on leads tagged `no_digital_presence` — not the full lead set.
Scrapes JustDial or IndiaMART via `requests` + BeautifulSoup (no browser
automation). Capped attempts, rate-limited with random delays, and
defensive: failures are logged and skipped, never crash the batch.

## 6. Storage Layer

**File:** `leadgen/storage.py`

SQLite database (`data/leads.db`) with three tables: `leads`,
`api_call_log`, `scraped_subareas`. Leads are inserted with
insert-or-ignore dedup on `place_id`.

## 7. Presentation Layer

**File:** `leadgen/dashboard.py`

Streamlit dashboard, read-only against the leads table except for two
write paths: status updates, and — via the "+ New Scrape Config" tab —
writing new config YAML files and triggering runs. Per-category tabs,
sidebar filters, an editable `status` column, and CSV export of the
currently filtered view.

The "+ New Scrape Config" tab is not a read-only view: it builds a
`SourceConfig`, validates it through the same pydantic model
`config_loader.py` uses for the CLI, writes it to `config/` (never
silently overwriting an existing file), and can trigger a run against it.
That run goes through `main.py`'s `run_stream` generator — the identical
code path `python main.py run` uses (orchestrator layer 2, fetch layer 3,
filter layer 4, storage layer 6, budget layer 8 all included, unchanged)
— so the dashboard has no separate scraping implementation. Progress is
streamed into the page as `run_stream` yields lines, rather than only
showing a result after the run completes.

## 8. Budget / Observability Layer

**File:** `leadgen/storage.py` (`api_call_log` table), checked from `main.py`

Every Places API call is logged to `api_call_log`. Before each call, usage
is checked against `api_budget.max_calls_per_month` from config — a run
stops cleanly if the budget is exhausted, and warns once past
`warn_at_percent`. The run summary prints skip/scrape counts and call
totals.

## Data flow diagram

```
┌─────────────────────────────────────────────────────────────┐
│ 1. CONFIG LAYER                                              │
│    config/*.yaml (category, location, sub_areas, radius,     │
│    require_phone, enrichment, api_budget) + .env (secrets)   │
└───────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. ORCHESTRATOR                                               │
│    main.py `run` — loads config, loops sub_areas,             │
│    checks scraped_subareas skip-state                         │
└───────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ 8. BUDGET / OBSERVABILITY LAYER  ◄──── checked before every ──┐
│    api_call_log vs api_budget.max_calls_per_month              │ call
└───────────────────────────┬───────────────────────────────────┘
                              │ (if budget OK)
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. FETCH LAYER                                                │
│    places_client.py — text_search() then get_place_details()  │
│    per result, Essentials/Pro field masks, retry w/ backoff    │
└───────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. FILTER LAYER                                                │
│    filters.py — qualify as LEAD if OPERATIONAL + phone exists  │
│    assign segment_tag                                          │
└───────────┬─────────────────────────────────┬─────────────────┘
             │                                  │
             │ (main path)                      │ (branch: only for
             ▼                                  │  no_digital_presence)
┌─────────────────────────────┐   ┌──────────────────────────────────┐
│ 6. STORAGE LAYER              │   │ 5. ENRICHMENT LAYER (conditional) │
│    storage.py — SQLite         │◄──┤    enrichment.py — JustDial/      │
│    (leads, api_call_log,       │   │    IndiaMART scrape, capped,      │
│    scraped_subareas),          │   │    rate-limited, defensive        │
│    insert-or-ignore on         │   └────────────────────────────────────┘
│    place_id                    │
└───────────────┬─────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ 7. PRESENTATION LAYER                                          │
│    dashboard.py — Streamlit, per-category tabs, filters,       │
│    editable status column, CSV export of filtered view          │
└─────────────────────────────────────────────────────────────┘
```

The enrichment layer (5) is a branch off the filter layer, not part of the
main path — it runs later, via `python main.py enrich`, and only touches
leads already stored with `segment_tag = no_digital_presence`.
