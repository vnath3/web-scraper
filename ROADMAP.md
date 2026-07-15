# Roadmap

This is a living list. Move an item to the "BUILT (moved from above)"
section below once implemented — don't just delete it. That keeps a
record of what was intentionally simple at each stage, so a future
session doesn't mistake a scoped-out feature for a missing bug.

## NOT YET BUILT (deliberately deferred)

- **Playwright/dynamic (JS-rendered) site fetching** — the current fetch
  layer is Places API only; there is no raw HTML scraping of JS-heavy
  sites.
- **Proxy rotation** — not needed yet since the Places API is the primary
  source and doesn't require it. Would only become relevant if direct
  site scraping is added later.
- **Scheduling/cron automation** — currently manual CLI invocation only.
- **Slack/Sheets/webhook export** — currently CSV export + Streamlit
  dashboard only.
- **CRM integration** — status tracking currently lives only in the local
  SQLite database and dashboard, not synced to any external CRM.
- **Multi-user access to the dashboard** — currently single-user,
  local-only, no auth.

## BUILT (moved from above)

_(nothing moved yet)_
