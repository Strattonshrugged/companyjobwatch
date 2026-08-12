# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`companyjobwatch` scrapes a configurable list of websites for lines matching keywords, maintains a history of found lines per site, and emails a summary whenever new matches appear. It runs on a GitHub Actions cron schedule.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the scraper locally (requires env vars below)
python scraper.py
```

Required environment variables for local runs:
```
SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO
```

## Architecture

All logic lives in `scraper.py`:

1. Loads `config.yaml` (list of sites + keywords to match)
2. Loads `history.json` (previously found matching lines, keyed by each site's `url`)
3. For each site: `fetch_lines_for_site()` dispatches on the site's `platform` field to get a list of text lines, then finds keyword matches
4. Diffs current matches against history — new lines trigger an email entry, removed lines are purged from history
5. Saves updated `history.json`
6. If any new matches were found, sends a single SMTP email listing each affected site and its new lines

### Fetching: HTML vs. platform APIs

Many career sites are client-rendered SPAs (Workday, Greenhouse, etc.) — the raw HTML is
an empty shell and `requests` + BeautifulSoup never sees the job listings. For those, we
call the ATS's own JSON API directly instead of scraping rendered HTML. Site fetching is
dispatched by `site.get("platform", "html")` in `fetch_lines_for_site()`:

| `platform` | Fetcher | Required config.yaml fields |
|---|---|---|
| *(unset)* / `html` | `fetch_lines_html` | `url` — plain `requests` + BeautifulSoup, works when the ATS renders listings server-side |
| `workday` | `fetch_lines_workday` | `workday_tenant`, `workday_site`; optional `workday_facets` (dict of facet-id → list, e.g. to filter by remote type / job family — inspect the site's own query params to find facet IDs) |
| `greenhouse` | `fetch_lines_greenhouse` | `greenhouse_board`; optional `greenhouse_department` (numeric department ID, scopes to one Greenhouse department) |
| `workable` | `fetch_lines_workable` | `workable_account` |
| `smartrecruiters` | `fetch_lines_smartrecruiters` | `smartrecruiters_company` (note: SmartRecruiters' public API returns *all* postings for the company, not scoped to a sub-brand/business-unit page) |

Before adding a new platform fetcher, confirm the ATS actually exposes a public JSON API —
check the site's Network tab for the XHR the frontend itself calls, and hit it directly with
`curl` to confirm the field names before wiring it into `scraper.py`. Don't guess endpoint
shapes.

**`config.yaml`** — edit this to add/remove sites and keywords. See the platform table above for site-level fields.  
**`history.json`** — committed back to the repo by the Actions workflow after each run; do not edit manually. History is keyed by `url`, so changing a site's `url` or `platform` resets its history — expect a one-time burst of "new matches" for that site on the next run since previously-seen postings look new again in the changed output format.  
**`DROPPED-SITES.md`** — companies intentionally removed from `config.yaml` (bot-blocked, low priority, etc.), with reasons, so they don't get silently re-added later.  
**`.github/workflows/jobwatch.yml`** — cron schedule is `0 */3 * * *` (every 3 hours); adjust as needed. Uses `workflow_dispatch` for manual triggers.

## Known limitations / future work

- **Playwright is intentionally not used yet.** Some sites are client-rendered with no
  discoverable public API, so they can't be fixed with the `html`-vs-platform-API approach
  above. Headless-browser rendering (e.g. Playwright) is the fallback for these, but adds
  real weight to the Actions run (browser install, slower per-site) — only reach for it if
  hand-picking an API really isn't possible. Known candidates, all currently returning 0
  matches or on plain-HTML fallback that won't actually find anything:
  - **Penn Entertainment** (ViziRecruiter) — checked its JS bundle for a data API, found
    none. Config entry is present but inert until this is solved.
  - **Max (Warner Bros. Discovery)**, **Crunchyroll** — custom SPAs, no obvious public API found yet.
  - **Pandora (SiriusXM)** — runs on iCIMS, which doesn't expose a clean no-auth public API.
- **`matching_lines()` does plain substring matching**, not word-boundary matching — e.g.
  the keyword `Test` matches inside `latest`. This is a known source of false-positive
  matches (confirmed in `history.json`) and hasn't been fixed yet.
- **Leidos** (`careers.leidos.com`) is blocked by Cloudflare (403) on every URL tried so
  far. Left in `config.yaml` as a known-dead entry (not `DROPPED-SITES.md`) because there's
  existing history worth keeping — it just won't ever produce new matches until unblocked
  some other way.

## GitHub Secrets Required

| Secret | Description |
|---|---|
| `SMTP_HOST` | SMTP server hostname |
| `SMTP_PORT` | SMTP port (typically `587`) |
| `SMTP_USER` | SMTP login username |
| `SMTP_PASSWORD` | SMTP password or app password |
| `EMAIL_FROM` | Sender address |
| `EMAIL_TO` | Recipient address |
