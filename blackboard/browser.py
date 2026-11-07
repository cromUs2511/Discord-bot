"""
blackboard/browser.py — Playwright Browser Management.

Provides helper functions to spawn Chromium instances and contexts
with saved storage states (cookies/local storage).
"""

import logging
from typing import AsyncGenerator
from contextlib import asynccontextmanager
from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Playwright,
)
import config

logger = logging.getLogger("blackboard.browser")

# Path where authenticated session state (cookies/storage) is stored
SESSION_STORAGE_PATH = config.AUTH_STATE_DIR / "session.json"


def ensure_session_file() -> bool:
    """
    Restores session.json from the BLACKBOARD_SESSION_DATA environment variable
    if the file does not already exist on disk (ideal for cloud platforms).
    """
    if not SESSION_STORAGE_PATH.exists() and config.BLACKBOARD_SESSION_DATA:
        try:
            SESSION_STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
            SESSION_STORAGE_PATH.write_text(config.BLACKBOARD_SESSION_DATA.strip(), encoding="utf-8")
            logger.info("Restored session.json from BLACKBOARD_SESSION_DATA environment variable.")
            return True
        except Exception as e:
            logger.error("Failed to restore session from BLACKBOARD_SESSION_DATA: %s", e)
    return SESSION_STORAGE_PATH.exists()


@asynccontextmanager
async def get_browser_context(
    headless: bool = True,
    use_saved_state: bool = True,
) -> AsyncGenerator[tuple[Playwright, Browser, BrowserContext], None]:
    """
    Context manager that launches Chromium and creates a browser context.
    
    If `use_saved_state` is True and `session.json` exists, the context
    is initialized with pre-existing session cookies and local storage.
    """
    ensure_session_file()
    browser_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
    ]

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(
                headless=headless,
                args=browser_args,
            )
        except Exception as e:
            err_msg = str(e).lower()
            if "executable doesn't exist" in err_msg or "please run" in err_msg:
                logger.info("Playwright Chromium browser missing. Installing automatically...")
                import subprocess
                import sys
                subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
                browser = await pw.chromium.launch(
                    headless=headless,
                    args=browser_args,
                )
            else:
                raise

        storage_state_arg = (
            str(SESSION_STORAGE_PATH)
            if use_saved_state and SESSION_STORAGE_PATH.exists()
            else None
        )

        # Do not override the user agent: Playwright's bundled Chromium UA
        # matches the actual browser version, which reduces SSO/bot-detection
        # mismatches.
        context = await browser.new_context(
            storage_state=storage_state_arg,
            viewport={"width": 1280, "height": 800},
            timezone_id=config.TIMEZONE,
            locale="en-PH",
        )

        try:
            yield pw, browser, context
        finally:
            await context.close()
            await browser.close()
