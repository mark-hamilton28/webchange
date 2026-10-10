#!/usr/bin/env python3
"""Watch websites for content changes and email a diff when one changes.

Sites are listed in sites.yaml. Each run fetches every site, extracts its
visible text, and compares it with the snapshot saved by the previous run.
"""

import argparse
import difflib
import html
import json
import os
import re
import smtplib
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "sites.yaml"
SNAPSHOT_DIR = ROOT / "snapshots"
STATE_PATH = SNAPSHOT_DIR / "state.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
FETCH_TIMEOUT = 30
FAILURE_ALERT_THRESHOLD = 3
MAX_DIFF_LINES = 300
STRIP_TAGS = ["script", "style", "noscript", "template", "svg", "iframe"]


class FetchError(Exception):
    pass


# ---------------------------------------------------------------- config/state

def load_sites():
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f) or {}
    sites = config.get("sites") or []
    for site in sites:
        if not site.get("name") or not site.get("url"):
            sys.exit(f"Each site in {CONFIG_PATH.name} needs a 'name' and a 'url': {site}")
    return [s for s in sites if s.get("enabled", True)]


def load_state():
    try:
        return json.loads(STATE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def snapshot_path(site):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", site["name"]).strip("-")
    return SNAPSHOT_DIR / f"{safe}.txt"


# ---------------------------------------------------------------- fetching

def fetch_text(site):
    """Return the normalized visible text of a site (or of its selector)."""
    try:
        resp = requests.get(
            site["url"],
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            timeout=FETCH_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise FetchError(str(e)) from e

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(STRIP_TAGS):
        tag.decompose()

    selector = site.get("selector")
    if selector:
        nodes = soup.select(selector)
        if not nodes:
            raise FetchError(f"CSS selector {selector!r} matched nothing on the page")
    else:
        nodes = [soup.body or soup]

    ignore = [re.compile(p) for p in site.get("ignore") or []]
    lines = []
    for node in nodes:
        for line in node.get_text("\n").splitlines():
            line = " ".join(line.split())
            if not line:
                continue
            for pattern in ignore:
                line = " ".join(pattern.sub("", line).split())
            if line:
                lines.append(line)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- email

def history_url(site):
    """Link to the snapshot's commit history on GitHub, when running in Actions."""
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    if not (server and repo):
        return None
    rel = snapshot_path(site).relative_to(ROOT).as_posix()
    return f"{server}/{repo}/commits/{branch}/{rel}"


def send_email(subject, text_body, html_body=None):
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    to = os.environ.get("NOTIFY_TO") or user
    if not (user and password):
        raise RuntimeError("SMTP_USER and SMTP_PASSWORD environment variables must be set")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"Web Change Notifier <{user}>"
    msg["To"] = to
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(user, password.replace(" ", ""))
        smtp.send_message(msg)


def build_change_email(site, diff_lines):
    added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))
    truncated = len(diff_lines) > MAX_DIFF_LINES
    shown = diff_lines[:MAX_DIFF_LINES]
    history = history_url(site)

    subject = f"🔔 {site['name']} changed (+{added} / -{removed} lines)"

    text = [f"{site['name']} changed: {site['url']}", ""]
    text += [l.rstrip("\n") for l in shown]
    if truncated:
        text.append(f"\n... diff truncated ({len(diff_lines)} lines total)")
    if history:
        text.append(f"\nFull history: {history}")

    rows = []
    for line in shown:
        line = line.rstrip("\n")
        if line.startswith(("+++", "---")):
            continue
        if line.startswith("@@"):
            style = "color:#6a737d;background:#f1f8ff;"
        elif line.startswith("+"):
            style = "color:#22863a;background:#e6ffed;"
        elif line.startswith("-"):
            style = "color:#b31d28;background:#ffeef0;"
        else:
            style = "color:#24292e;"
        rows.append(f'<div style="{style}padding:1px 6px;white-space:pre-wrap;">'
                    f"{html.escape(line) or '&nbsp;'}</div>")

    links = f'<a href="{html.escape(site["url"])}">Open the website</a>'
    if history:
        links += f' &nbsp;·&nbsp; <a href="{html.escape(history)}">Full change history</a>'
    note = (f"<p><em>Diff truncated — showing {MAX_DIFF_LINES} of {len(diff_lines)} lines.</em></p>"
            if truncated else "")
    html_body = (
        '<div style="font-family:-apple-system,Helvetica,Arial,sans-serif;">'
        f"<h2 style=\"margin:0 0 8px;\">{html.escape(site['name'])} changed</h2>"
        f"<p>{links}</p>"
        f'<p><span style="color:#22863a;">+{added} added</span> &nbsp; '
        f'<span style="color:#b31d28;">-{removed} removed</span></p>'
        '<div style="font-family:Menlo,Consolas,monospace;font-size:13px;'
        'border:1px solid #e1e4e8;border-radius:6px;">'
        + "".join(rows) + f"</div>{note}</div>"
    )
    return subject, "\n".join(text), html_body


# ---------------------------------------------------------------- main loop

def check_site(site, state, dry_run):
    """Check one site. Returns True if an email failed to send."""
    name = site["name"]
    path = snapshot_path(site)
    site_state = state.setdefault(name, {"failures": 0, "alerted": False})

    try:
        new_text = fetch_text(site)
    except FetchError as e:
        site_state["failures"] += 1
        site_state["last_error"] = str(e)
        print(f"[{name}] fetch failed ({site_state['failures']} in a row): {e}")
        if site_state["failures"] >= FAILURE_ALERT_THRESHOLD and not site_state["alerted"]:
            subject = f"⚠️ {name} could not be checked"
            body = (f"The last {site_state['failures']} checks of {site['url']} failed.\n\n"
                    f"Latest error: {e}\n\nYou won't get another alert for this site until it "
                    f"recovers and then fails again.")
            if dry_run:
                print(f"[{name}] (dry run) would send: {subject}")
            else:
                try:
                    send_email(subject, body)
                    site_state["alerted"] = True
                except Exception as mail_err:
                    print(f"[{name}] could not send failure alert: {mail_err}")
                    return True
        return False

    site_state.update(failures=0, alerted=False)
    site_state.pop("last_error", None)

    if not path.exists():
        path.write_text(new_text)
        print(f"[{name}] baseline saved ({len(new_text.splitlines())} lines)")
        return False

    old_text = path.read_text()
    if old_text == new_text:
        print(f"[{name}] no change")
        return False

    diff = list(difflib.unified_diff(
        old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
        fromfile="before", tofile="after", n=2,
    ))
    print(f"[{name}] CHANGED")
    if dry_run:
        sys.stdout.writelines(diff)
        print(f"[{name}] (dry run) snapshot not updated, no email sent")
        return False

    try:
        send_email(*build_change_email(site, diff))
    except Exception as e:
        # Keep the old snapshot so the next run retries the notification.
        print(f"[{name}] change detected but email failed: {e}")
        return True
    path.write_text(new_text)
    site_state["last_changed"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{name}] email sent, snapshot updated")
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="print diffs instead of emailing; don't update changed snapshots")
    parser.add_argument("--test-email", action="store_true",
                        help="send a test email and exit")
    args = parser.parse_args()

    if args.test_email:
        send_email("✅ Web Change Notifier test",
                   "If you can read this, email notifications are working.")
        print("Test email sent.")
        return

    SNAPSHOT_DIR.mkdir(exist_ok=True)
    state = load_state()
    email_failed = False
    for site in load_sites():
        email_failed |= check_site(site, state, args.dry_run)
    save_state(state)

    if email_failed:
        sys.exit("One or more notification emails failed to send.")


if __name__ == "__main__":
    main()
