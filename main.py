"""
main.py — Entry point for the Blackboard Discord Academic Assistant.

This file bootstraps logging, validates critical configuration, and
will eventually start the Discord bot + Blackboard monitoring loop.

For Phase 1 we only verify that the environment is wired up correctly.
"""

import asyncio
import logging
import sys

import config  # our centralized settings

# ------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)-22s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


async def verify_environment() -> bool:
    """Run a series of quick checks to confirm Phase 1 is complete."""
    all_ok = True

    # --- Python version ---
    v = sys.version_info
    logger.info("Python %s.%s.%s", v.major, v.minor, v.micro)
    if v < (3, 12):
        logger.error("Python 3.12+ is required.")
        all_ok = False

    # --- SQLite via aiosqlite ---
    try:
        import aiosqlite

        async with aiosqlite.connect(":memory:") as db:
            cursor = await db.execute("SELECT sqlite_version()")
            row = await cursor.fetchone()
            logger.info("aiosqlite OK — SQLite %s", row[0] if row else "?")
    except ImportError:
        logger.error("aiosqlite is not installed.")
        all_ok = False

    # --- Playwright ---
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto("https://example.com", timeout=15000)
            title = await page.title()
            await browser.close()
            logger.info("Playwright OK — loaded page title: %s", title)
    except ImportError:
        logger.error("playwright is not installed.")
        all_ok = False
    except Exception as exc:
        logger.error("Playwright browser test failed: %s", exc)
        all_ok = False

    # --- discord.py ---
    try:
        import discord

        logger.info("discord.py OK — version %s", discord.__version__)
    except ImportError:
        logger.error("discord.py is not installed.")
        all_ok = False

    # --- python-dotenv (.env loading) ---
    if config.DISCORD_BOT_TOKEN:
        logger.info(".env loaded — DISCORD_BOT_TOKEN is set (value hidden).")
    else:
        logger.warning(
            ".env note: DISCORD_BOT_TOKEN is empty. "
            "This is expected in Phase 1; you will set it in Phase 2."
        )

    # --- Directories ---
    logger.info("Project root : %s", config.PROJECT_ROOT)
    logger.info("Data dir     : %s", config.DATA_DIR)
    logger.info("Database path: %s", config.DATABASE_PATH)
    logger.info("Auth state   : %s", config.AUTH_STATE_DIR)

    return all_ok


async def main() -> None:
    logger.info("=" * 60)
    logger.info("Blackboard Discord Academic Assistant — Environment Check")
    logger.info("=" * 60)

    ok = await verify_environment()

    if ok:
        logger.info("=" * 60)
        logger.info("✅  Phase 1 complete — all checks passed!")
        logger.info("=" * 60)
    else:
        logger.error("=" * 60)
        logger.error("❌  Some checks failed. Review the errors above.")
        logger.error("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
