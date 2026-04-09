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
2. Loads `history.json` (previously found matching lines, keyed by URL)
3. For each site: fetches the page, extracts visible text lines, finds keyword matches
4. Diffs current matches against history — new lines trigger an email entry, removed lines are purged from history
5. Saves updated `history.json`
6. If any new matches were found, sends a single SMTP email listing each affected site and its new lines

**`config.yaml`** — edit this to add/remove sites and keywords.  
**`history.json`** — committed back to the repo by the Actions workflow after each run; do not edit manually.  
**`.github/workflows/jobwatch.yml`** — cron schedule defaults to `0 8 * * *` (08:00 UTC daily); adjust as needed. Uses `workflow_dispatch` for manual triggers.

## GitHub Secrets Required

| Secret | Description |
|---|---|
| `SMTP_HOST` | SMTP server hostname |
| `SMTP_PORT` | SMTP port (typically `587`) |
| `SMTP_USER` | SMTP login username |
| `SMTP_PASSWORD` | SMTP password or app password |
| `EMAIL_FROM` | Sender address |
| `EMAIL_TO` | Recipient address |
