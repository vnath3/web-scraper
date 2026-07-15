# Design Decisions

Add a new entry here any time a design choice is made or reversed — this
file exists so future sessions don't accidentally undo a deliberate
decision.

## Google Places API (New) over scraping Google Maps/Search directly

We chose the official Places API (New) over scraping Google Maps or Search
results pages because ToS compliance, reliability, and cost are all better
at this project's volume. Direct scraping of Google's own properties is
fragile (layout changes, JS rendering, CAPTCHAs) and carries real ban risk
for the scraping IP/account. The API's cost is near-zero at the volumes
this project runs, so there was no cost incentive to take on that risk.

## Qualifying filter is (business_status == OPERATIONAL AND phone exists), NOT "has no website"

Website presence was originally considered as a qualifying filter and was
rejected. It's a weak proxy for lead quality — a business with a bad
website can be an easier close than one with none at all, and excluding
businesses that already have a website would have thrown away good leads
for the wrong reason. Website presence is instead used as a data field
that drives `segment_tag` (pitch-angle selection: `no_digital_presence`,
`reputation_angle`, `visibility_angle`, `general_outreach`), not as a
gate on whether a lead is stored at all.

## Enrichment (JustDial/IndiaMART) runs ONLY on no_digital_presence leads

The directory scrape has no ToS protection and no API stability guarantee,
so its use is deliberately narrow. It only runs against leads already
tagged `no_digital_presence` — not the full lead set — because the
cost/fragility of an unofficial scrape is only worth paying where it adds
real signal: confirming or strengthening the "no digital presence"
segment by checking whether a directory listing exists that Places
missed. Running it against every lead would multiply the fragile-scrape
risk for no corresponding gain.

## Field masks are locked to Essentials/Pro tier fields

Places API field masks (`places.id,places.displayName,...` for Text
Search; `id,displayName,nationalPhoneNumber,...` for Place Details) never
request rating/reviews/atmosphere fields by default. Adding those fields
silently upgrades the billing tier for that call — a mistake here is a
billing mistake, not just a data mistake, so this boundary is treated as
a hard constraint rather than a preference.

## scraped_subareas tracking prevents re-spending budget on already-scraped areas

Without this, every `run` would re-query every sub-area from scratch,
burning API budget on data that hasn't changed. `scraped_subareas` records
`(category, sub_area, last_scraped_at)` so a normal run skips sub-areas
it's already covered. `--force-rescrape` exists as a deliberate override
for periodic refresh (e.g. checking whether closed businesses reopened or
new ones appeared) — it is explicitly opt-in, not the default behavior,
because the default should protect the budget.

## Category/location/sub_area are 100% config-driven, never hardcoded in .py files

This is what makes adding a new vertical (clinic, wholesaler, etc.) a
config change instead of a code change. `scripts/verify_no_hardcoding.py`
exists specifically to catch regressions on this constraint — any future
code that special-cases a specific category or location string breaks the
premise that this is a generic, multi-vertical tool.

## API budget enforcement is an in-app safety net in addition to GCP-side quotas

`api_call_log` + `api_budget.max_calls_per_month` exist even though GCP
lets you set budget alerts, because GCP budget alerts are notify-only —
they tell you after the fact, they don't stop a run mid-flight. The
in-app check stops a `run` before it exceeds the configured monthly
ceiling, which GCP's alerting does not do by default.
