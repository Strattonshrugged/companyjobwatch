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
    limit = 50
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
