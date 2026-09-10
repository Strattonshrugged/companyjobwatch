import json
import re
import sys

import anthropic

from scraper import load_history, save_history, send_email

# Not a real site URL - just a stable key so this reuses the same
# load/diff/save history mechanism as every entry in config.yaml.
SEARCH_KEY = "ai-search:connected-devices-smart-tv-stb"

PROMPT = """Search the web for current QA, software testing, and quality assurance job \
openings at companies that build connected TV apps, smart TV platforms, or set-top box / \
streaming device software. This includes companies making the devices and firmware (e.g. \
Roku, Amazon Fire TV, Google TV, Samsung Tizen, LG webOS, Comcast/Xfinity, set-top box \
vendors) as well as companies building apps FOR those platforms (streaming services, \
connected-device software vendors, smart TV ad/analytics platforms).

Focus specifically on QA Engineer, QA Analyst, Software Test Engineer, SDET, Quality \
Assurance, UAT, or similar testing-focused roles - not general software engineering roles, \
even at these companies.

Search broadly (job boards, company career pages, LinkedIn postings) rather than relying on \
memory - only include postings you can point to a real URL for.

Respond with ONLY a JSON array (no other text before or after it) of objects with these \
exact keys: "company", "title", "location", "url". If you find no relevant postings, \
respond with an empty array: []"""


def extract_json_array(text):
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON array found in response: {text[:500]}")
    return json.loads(match.group(0))


def search_jobs(client):
    messages = [{"role": "user", "content": PROMPT}]
    response = None
    for _ in range(5):
        response = client.messages.create(
            model="claude-opus-5",
            max_tokens=8000,
            output_config={"effort": "medium"},
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 10}],
            messages=messages,
        )
        if response.stop_reason != "pause_turn":
            break
        # Server-tool loop hit its iteration cap - resend to let it continue.
        messages = [messages[0], {"role": "assistant", "content": response.content}]

    text = next(
        (b.text for b in reversed(response.content) if b.type == "text"), ""
    )
    return extract_json_array(text)


def main():
    history = load_history()
    client = anthropic.Anthropic()

    try:
        jobs = search_jobs(client)
    except Exception as e:
        print(f"ERROR searching for jobs: {e}", file=sys.stderr)
        return

    current_matches = {
        f"{job['company']} - {job['title']} ({job['location']}) - {job['url']}"
        for job in jobs
        if job.get("company") and job.get("title") and job.get("url")
    }
    previous_matches = set(history.get(SEARCH_KEY, []))

    new_lines = sorted(current_matches - previous_matches)
    removed_lines = previous_matches - current_matches

    if new_lines:
        print(f"{len(new_lines)} new match(es) found")
    if removed_lines:
        print(f"{len(removed_lines)} match(es) no longer listed")

    updated = (previous_matches | current_matches) - removed_lines
    history[SEARCH_KEY] = sorted(updated)
    save_history(history)
    print("History saved.")

    if new_lines:
        send_email(
            [("Connected Devices / Smart TV / STB QA Search (AI)", "n/a", new_lines)]
        )
    else:
        print("No new matches - no email sent.")


if __name__ == "__main__":
    main()
