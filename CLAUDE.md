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
| `typesense` | `fetch_lines_typesense` | `typesense_base_url`, `typesense_api_key`, `typesense_collection`; optional `typesense_filter_by`, `typesense_title_field` (default `name`), `typesense_city_field` (default `city-2`) |
| `algolia` | `fetch_lines_algolia` | `algolia_app_id`, `algolia_api_key`, `algolia_index`; optional `algolia_facet_filters`, `algolia_filters`, `algolia_title_field` (default `title`), `algolia_city_field` (default `primaryLocationCity`) |
| `phenom` | `fetch_lines_phenom` | `phenom_base_url`, `phenom_domain`; optional `phenom_query`, `phenom_location` — confirmed only on newer Phenom "pcsx" widget instances (CACI); older instances (Serco) don't expose this endpoint |
| `jibe` | `fetch_lines_jibe` | `jibe_base_url`; optional `jibe_location`, `jibe_query` — Jibe is an iCIMS-owned ATS product, distinct from classic iCIMS |
| `playwright` | `fetch_lines_playwright` | `url`; optional `playwright_wait_ms` (default `4000`), `playwright_max_pages` (default `1`) — headless-browser fallback for sites with no discoverable public API, see below |

Before adding a new platform fetcher, confirm the ATS actually exposes a public JSON API —
check the site's Network tab for the XHR the frontend itself calls, and hit it directly with
`curl` to confirm the field names before wiring it into `scraper.py`. Don't guess endpoint
shapes. Typesense/Algolia/Phenom/Jibe search keys found this way are meant to be public
(search-only, embedded directly in the frontend JS every visitor's browser already loads) —
calling them directly from `scraper.py` is the same access the page itself has, not a
credential leak. Only fall back to `platform: playwright` once that search comes up empty —
it's meaningfully heavier (spins up a real Chromium instance per site) and more fragile than
a direct API call.

**`config.yaml`** — edit this to add/remove sites and keywords. See the platform table above for site-level fields.  
**`history.json`** — committed back to the repo by the Actions workflow after each run; do not edit manually. History is keyed by `url`, so changing a site's `url` or `platform` resets its history — expect a one-time burst of "new matches" for that site on the next run since previously-seen postings look new again in the changed output format.  
**`DROPPED-SITES.md`** — companies intentionally removed from `config.yaml` (bot-blocked, low priority, etc.), with reasons, so they don't get silently re-added later.  
**`.github/workflows/jobwatch.yml`** — cron schedule is `0 */3 * * *` (every 3 hours); adjust as needed. Uses `workflow_dispatch` for manual triggers.

## Known limitations / future work

- **`platform: playwright` is the fallback for sites with no discoverable public API, OR
  for sites blocked by TLS-fingerprint WAFs even with a correct API/URL** (see the
  TLS-fingerprint bullet below) — currently Penn Entertainment (ViziRecruiter), Max (WBD),
  Pandora (SiriusXM), SAIC, and Amentum. It's meaningfully heavier than the other fetchers —
  a real headless Chromium launch per site — and the GitHub Actions workflow needs its
  `playwright install --with-deps chromium` step to have run for it to work at all.
  Verified with a real local Python + Playwright install (2026-08-12); per-site status for
  the original three (SAIC and Amentum's specifics are in their own `config.yaml` comments):
  - **Penn Entertainment** — works well, renders the full listing in one page.
  - **Max (WBD)** — works, but results are paginated (441 jobs, 10/page). Only the first
    `playwright_max_pages` (currently 3, i.e. 30 jobs, site's default sort) are fetched —
    not exhaustive. The "Next" control has no `href`/ARIA role, so pagination is driven by
    `get_by_text("Next", exact=True)`, not `get_by_role`; if WBD's markup changes, re-verify
    that selector still finds it before trusting silence as "no more pages."
  - **Pandora (SiriusXM)** — only page 1 (10 of 66 jobs) is reachable. Its pagination
    control isn't exposed as visible text or an ARIA name our generic clicker can find
    (probably shadow DOM) — didn't chase this further. Accepted as-is per user request.
  - **Crunchyroll was tried and dropped** (see `DROPPED-SITES.md`) — blocked by a
    Cloudflare Turnstile bot-verification challenge even through Playwright. Not worth
    engineering a bypass for a personal script.
- **`matching_lines()` does plain substring matching**, not word-boundary matching — e.g.
  the keyword `Test` matches inside `latest`. This is a known source of false-positive
  matches (confirmed in `history.json`) and hasn't been fixed yet.
- **Leidos** (`careers.leidos.com`) is blocked by Cloudflare (403) on every URL tried so
  far. Left in `config.yaml` as a known-dead entry (not `DROPPED-SITES.md`) because there's
  existing history worth keeping — it just won't ever produce new matches until unblocked
  some other way.
- **Workday's job-search API rejects `limit` > 20 with an HTTP 400** (no useful error
  message body — just `{"errorCode":"HTTP_400", ...}`). Found this the hard way by actually
  running `fetch_lines_workday` against Cerence's API with `limit=50`; confirmed the exact
  cutoff (20 works, 25 doesn't) by bisecting with `curl`. `fetch_lines_workday` now hardcodes
  `limit = 20` — don't raise it without reconfirming against a live tenant first.
- **Some 403s are a TLS-fingerprint block, not a missing-header problem** — discovered on
  SAIC: `requests.get()` got a 403 even with a full browser `User-Agent` header, while the
  exact same URL via `curl` (with or without a UA) succeeded, and Playwright (a real browser
  TLS stack) also succeeded. The WAF is fingerprinting the TLS handshake itself
  (JA3/JA4-style), not just checking headers - so no amount of header-spoofing in
  `requests` fixes it; only a real browser engine does. SAIC's `platform` is now
  `playwright` because of this. A full-config dry run (2026-08-12) turned up several other
  sites failing with plain 403/404 that weren't touched this session and may have the same
  root cause: Sportradar, Plex, Conviva, Wurl, Zendesk, GlobalStep (403s - worth checking if
  they're TLS-fingerprint blocks too), and Audible, Innovid, FX Digital, QAwerk, Witbe
  (404s - likely just stale URLs, same class of bug as the original Tubi/iHeartMedia/etc.
  fixes). Not fixed here since they're outside what was asked this session - flagging for
  a future pass.
- **Several defense-contractor entries are filtered to Washington/JBLM specifically**
  (SAIC, GDIT, Peraton, CACI, Amentum, Akima) — user is local to Joint Base Lewis-McChord
  and wants on-site/hybrid roles there over a nationwide feed. Each site's filtering
  mechanism (or why it couldn't be filtered) is documented inline as a YAML comment next to
  that site in `config.yaml` rather than duplicated here — check there first. Booz Allen,
  ManTech, Accenture Federal, and Serco stayed unfiltered/nationwide (Taleo autocomplete
  widgets and Accenture's search both resisted reasonable effort to filter; Serco's Phenom
  instance doesn't expose the endpoint CACI's does). If you add more defense-contractor
  sites later, keep the same bar: verify a real filtered result via `curl`/API before trusting
  a query-string guess — see e.g. Amentum's config comment, where a plausible-looking
  pre-filtered URL silently returned 0 results and a broader one had to be used instead.

## GitHub Secrets Required

| Secret | Description |
|---|---|
| `SMTP_HOST` | SMTP server hostname |
| `SMTP_PORT` | SMTP port (typically `587`) |
| `SMTP_USER` | SMTP login username |
| `SMTP_PASSWORD` | SMTP password or app password |
| `EMAIL_FROM` | Sender address |
| `EMAIL_TO` | Recipient address |
