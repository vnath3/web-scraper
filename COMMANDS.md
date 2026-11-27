# Commands

This file must be updated whenever a new CLI command, flag, or config file
is added to the project — treat it as living documentation, not a
one-time snapshot.

## Setup (one-time)

```bash
python -m venv .venv
source .venv/bin/activate   # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

```bash
cp .env.example .env
```

Edit `.env` and fill in `GOOGLE_PLACES_API_KEY=your-key-here`.

## Running the scraper

```bash
python main.py run --config config/sources.yaml
python main.py run --config config/sources_clinic.yaml
python main.py run --config config/sources_wholesaler.yaml
```

```bash
python main.py run --config config/sources.yaml --force-rescrape
```

`--force-rescrape` re-scrapes all sub-areas even if already marked done in
`scraped_subareas`. This costs API calls against the monthly budget — use
it deliberately (e.g. a monthly refresh), not as a default habit.

## Enrichment

```bash
python main.py enrich --config config/sources.yaml
```

Only runs if `enrichment.enabled: true` is set in the given config, and
only against leads tagged `no_digital_presence`.

## Export

```bash
python main.py export --output leads.csv
```

## Outreach (personalised demo links on WhatsApp)

```bash
python main.py outreach --config config/sources_clinic.yaml
python main.py outreach --config config/sources_clinic.yaml --lang mr --segment no_digital_presence --limit 20
python main.py outreach --config config/sources_salon.yaml --mark-contacted
```

Builds `outreach/<demo>-<date>-<lang>.html` + `.csv` from leads of that
config's `category` with `status = new`. Open the HTML on your phone: each
lead has **Send on WhatsApp** (prefilled message with their personalised
flowgridlabs.com demo link) and **Preview demo**. Nothing is sent
automatically — you review and send each one.

- `--config` (required) must have `outreach.demo` set (which
  flowgridlabs.com demo folder to show — e.g. `demo: "clinic"`).
- `--lang` picks the message template from `config/outreach.yaml`
  (`en`, `mr`).
- `--segment` limits to one `segment_tag` (e.g. `no_digital_presence`).
  Without it, all `new` leads are eligible; `no_digital_presence` leads
  are listed first either way.
- `--limit` defaults to `default_limit` in `config/outreach.yaml` (20).
- `--mark-contacted` sets the listed leads to `status = contacted` with a
  `follow_up_date` `follow_up_days` ahead, so tomorrow's run picks new
  people. Run it after you've actually sent the messages.
- Leads without a WhatsApp-able mobile (landlines, missing numbers) are
  skipped and counted; `enrichment_phone` is used when `phone` isn't a
  mobile. The same number on two listings is messaged once.
- Message text, site URL, sender and follow-up gap live in
  `config/outreach.yaml`.

Tests: `python -m unittest discover -s tests -t .`

## Dashboard

```bash
streamlit run leadgen/dashboard.py
```

Each category tab's grid has three editable columns: `status`, `notes`
(free-form, overwritten on each save — not a log), and `follow_up_date`
(a single next-contact date). Edit cells, then click **Save changes** to
write only the rows that actually changed. The sidebar's **"Due for
follow-up"** toggle filters to leads with `follow_up_date` today or
earlier that aren't `converted`/`rejected` — the "what do I call today"
view — and each tab shows a small count of leads due today near the top.
This is intentionally minimal outcome tracking, not a CRM — see
DECISIONS.md.

Besides browsing/triaging existing leads, the dashboard's **"+ New Scrape
Config" tab** can create new vertical configs and run them without
touching a terminal:

- Fill in category, location, sub-areas (one per line or comma-separated),
  radius, require_phone, and optionally niche keywords, then **Save
  Config** — writes `config/sources_{category_slug}.yaml` after validating
  it through the same `SourceConfig` pydantic model the CLI uses. Won't
  silently overwrite an existing file; requires an explicit "Overwrite
  existing config" checkbox.
- **Run Scraper** (appears once a config is saved or selected from the
  dropdown of everything in `config/`) shows current API budget usage,
  warns before a run that would push close to the monthly limit, and
  streams live progress (sub-area skip messages, found/qualified/dropped
  counts) as the run executes.
- This goes through the exact same `run_stream` generator in `main.py`
  that the CLI's `run` command wraps — no separate/duplicated scrape
  logic, no subprocess.

## Maintenance / verification

```bash
python scripts/verify_no_hardcoding.py
python scripts/smoke_test_enrichment.py
python scripts/backfill_niche_tags.py --config config/sources_clinic.yaml
```

`verify_no_hardcoding.py` greps the codebase for hardcoded
category/location/sub_area strings outside `config/*.yaml` — run it after
any change that touches config-related code.

`smoke_test_enrichment.py` exercises the JustDial/IndiaMART parsers
against saved sample HTML, without hitting the live sites.

`backfill_niche_tags.py` is a one-off: it assigns `niche_tag` to existing
leads (scraped before `niche_tag` existed) using name-keyword matching
only, no API calls spent. Run once per vertical config after adding or
changing that vertical's `niche_keywords`. New leads from `main.py run`
get `niche_tag` automatically going forward and don't need this.
