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

# ------------------------------------------------------------------
# Discord
# ------------------------------------------------------------------
DISCORD_BOT_TOKEN: str = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_GUILD_ID: int = int(os.getenv("DISCORD_GUILD_ID", "0"))
DISCORD_NOTIFICATION_CHANNEL_ID: int = int(
    os.getenv("DISCORD_NOTIFICATION_CHANNEL_ID", "0")
)

# Specific Course Dedicated Channels
CHANNEL_THESIS_1: int = int(os.getenv("CHANNEL_THESIS_1", "1567109364664238150"))
CHANNEL_CIRCUITS_LAB: int = int(os.getenv("CHANNEL_CIRCUITS_LAB", "1567109422767939604"))
CHANNEL_CIRCUITS_LECTURE: int = int(os.getenv("CHANNEL_CIRCUITS_LECTURE", "1567109447971774494"))
CHANNEL_FEEDBACK_CONTROL: int = int(os.getenv("CHANNEL_FEEDBACK_CONTROL", "1567109482054549544"))

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
    """Returns the course ID pattern corresponding to a channel, or None if general."""
    if channel_id == CHANNEL_THESIS_1:
        return "CPE200"
    elif channel_id == CHANNEL_CIRCUITS_LAB:
        return "ECEA101-1L"
    elif channel_id == CHANNEL_CIRCUITS_LECTURE:
        return "ECEA101-1_A1"  # Matches lecture, not 1L lab
    elif channel_id == CHANNEL_FEEDBACK_CONTROL:
        return "CPE121"
    return None

# ------------------------------------------------------------------
# Blackboard
# ------------------------------------------------------------------
BLACKBOARD_URL: str = os.getenv("BLACKBOARD_URL", "")

# ------------------------------------------------------------------
# Scheduling
# ------------------------------------------------------------------
SYNC_INTERVAL_MINUTES: int = int(os.getenv("SYNC_INTERVAL_MINUTES", "30"))

# ------------------------------------------------------------------
# Timezone
# ------------------------------------------------------------------
TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Manila")

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
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
