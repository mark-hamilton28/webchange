# Web Change Notifier

Watches websites and emails you a red/green diff when their content changes.
Runs free on GitHub Actions every 15 minutes, so your computer doesn't need to be on.

## Setup

### 1. Create a Gmail app password
1. Turn on 2-Step Verification for the Google account that will *send* the alerts
   (https://myaccount.google.com/security). Optionally use a separate Gmail
   account just for sending, so the password can't touch your main inbox.
2. Go to https://myaccount.google.com/apppasswords, name it `webchange`, and copy
   the 16-character password Google shows you.

### 2. Put the code on GitHub
Create a new repository on GitHub (empty, no README), then from this folder:

```bash
git init -b main
git add .
git commit -m "Web change notifier"
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

**Public or private?** Public repos get unlimited free Actions minutes; your
snapshots and `sites.yaml` would be visible, but secrets are never shown.
Private repos get 2,000 free minutes/month. Every-15-minute checks use about
2,900, so for a private repo change the cron in `.github/workflows/check.yml`
to `*/30 * * * *`.

### 3. Add secrets
In the repo: **Settings → Secrets and variables → Actions → New repository secret**

| Name            | Value                                       |
|-----------------|---------------------------------------------|
| `SMTP_USER`     | the Gmail address sending the alerts        |
| `SMTP_PASSWORD` | the app password from step 1                |
| `NOTIFY_TO`     | the address to receive alerts (can be same) |

### 4. Record a baseline
**Actions → Check websites → Run workflow.** The first run saves a snapshot of
each site (no email). After that it runs automatically and emails you on changes.

## Watching other sites
Edit `sites.yaml`, add an entry, and push. See the comments in that file for
`selector` (watch only part of a page) and `ignore` (skip noisy text like
countdown timers). If a site sends false alarms, add a `selector` or `ignore`.

## Running locally
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python webchange.py --dry-run      # print diffs, send nothing
SMTP_USER=... SMTP_PASSWORD=... NOTIFY_TO=... .venv/bin/python webchange.py --test-email
```

## Good to know
- GitHub's scheduled runs often start 5–15 minutes late, so expect alerts roughly
  10–30 minutes after a change.
- If a site fails to load 3 checks in a row, you get one "could not be checked" email.
- GitHub pauses scheduled workflows after 60 days with no repo activity. If that
  happens, re-enable it from the Actions tab.
- Each change is committed to `snapshots/`, so you can browse the full history on GitHub.
