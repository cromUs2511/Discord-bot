"""
main.py — Entry point for the Blackboard Discord Academic Assistant.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Ensure project root is in sys.path so modules like discord_bot are always found
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from discord_bot.bot import create_bot

# ------------------------------------------------------------------
# Logging configuration
# ------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)-22s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


async def main() -> None:
    logger.info("=" * 60)
    logger.info("Starting Blackboard Discord Academic Assistant...")
    logger.info("Configuration loaded:")
    logger.info("  • BLACKBOARD_URL: %s", config.BLACKBOARD_URL)
    logger.info("  • BLACKBOARD_USERNAME: %s", config.BLACKBOARD_USERNAME)
    logger.info("  • BLACKBOARD_PASSWORD: %s", "CONFIGURED" if config.BLACKBOARD_PASSWORD else "MISSING")
    logger.info("  • NOTIFICATION_PING: %s", config.NOTIFICATION_PING)
    logger.info("  • GENERAL_CHANNEL: %s", config.DISCORD_NOTIFICATION_CHANNEL_ID)
    logger.info("  • THESIS_1: %s", config.CHANNEL_THESIS_1)
    logger.info("  • CIRCUITS_LAB: %s", config.CHANNEL_CIRCUITS_LAB)
    logger.info("  • CIRCUITS_LECTURE: %s", config.CHANNEL_CIRCUITS_LECTURE)
    logger.info("  • FEEDBACK_CONTROL: %s", config.CHANNEL_FEEDBACK_CONTROL)
    logger.info("=" * 60)

    if not config.DISCORD_BOT_TOKEN:
        logger.error(
            "DISCORD_BOT_TOKEN is not set! Please add it to your .env file."
        )
        sys.exit(1)

    bot = create_bot()

    try:
        await bot.start(config.DISCORD_BOT_TOKEN)
    except KeyboardInterrupt:
        logger.info("Shutting down bot...")
    finally:
        if not bot.is_closed():
            await bot.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application exited by user.")
