# MASTER ARCHITECTURAL & OPERATIONAL SPECIFICATION
## Blackboard Ultra-to-Discord Academic Assistant

You are an expert full-stack Python systems engineer, browser automation specialist (Playwright), and Discord bot architect. Below is the complete design, domain knowledge, operational state, and codebase architecture for the **Blackboard Ultra Academic Assistant**. Use this context to continue maintaining, debugging, extending, or deploying this system.

---

### 1. CORE PROBLEM & ARCHITECTURAL FOUNDATION

1. **Student Constraints:**
   - The user is a university student without Blackboard administrative privileges or institutional REST API access keys.
   - Authentication relies on normal browser login via Microsoft 365 / Azure AD Single Sign-On (SSO) with Multi-Factor Authentication (MFA / 2FA Authenticator app).

2. **Session Persistence Architecture:**
   - **Interactive Bootstrapping:** Playwright spawns a visible Chromium browser (`headless=False`) once via `python -m blackboard.authentication login`. The student enters credentials and approves MFA.
   - **State Extraction:** Upon reaching `/ultra/*`, Playwright dumps the full browser context (session cookies, tokens, `localStorage`) into a secured file: `data/auth_state/session.json`.
   - **Headless Runtime:** All background tasks run headlessly (`headless=True`) using `storage_state="data/auth_state/session.json"`. No credentials or passwords are ever hardcoded or stored in plaintext.

3. **Blackboard Ultra Single Page Application (SPA) DOM Nuances:**
   - **Dynamic Navigation:** Blackboard Ultra runs on an Angular/React single-page architecture with background WebSockets. Never use `wait_until="networkidle"` as it will time out; always use `wait_until="domcontentloaded"`.
   - **Direct Outline Routing:** Blackboard Ultra course cards in `/ultra/course` overlay `<div class="image-div">` banner elements that intercept pointer clicks. **Do not click course cards on the main menu.** Instead, extract the internal course ID (`id="course-id-_XXXXXX_1"`) from each card and navigate directly to:
     `https://<university>.blackboard.com/ultra/courses/_XXXXXX_1/outline`
   - **Tab Navigation Inside Courses:**
     - Announcements tab: `[role="tab"]:has-text("Announcements"), a:has-text("Announcements")` (with `force=True`).
     - Gradebook tab: `[role="tab"]:has-text("Gradebook"), a:has-text("Gradebook")` (with `force=True`).

---

### 2. DATA PIPELINE & CHANGE DETECTION

1. **Extraction Capabilities:**
   - **Courses:** Code (`CPE121-4_C1_2Q2627`), Name (`FEEDBACK AND CONTROL SYSTEMS`), Instructor (`John Peter Ramos`), Status (`Open` / `Closed`), Term (`2Q2627`), Direct Outline URL.
   - **Announcements:** Unique MD5 ID (`course_id + title`), Title, Body content preview, Author, SHA256 content fingerprint.
   - **Activities & Deadlines:** Unique MD5 ID (`course_id + title`), Title, Due Date (supports multiple formats), Submission Status (`Submitted`, `Not submitted`, `Graded`), Points (`1/1`, `%`), SHA256 status fingerprint.

2. **Intelligent Change Detection (Database Layer):**
   - Implemented via `aiosqlite` in `data/academic_assistant.db`.
   - **New Item:** Discovered ID not in database -> Trigger "New Announcement" or "New Assignment" Discord Embed.
   - **Deadline Change:** Existing activity ID where `due_date` changed -> Trigger "⏰ DEADLINE CHANGED" Discord Embed showing `~~Old Date~~` -> `**New Date**`.
   - **Content Update:** Fingerprint changed -> Trigger "📢 UPDATED ANNOUNCEMENT" Embed.
   - **Deduplication:** Repeated runs produce zero duplicate notifications because fingerprints match previously stored hashes.

---

### 3. PROJECT DIRECTORY STRUCTURE

```
Blackboard Announcement BOT/
├── main.py                     # Application entry point & bot runtime runner
├── config.py                   # Centralized configuration loaded from .env
├── requirements.txt            # discord.py, playwright, aiosqlite, apscheduler, python-dotenv
├── .env                        # Local secrets (BOT_TOKEN, GUILD_ID, CHANNEL_ID, BLACKBOARD_URL)
├── .gitignore                  # Ignores venv/, data/*.db, data/auth_state/
│
├── blackboard/                 # Browser automation & scraping
│   ├── browser.py              # Playwright context manager & session state loader
│   ├── authentication.py       # Interactive login & headless session verification
│   ├── courses.py              # Course catalog extractor with internal ID resolution
│   ├── announcements.py        # Tab scraper for announcements
│   └── activities.py           # Gradebook scraper for assignments, points, and deadlines
│
├── discord_bot/                # Discord UI & Slash Commands
│   ├── bot.py                  # Client setup, setup_hook, command sync, scheduler startup
│   ├── commands.py             # Slash commands (/ping, /status, /sync, /courses, /course,
│   │                           #  /announcements, /activities, /pending, /deadlines,
│   │                           #  /upcoming, /overdue, /summary, /clear)
│   ├── embeds.py               # Color-coded rich embed templates
│   └── notifications.py        # Dispatches embeds to configured Discord notification channel
│
├── database/                   # SQLite persistence layer (aiosqlite)
│   ├── db.py                   # Async DatabaseManager class & complex academic queries
│   └── models.py               # Table DDL schemas (courses, announcements, activities)
│
├── monitoring/                 # Background tasks & synchronization
│   ├── synchronizer.py         # Complete non-blocking multi-course crawl & diff pipeline
│   └── scheduler.py            # AsyncIOScheduler running every 30 mins (Asia/Manila)
│
└── data/                       # Local runtime directory
    ├── academic_assistant.db   # Persistent SQLite database
    └── auth_state/             # Contains session.json (saved cookies/tokens)
```

---

### 4. DISCORD COMMANDS SUITE

| Slash Command | Behavior | Response Time |
|---|---|---|
| `/summary` | Consolidated dashboard: Enrolled courses, total coursework, pending items, recent announcements | `< 0.1s` (DB) |
| `/activities [course]` | All coursework items, points, and submission statuses (with course dropdown autocomplete) | `< 0.1s` (DB) |
| `/course [course]` | Unified view of a single subject's announcements AND coursework (autocomplete enabled) | `< 0.1s` (DB) |
| `/pending` | Filtered list of assignments you have NOT yet submitted | `< 0.1s` (DB) |
| `/deadlines` | All dated assignments sorted in chronological order | `< 0.1s` (DB) |
| `/upcoming` | Assignments due within the next 7 days (color-coded 🔴 Due Today/Tomorrow, 🟡 Soon, 🟢 Safe) | `< 0.1s` (DB) |
| `/overdue` | Past-due activities not yet submitted | `< 0.1s` (DB) |
| `/announcements [course]` | Recent announcements with preview content (autocomplete enabled) | `< 0.1s` (DB) |
| `/courses` | Catalog of all enrolled subjects, instructor names, and open/closed statuses | `< 0.1s` (DB) |
| `/sync` | Triggers a live Blackboard headless browser scrape in the background; gives an immediate yellow acknowledgment card and edits to green with exact counts once finished | Non-blocking (`< 0.5s` ack) |
| `/clear [target] [amount]` | Purges either only the bot's own messages (default) or the entire channel chat | Ephemeral (`< 0.5s`) |
| `/status` & `/ping` | Health diagnostics (gateway latency, session file status, channel connection, uptime) | `< 0.1s` |

---

### 5. REAL-TIME MENTIONS & CHANNEL NOTIFICATIONS

- **Automated Pings:** Whenever new announcements or added/changed coursework activities are detected during background sync passes, the bot attaches a mention (e.g. `@everyone` or `@here`) to the message so members are actively notified.
- **Configurable via `.env`:** Set `NOTIFICATION_PING=@everyone` or `NOTIFICATION_PING=@here` (or leave empty `NOTIFICATION_PING=` to disable pings).
- **Subject Channel Isolation:** Notifications are dispatched strictly to their dedicated course channel (`#circuits-lecture`, `#circuits-lab`, `#feedback-and-control`, `#thesis-1`), or `#announcements` for unmapped courses.

---

### 6. CLOUD HOSTING & PRODUCTION CONSIDERATIONS

- **Why Render Free Tier is Incompatible:** Render free web services have 512MB RAM (causing Chromium to OOM crash) and ephemeral disks (which wipe `session.json` and `academic_assistant.db` on restart/sleep).
- **Recommended Cloud Deployment:** A low-cost permanent Linux VPS (DigitalOcean Droplet, Hetzner, Linode, or AWS Lightsail with 1–2GB RAM) running Docker or a systemd service, or a dedicated home server/Raspberry Pi.
