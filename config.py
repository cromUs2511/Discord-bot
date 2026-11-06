"""
config.py — Centralized configuration for the Blackboard Discord Academic Assistant.

All settings are loaded from environment variables (via a .env file).
This module is imported by every other module that needs configuration,
so nothing else needs to read .env directly.
"""

import os
import pathlib

from dotenv import load_dotenv

# ------------------------------------------------------------------
# Load .env from the project root (one level above this file).
# ------------------------------------------------------------------
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")


def _env_int(name: str, default: int = 0) -> int:
    """Read an integer env var without crashing on placeholders/empty values."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value.strip() if value is not None else default


# ------------------------------------------------------------------
# Discord
# ------------------------------------------------------------------
DISCORD_BOT_TOKEN: str = _env_str("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID: int = _env_int("DISCORD_GUILD_ID", 0)
DISCORD_NOTIFICATION_CHANNEL_ID: int = _env_int(
    "DISCORD_NOTIFICATION_CHANNEL_ID", 0
)

# Notifications are mentioned with this string (e.g. "@everyone", "@here").
# Leave empty to disable pings.
NOTIFICATION_PING: str = _env_str("NOTIFICATION_PING", "@everyone")

# Specific course dedicated channels. Set these in .env for your server.
CHANNEL_THESIS_1: int = _env_int("CHANNEL_THESIS_1", 0)
CHANNEL_CIRCUITS_LAB: int = _env_int("CHANNEL_CIRCUITS_LAB", 0)
CHANNEL_CIRCUITS_LECTURE: int = _env_int("CHANNEL_CIRCUITS_LECTURE", 0)
CHANNEL_FEEDBACK_CONTROL: int = _env_int("CHANNEL_FEEDBACK_CONTROL", 0)


def get_course_channel_scope(channel_id: int | None) -> dict | None:
    """
    Resolves a Discord channel into a course-ID scope.

    Returns a dict with:
      - prefix:  course IDs starting with this value belong to the channel
      - exclude: optional prefix to exclude (e.g. lab vs lecture)
      - label:   human-readable channel label
    Returns None for the general/unmapped channel.
    """
    if not channel_id:
        return None

    if channel_id == CHANNEL_THESIS_1:
        return {"prefix": "CPE200", "exclude": None, "label": "Thesis 1"}
    # Lab must be checked before the lecture prefix because the lab ID
    # ("ECEA101-1L...") also starts with "ECEA101-1".
    if channel_id == CHANNEL_CIRCUITS_LAB:
        return {"prefix": "ECEA101-1L", "exclude": None, "label": "Circuits Lab"}
    if channel_id == CHANNEL_CIRCUITS_LECTURE:
        return {
            "prefix": "ECEA101-1",
            "exclude": "ECEA101-1L",
            "label": "Circuits Lecture",
        }
    if channel_id == CHANNEL_FEEDBACK_CONTROL:
        return {"prefix": "CPE121", "exclude": None, "label": "Feedback & Control"}
    return None


def get_course_channel_id(course_id: str, course_name: str = "") -> int:
    """Resolves the dedicated channel ID for a given course, or falls back to default."""
    cid = (course_id or "").upper()
    cname = (course_name or "").lower()

    if "CPE200" in cid or "thesis" in cname:
        return CHANNEL_THESIS_1
    if "ECEA101-1L" in cid or "laboratory" in cname or "circuits lab" in cname:
        return CHANNEL_CIRCUITS_LAB
    if "ECEA101-1" in cid or "electronic circuits" in cname:
        return CHANNEL_CIRCUITS_LECTURE
    if "CPE121" in cid or "feedback" in cname:
        return CHANNEL_FEEDBACK_CONTROL

    # Default fallback
    return DISCORD_NOTIFICATION_CHANNEL_ID


def get_course_id_for_channel(channel_id: int) -> str | None:
    """Backwards-compatible helper returning the course-ID prefix for a channel."""
    scope = get_course_channel_scope(channel_id)
    return scope["prefix"] if scope else None


# ------------------------------------------------------------------
# Blackboard
# ------------------------------------------------------------------
BLACKBOARD_URL: str = _env_str("BLACKBOARD_URL").rstrip("/")

# Optional: only monitor courses from this term (e.g. "2Q2627").
# Leave empty to auto-detect every term found on the course dashboard.
ACADEMIC_TERM: str = _env_str("ACADEMIC_TERM")

# ------------------------------------------------------------------
# Scheduling
# ------------------------------------------------------------------
SYNC_INTERVAL_MINUTES: int = max(1, _env_int("SYNC_INTERVAL_MINUTES", 30))

# ------------------------------------------------------------------
# Timezone
# ------------------------------------------------------------------
TIMEZONE: str = _env_str("TIMEZONE", "Asia/Manila")

# ------------------------------------------------------------------
# Branding
# ------------------------------------------------------------------
INSTITUTION_NAME: str = _env_str("INSTITUTION_NAME", "Mapúa University")

# ------------------------------------------------------------------
# Paths
# ------------------------------------------------------------------
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

DATABASE_PATH = DATA_DIR / "academic_assistant.db"
AUTH_STATE_DIR = DATA_DIR / "auth_state"
AUTH_STATE_DIR.mkdir(exist_ok=True)

# ------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------
LOG_LEVEL: str = _env_str("LOG_LEVEL", "INFO").upper()
