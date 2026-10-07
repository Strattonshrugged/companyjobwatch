# jobwatch-webscrape-python

Watches a list of company career sites for job postings matching a set of keywords, and emails me when something new shows up.

### How it works
Once a day (GitHub Actions cron, 01:00 UTC), `scraper.py`:

1. Reads `config.yaml`, which holds the keywords to look for and the sites to check
2. Fetches each site and pulls out every line containing a keyword
3. Compares those lines against `history.json`, the record of what was already seen on each site
4. Emails a single summary of any new matches, then commits the updated `history.json` back to the repo

Lines that disappear from a site are dropped from history, so a posting that comes back later gets reported again.

### Fetching
Plain HTML scraping (`requests` + BeautifulSoup) is the default, but many career sites render their listings client-side, so the raw HTML comes back empty. For those, a site in `config.yaml` can set a `platform` that calls the job board's own public JSON API instead: `workday`, `greenhouse`, `workable`, `smartrecruiters`, `typesense`, `algolia`, `phenom` or `jibe`. When no API can be found, `playwright` drives a headless Chromium browser as a last resort. `CLAUDE.md` lists the config fields each platform needs.

### Layout
```
jobwatch-webscrape-python/
├── .github/workflows/jobwatch.yml   # Daily schedule + manual trigger; commits history.json
├── scraper.py                       # All fetching, diffing, and emailing logic
├── config.yaml                      # Keywords and the list of sites to watch
├── history.json                     # Previously seen matching lines, keyed by site URL (don't hand-edit)
├── DROPPED-SITES.md                 # Sites removed on purpose, and why
└── requirements.txt
```

### Usage

**Run locally:**
1. `pip install -r requirements.txt`
2. `playwright install chromium` (only needed for sites using `platform: playwright`)
3. Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM` and `EMAIL_TO`
4. `python scraper.py`

A local run updates `history.json` too, so the next scheduled run won't re-report anything it already found.

**Run via GitHub Actions:**
1. Add the six SMTP/email values above as repository secrets
2. It runs daily on its own; to trigger it by hand, go to the **Actions** tab → **Job Watch** → **Run workflow**

### Adding or removing sites
Edit `config.yaml`. A site needs at least a `name` and a `url`; add a `platform` and its fields if the page is client-rendered. Use `exclude_lines` to silence a specific line that matches a keyword but isn't a job (see the Walt Disney Company entry). Changing a site's `url` or `platform` resets its history, so expect one burst of "new" matches for that site on the next run. When you drop a site on purpose, note it in `DROPPED-SITES.md` so it doesn't get re-added later.

### Known limitations
- Keyword matching is plain substring matching, so `Test` also matches `latest`
- Some sites block automated requests (Cloudflare, TLS fingerprinting), and a few listed in `config.yaml` currently return 403/404
- Paginated sites under `platform: playwright` only fetch the first few pages

`CLAUDE.md` has the details on each.
