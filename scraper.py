import json
import os
import smtplib
import sys
from datetime import date
from email.mime.text import MIMEText

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


def fetch_lines(url):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    # Remove script and style elements
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    return [line.strip() for line in text.splitlines() if line.strip()]


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
            current_lines = fetch_lines(url)
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
