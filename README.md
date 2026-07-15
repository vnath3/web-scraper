# leadgen

A small CLI tool that finds local businesses via the Google Places API (New),
filters them into qualified leads, and exports them to CSV.

## What it does

1. Reads a category + location + list of sub-areas from `config/sources.yaml`.
2. For each sub-area, runs a Text Search query (`"{category} in {sub_area}, {location}"`)
   against the Places API and collects place IDs (deduped across sub-areas).
3. Fetches Place Details for each place (phone, website, rating, business status).
4. Filters: a place qualifies as a lead if it's `OPERATIONAL` and has a phone
   number (or `require_phone: false` in config). Everything else is dropped
   with a reason (`closed` or `no_phone`).
5. Tags each qualified lead with a `segment_tag` (`no_digital_presence`,
   `reputation_angle`, `visibility_angle`, `general_outreach`) to help
   prioritize outreach.
6. Stores leads in a local SQLite database (`data/leads.db`), skipping
   duplicates on re-run.
7. Optionally enriches `no_digital_presence` leads by looking them up on a
   public business directory (JustDial or IndiaMART) to recover a phone
   number or website the Places listing didn't have.
8. Tracks Places API usage in a monthly budget so a run stops before it
   blows through your free-tier quota.
9. Exports the leads table to CSV on demand, or browse/triage leads in a
   local Streamlit dashboard.

## Setup

### 1. Get a Google Places API key

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project (or pick an existing one).
3. Enable **Places API (New)** for that project.
4. **Enable billing** on the project — the Places API requires a linked
   billing account even within the free-tier quota. Text Search and Place
   Details (Essentials-tier fields, as used here) both incur cost per call
   beyond any monthly free credit, so keep an eye on usage.
5. Create an API key under **APIs & Services > Credentials**. Optionally
   restrict it to the Places API for safety.

### 2. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate   # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure your API key

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```
GOOGLE_PLACES_API_KEY=your-key-here
```

### 4. Edit `config/sources.yaml`

The repo ships with a ready-to-run example:

```yaml
category: "architect"
location: "Chhatrapati Sambhaji Nagar"
sub_areas:
  - "Cidco"
  - "Garkheda"
  - "Jyoti Nagar"
radius_meters: 5000
require_phone: true
```

Edit `category`, `location`, and `sub_areas` for your own search. Sub-areas
are searched individually because the Places API caps results per query —
splitting a city into localities surfaces more businesses.

The pipeline is generic across verticals — nothing in the code assumes a
specific category or city. Two more example configs ship alongside the
default:

- `config/sources_wholesaler.yaml` — wholesalers in Cidco, Waluj, MIDC
  (enrichment source: IndiaMART, the better fit for that vertical)
- `config/sources_clinic.yaml` — clinics in Garkheda, Osmanpura
  (enrichment source: JustDial)

Run any of them the same way, e.g. `python main.py run --config config/sources_clinic.yaml`.
Run `python scripts/verify_no_hardcoding.py` any time you add a new vertical
config — it greps the codebase for hardcoded category/location strings
outside `config/` and fails if it finds any, so the pipeline stays generic.

### 5. Run it

```bash
python main.py run --config config/sources.yaml
```

This prints a summary (total found, qualified, dropped by reason, API calls
made) and writes qualified leads to `data/leads.db`. Re-running is safe —
existing `place_id`s are skipped.

Each `(category, sub_area)` pair is tracked in a `scraped_subareas` table.
Once a sub-area has been scraped, later runs skip it entirely (no
`text_search`/Place Details calls, printing `Skipping {sub_area} — already
scraped on {timestamp}`) instead of burning API budget re-fetching the same
area. To force a refresh — e.g. to check whether closed businesses reopened
or new ones appeared — pass `--force-rescrape`:

```bash
python main.py run --config config/sources.yaml --force-rescrape
```

The run summary always reports `X sub-areas skipped (already scraped), Y
sub-areas scraped this run`.

### 6. Enrich leads with no digital presence (optional)

Leads tagged `no_digital_presence` (no `websiteUri` from Places) can be
looked up on a public business directory to see if they have a phone number
or website listed there that Places missed. This is controlled by the
`enrichment` block in `sources.yaml`:

```yaml
enrichment:
  enabled: true
  source: "justdial"   # or "indiamart"
  max_leads_per_run: 50
```

Run it after `run`:

```bash
python main.py enrich --config config/sources.yaml
```

This pulls up to `max_leads_per_run` leads with `segment_tag =
no_digital_presence` and `enrichment_checked_at IS NULL`, looks each one up,
and writes the result back to the row (`enrichment_source`,
`enrichment_phone`, `enrichment_website_found`, `enrichment_checked_at`).
Already-checked leads are never re-queried. It prints a summary of attempted
/ succeeded / failed / skipped.

**This is a fragile, unofficial scrape** — JustDial and IndiaMART have no
public API and no ToS-sanctioned scraping path. The client:

- Uses `requests` + BeautifulSoup (no browser automation), with a realistic
  `User-Agent` header.
- Waits 3–7 random seconds between requests to stay slow and polite.
- Retries a failed request at most once (unlike the Places client's 3x
  backoff) — it's not worth hammering a fragile, unofficial source.
- Never crashes the batch: a failed or unparseable page is logged as a
  warning (with the raw HTTP status code where available) and the run moves
  on to the next lead.

**Expect to update the CSS selectors in `leadgen/enrichment.py`
(`parse_justdial_html` / `parse_indiamart_html`) whenever JustDial or
IndiaMART change their page markup.** That's expected maintenance for an
unofficial directory scrape, not a bug.

### 7. API call budgeting

Every `text_search` and `get_place_details` call is logged to an
`api_call_log` table, keyed by day and endpoint. The `api_budget` block in
`sources.yaml` sets a monthly ceiling:

```yaml
api_budget:
  max_calls_per_month: 8000   # buffer under the 10K free threshold
  warn_at_percent: 80
```

During `python main.py run`, the budget is checked before every API call:

- Once the month's total calls reach `max_calls_per_month`, the run stops
  immediately (no crash — it prints how many days remain until the budget
  resets and finishes with the summary of whatever it collected so far).
- Once usage crosses `warn_at_percent`, a warning banner prints once and the
  run continues.

The run summary always prints the running total for the current calendar
month (`Total API calls this month`), in addition to the calls made during
that specific run.

### 8. Export to CSV

```bash
python main.py export --output leads.csv
```

### 9. Browse and triage leads in the dashboard

```bash
streamlit run leadgen/dashboard.py
```

Opens a local, read-only-by-default dashboard (`data/leads.db` is opened in
SQLite's `mode=ro`, so no accidental writes) at `http://localhost:8501`:

- One tab per distinct `category` in the leads table — add a new vertical
  and its tab appears automatically, no code change needed.
- Each tab shows a summary (total leads, counts by `segment_tag` and
  `status`) plus a filterable, sortable grid (sub-area, segment tag, status,
  has-website filters live in the sidebar and apply to whichever tab is
  active).
- The `status` column is editable as a dropdown (`new`, `contacted`,
  `no_answer`, `converted`, `rejected`, right in the grid). Click **Save
  status changes** to write only the changed statuses back to SQLite — no
  other column is ever written from the dashboard.
- **Export this view to CSV** downloads exactly the filtered rows currently
  shown in that tab, not the whole table.
- The header shows current-month API usage (`calls used / budget`), read
  from the same `api_call_log` table the CLI writes to.

This is a local-only tool with no authentication — don't expose it beyond
your own machine.

## Notes

- Only Essentials/Pro-tier fields are requested from the Places API (no
  reviews, no atmosphere data), which keeps calls out of Enterprise pricing.
- Places API requests retry up to 3 times with exponential backoff on
  429/5xx responses.
- This pass is scoped to manual runs producing a CSV, plus optional
  directory enrichment and a local dashboard. Playwright, proxy rotation,
  Slack/Sheets export, scheduling, and dashboard authentication are not
  implemented here.
