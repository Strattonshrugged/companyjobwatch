import json
import os
import smtplib
import sys
from datetime import date
from email.mime.text import MIMEText
from urllib.parse import urlparse

import requests
import yaml
from bs4 import BeautifulSoup

CONFIG_FILE = "config.yaml"
HISTORY_FILE = "history.json"


def load_config():
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def load_history():
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)
        f.write("\n")


def fetch_lines_html(url):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    # Remove script and style elements
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    return [line.strip() for line in text.splitlines() if line.strip()]


def fetch_lines_workday(url, tenant, site_id, facets=None):
    # These sites render job listings client-side; the page itself is an
    # empty shell, so we hit the JSON API the frontend calls instead.
    netloc = urlparse(url).netloc
    api_url = f"https://{netloc}/wday/cxs/{tenant}/{site_id}/jobs"

    lines = []
    offset = 0
    limit = 20  # Workday's API rejects limit > 20 with an HTTP 400
    while True:
        response = requests.post(
            api_url,
            json={
                "appliedFacets": facets or {},
                "limit": limit,
                "offset": offset,
                "searchText": "",
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        postings = data.get("jobPostings", [])
        for job in postings:
            lines.append(f"{job['title']} - {job.get('locationsText', '')}")
        offset += limit
        if not postings or offset >= data.get("total", 0):
            break
    return lines


def fetch_lines_greenhouse(board, department=None):
    if department:
        api_url = f"https://boards-api.greenhouse.io/v1/boards/{board}/departments/{department}"
    else:
        api_url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
    response = requests.get(api_url, timeout=30)
    response.raise_for_status()
    data = response.json()
    return [
        f"{job['title']} - {job.get('location', {}).get('name', '')}"
        for job in data.get("jobs", [])
    ]


def fetch_lines_workable(account):
    api_url = f"https://apply.workable.com/api/v1/widget/accounts/{account}"
    response = requests.get(api_url, timeout=30)
    response.raise_for_status()
    data = response.json()
    return [
        f"{job['title']} - {job.get('department', '')}"
        for job in data.get("jobs", [])
    ]


def fetch_lines_smartrecruiters(company):
    api_url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings"

    lines = []
    offset = 0
    limit = 100
    while True:
        response = requests.get(
            api_url, params={"limit": limit, "offset": offset}, timeout=30
        )
        response.raise_for_status()
        data = response.json()
        content = data.get("content", [])
        for job in content:
            location = job.get("location", {}).get("city", "")
            lines.append(f"{job['name']} - {location}")
        offset += limit
        if not content or offset >= data.get("totalFound", 0):
            break
    return lines


def fetch_lines_typesense(
    base_url, api_key, collection, filter_by=None, title_field="name", city_field="city-2"
):
    # Some career sites run their job search through a hosted Typesense
    # instance with a public search-only API key embedded in the frontend JS
    # (visible via the browser's Network tab). That key is scoped to search,
    # not writes, so calling it directly is the same access the page itself has.
    api_url = f"{base_url}/multi_search"
    search = {
        "collection": collection,
        "q": "*",
        "query_by": title_field,
        "per_page": 250,
        "page": 1,
    }
    if filter_by:
        search["filter_by"] = filter_by

    lines = []
    page = 1
    while True:
        search["page"] = page
        response = requests.post(
            api_url,
            params={"x-typesense-api-key": api_key},
            json={"searches": [search]},
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()["results"][0]
        hits = result.get("hits", [])
        for hit in hits:
            doc = hit.get("document", {})
            lines.append(f"{doc.get(title_field, '')} - {doc.get(city_field, '')}")
        if len(hits) < search["per_page"] or page * search["per_page"] >= result.get("found", 0):
            break
        page += 1
    return lines


def fetch_lines_algolia(
    app_id, api_key, index_name, facet_filters=None, filters=None,
    title_field="title", city_field="primaryLocationCity",
):
    # Same idea as fetch_lines_typesense: some career sites run job search
    # through Algolia with a public search-only key visible in the frontend's
    # network requests. facet_filters mirrors Algolia's own facetFilters
    # array (list of "facet:value" strings, or a list-of-lists for OR groups).
    api_url = f"https://{app_id}-dsn.algolia.net/1/indexes/*/queries"

    lines = []
    page = 0
    while True:
        request = {
            "indexName": index_name,
            "hitsPerPage": 50,
            "page": page,
        }
        if facet_filters:
            request["facetFilters"] = facet_filters
        if filters:
            request["filters"] = filters
        response = requests.post(
            api_url,
            params={"x-algolia-api-key": api_key, "x-algolia-application-id": app_id},
            json={"requests": [request]},
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()["results"][0]
        hits = result.get("hits", [])
        for hit in hits:
            lines.append(f"{hit.get(title_field, '')} - {hit.get(city_field, '')}")
        page += 1
        if page >= result.get("nbPages", 0):
            break
    return lines


def fetch_lines_phenom(base_url, domain, query="", location=""):
    # Some career sites run on Phenom People's "pcsx" search widget, which
    # calls back to a first-party endpoint on the career site's own domain
    # (no API key needed, since it's the site's own backend, not a
    # third-party service key). Confirmed on CACI; other Phenom-based sites
    # may use an older widget generation without this endpoint (see Serco in
    # CLAUDE.md's known limitations - couldn't find an equivalent for it).
    api_url = f"{base_url}/api/pcsx/search"

    lines = []
    start = 0
    while True:
        response = requests.get(
            api_url,
            params={"domain": domain, "query": query, "location": location, "start": start},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()["data"]
        positions = data.get("positions", [])
        for position in positions:
            locs = position.get("locations") or [""]
            lines.append(f"{position.get('name', '')} - {locs[0]}")
        start += len(positions)
        if not positions or start >= data.get("count", 0):
            break
    return lines


def fetch_lines_jibe(base_url, location="", query=""):
    # Jibe (an iCIMS-owned ATS product, distinct from classic iCIMS) exposes
    # a first-party /api/jobs endpoint on the career site's own domain.
    # Confirmed on Akima.
    api_url = f"{base_url}/api/jobs"

    lines = []
    page = 1
    fetched = 0
    while True:
        response = requests.get(
            api_url,
            params={
                "lang": "en-us",
                "location": location,
                "q": query,
                "page": page,
                "sortBy": "relevance",
                "descending": "false",
                "internal": "false",
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        jobs = data.get("jobs", [])
        for job in jobs:
            doc = job.get("data", {})
            location_str = doc.get("full_location") or doc.get("short_location") or doc.get("city", "")
            lines.append(f"{doc.get('title', '')} - {location_str}")
        fetched += len(jobs)
        if not jobs or fetched >= data.get("totalCount", 0):
            break
        page += 1
    return lines


def fetch_lines_playwright(url, wait_ms=4000, max_pages=1):
    # Fallback for sites that render client-side and have no discoverable
    # public API. Heavier than the other fetchers (spins up a real browser),
    # so only use this when a direct API call genuinely isn't an option.
    # Some result pages are paginated behind a "Next" link/button - if
    # max_pages > 1, click through up to that many pages and concatenate the
    # text. This only works when a real "Next"-labelled link exists in the
    # page (confirmed e.g. on WBD's careers site); sites whose pagination
    # controls live in a shadow DOM or lack any text/aria-label won't be
    # reachable this way and will just yield their first page.
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                )
            )
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass  # some sites never go idle (polling/analytics); fall through to the fixed wait below
            page.wait_for_timeout(wait_ms)

            chunks = [page.inner_text("body")]
            for _ in range(max_pages - 1):
                next_link = page.get_by_text("Next", exact=True).first
                try:
                    if not next_link.is_visible():
                        break
                    next_link.click()
                except Exception:
                    break
                page.wait_for_timeout(wait_ms)
                chunks.append(page.inner_text("body"))
        finally:
            browser.close()
    text = "\n".join(chunks)
    return [line.strip() for line in text.splitlines() if line.strip()]


PLATFORM_FETCHERS = {
    "workday": lambda site: fetch_lines_workday(
        site["url"],
        site["workday_tenant"],
        site["workday_site"],
        site.get("workday_facets"),
    ),
    "greenhouse": lambda site: fetch_lines_greenhouse(
        site["greenhouse_board"], site.get("greenhouse_department")
    ),
    "workable": lambda site: fetch_lines_workable(site["workable_account"]),
    "smartrecruiters": lambda site: fetch_lines_smartrecruiters(
        site["smartrecruiters_company"]
    ),
    "typesense": lambda site: fetch_lines_typesense(
        site["typesense_base_url"],
        site["typesense_api_key"],
        site["typesense_collection"],
        site.get("typesense_filter_by"),
        site.get("typesense_title_field", "name"),
        site.get("typesense_city_field", "city-2"),
    ),
    "algolia": lambda site: fetch_lines_algolia(
        site["algolia_app_id"],
        site["algolia_api_key"],
        site["algolia_index"],
        site.get("algolia_facet_filters"),
        site.get("algolia_filters"),
        site.get("algolia_title_field", "title"),
        site.get("algolia_city_field", "primaryLocationCity"),
    ),
    "phenom": lambda site: fetch_lines_phenom(
        site["phenom_base_url"],
        site["phenom_domain"],
        site.get("phenom_query", ""),
        site.get("phenom_location", ""),
    ),
    "jibe": lambda site: fetch_lines_jibe(
        site["jibe_base_url"], site.get("jibe_location", ""), site.get("jibe_query", "")
    ),
    "playwright": lambda site: fetch_lines_playwright(
        site["url"],
        site.get("playwright_wait_ms", 4000),
        site.get("playwright_max_pages", 1),
    ),
}


def fetch_lines_for_site(site):
    platform = site.get("platform", "html")
    if platform == "html":
        return fetch_lines_html(site["url"])
    return PLATFORM_FETCHERS[platform](site)


def matching_lines(lines, keywords):
    lower_keywords = [kw.lower() for kw in keywords]
    return [
        line for line in lines
        if any(kw in line.lower() for kw in lower_keywords)
    ]


def send_email(to_review):
    host = os.environ["SMTP_HOST"]
    port = int(os.environ["SMTP_PORT"])
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]
    email_from = os.environ["EMAIL_FROM"]
    email_to = os.environ["EMAIL_TO"]

    lines = ["New matches found:\n"]
    for site_name, url, new_lines in to_review:
        lines.append(f"{site_name} ({url})")
        for line in new_lines:
            lines.append(f"  - {line}")
        lines.append("")

    body = "\n".join(lines).strip()
    subject = f"[companyjobwatch] New matches found — {date.today()}"

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to

    with smtplib.SMTP(host, port) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.sendmail(email_from, [email_to], msg.as_string())

    print(f"Email sent to {email_to}")


def main():
    config = load_config()
    history = load_history()
    keywords = config.get("keywords", [])
    sites = config.get("sites", [])

    to_review = []  # list of (site_name, url, [new_lines])

    for site in sites:
        name = site["name"]
        url = site["url"]
        print(f"Checking: {name} ({url})")

        try:
            current_lines = fetch_lines_for_site(site)
        except Exception as e:
            print(f"  ERROR fetching {url}: {e}", file=sys.stderr)
            continue

        current_matches = set(matching_lines(current_lines, keywords))
        previous_matches = set(history.get(url, []))

        new_lines = sorted(current_matches - previous_matches)
        removed_lines = previous_matches - current_matches

        if new_lines:
            print(f"  {len(new_lines)} new match(es) found")
            to_review.append((name, url, new_lines))

        if removed_lines:
            print(f"  {len(removed_lines)} match(es) removed")

        updated = (previous_matches | current_matches) - removed_lines
        history[url] = sorted(updated)

    save_history(history)
    print("History saved.")

    if to_review:
        send_email(to_review)
    else:
        print("No new matches — no email sent.")


if __name__ == "__main__":
    main()
