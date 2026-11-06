# Blackboard Ultra Discord Academic Assistant

A self-hosted Discord bot that monitors a university Blackboard Ultra account and posts new announcements, assignments, deadline changes, and grade updates to Discord.

It uses your normal browser login (Microsoft 365 / Azure AD SSO + MFA), saves the browser storage state once, and then runs headlessly on a schedule.

## Features

- **One-time interactive login** with Playwright; cookies/localStorage are saved to `data/auth_state/session.json`.
- **Headless background sync** every N minutes (default: 30).
- **Per-course Discord channels** for Thesis, Circuits Lab, Circuits Lecture, and Feedback & Control, with a general-channel fallback.
- **Change detection** via SQLite fingerprints:
  - new announcement / assignment
  - edited announcement
  - changed deadline (old → new date)
  - silent grade/status/points updates
- **Reconciliation:** items removed from Blackboard are marked inactive; a reappearing item is reported again.
- **Slash commands:** `/sync`, `/status`, `/courses`, `/course`, `/announcements`, `/activities`, `/pending`, `/deadlines`, `/upcoming`, `/overdue`, `/summary`, `/clear`, `/ping`.
- **Session-aware health reporting:** `/status` shows whether the saved session was verified, plus the last sync result.

## Requirements

- Python 3.12+
- A Discord bot application with the **Server Members** intent optional (message-content intent is not required)
- A Blackboard Ultra account that uses normal browser SSO

## Setup

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

Copy `.env.example` to `.env` and fill in the values:

| Variable | Meaning |
|---|---|
| `DISCORD_BOT_TOKEN` | Bot token from the Discord Developer Portal |
| `DISCORD_GUILD_ID` | Your Discord server ID (for fast guild command sync) |
| `DISCORD_NOTIFICATION_CHANNEL_ID` | General/fallback notification channel |
| `CHANNEL_THESIS_1` | Optional course-specific channel |
| `CHANNEL_CIRCUITS_LAB` | Optional course-specific channel |
| `CHANNEL_CIRCUITS_LECTURE` | Optional course-specific channel |
| `CHANNEL_FEEDBACK_CONTROL` | Optional course-specific channel |
| `NOTIFICATION_PING` | `@everyone`, `@here`, a role mention, or empty to disable |
| `BLACKBOARD_URL` | e.g. `https://mapua.blackboard.com` (no trailing slash needed) |
| `ACADEMIC_TERM` | Optional, e.g. `2Q2627`; empty auto-detects all terms |
| `SYNC_INTERVAL_MINUTES` | Poll interval (minimum 1) |
| `TIMEZONE` | IANA timezone, default `Asia/Manila` |
| `INSTITUTION_NAME` | Footer branding text |
| `LOG_LEVEL` | `INFO`, `DEBUG`, etc. |

## Authentication

Run the visible browser once:

```powershell
python -m blackboard.authentication login
```

Log in normally, approve MFA, and wait until the script detects the Ultra dashboard. The session is written to `data/auth_state/session.json`.

Verify it headlessly:

```powershell
python -m blackboard.authentication verify
```

Sessions expire periodically (institutional policy). If `/status` reports **Expired**, run the login command again.

## Running the bot

```powershell
python main.py
```

On startup the bot:

1. Initializes/migrates SQLite (`data/academic_assistant.db`).
2. Syncs slash commands to `DISCORD_GUILD_ID` (or globally).
3. Starts the APScheduler sync job.
4. Verifies the Blackboard session in the background.

The first `/sync` performs the initial crawl. Subsequent runs only notify on changes.

## Tests

```powershell
python -m unittest discover -s tests -v
```

The suite covers deadline comparison, due-date ordering, channel scoping (including the lecture/lab prefix collision), overdue handling, points parsing, To Do course mapping, canonical activity IDs, announcement fallback safety, reconciliation, and schema migration.

## Architecture

```
main.py                  entry point
config.py                env-driven configuration
utils/dates.py           shared date/timezone parsing and sorting
blackboard/
  browser.py             Playwright context + storage state
  authentication.py      interactive login, headless verification
  courses.py             structural course-code discovery
  announcements.py       announcement scraper
  activities.py          To Do + gradebook scrapers
database/
  models.py              schema + indexes
  db.py                  async queries, change detection, reconciliation
discord_bot/
  bot.py                 client, command sync, startup checks
  commands.py            slash command suite
  embeds.py              rich embed templates
  notifications.py       strict channel routing + audit history
monitoring/
  synchronizer.py        crawl/diff/notify pipeline
  scheduler.py           APScheduler interval job
```

## Data and secrets

- `data/auth_state/session.json` contains session cookies — **never commit it**.
- `.env` contains the bot token — **never commit it**.
- `data/*.db` is local state.
- All three are covered by `.gitignore`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `DISCORD_BOT_TOKEN is not set` | Fill `.env` and restart. |
| `PrivilegedIntentsRequired` | Enable the intent in the Discord Developer Portal, or use only slash commands (this bot does not request message content). |
| `/status` says session expired | Run `python -m blackboard.authentication login`. |
| Sync completes with 0 courses | Check session validity, `BLACKBOARD_URL`, and that the dashboard shows courses. Set `ACADEMIC_TERM` if you intentionally restrict a term. |
| Notifications in the wrong channel | Set the `CHANNEL_*` variables; unmapped courses go to `DISCORD_NOTIFICATION_CHANNEL_ID`. |
| Deadline ordering looks wrong | The bot parses dates in Python; ensure due dates are stored in a supported format (see `utils/dates.py`). |

## Upgrade note

Activity IDs are now canonicalized (for example, `Coursework 1.1` and `Coursework 1.1 Submission` are one item). Existing databases may therefore emit one round of “new” notifications for gradebook items whose titles changed form. That is a one-time event; subsequent syncs are deduplicated normally.

## Deployment

A small always-on Linux VPS with 1–2 GB RAM is recommended. Render's free tier is not suitable: 512 MB RAM is too little for Chromium, and ephemeral disks wipe the session and database on restart. Use Docker or a systemd service with a persistent volume.
