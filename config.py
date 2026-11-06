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
