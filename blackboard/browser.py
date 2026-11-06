"""
blackboard/browser.py — Playwright Browser Management.

Provides helper functions to spawn Chromium instances and contexts
with saved storage states (cookies/local storage).
"""

import logging
import pathlib
from typing import AsyncGenerator
from contextlib import asynccontextmanager
from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
)
import config

logger = logging.getLogger("blackboard.browser")

# Path where authenticated session state (cookies/storage) is stored
SESSION_STORAGE_PATH = config.AUTH_STATE_DIR / "session.json"


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
    async with async_playwright() as pw:
        # Launch Chromium with anti-detection args suitable for student portals
        browser = await pw.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )

        storage_state_arg = (
            str(SESSION_STORAGE_PATH)
            if use_saved_state and SESSION_STORAGE_PATH.exists()
            else None
        )

        context = await browser.new_context(
            storage_state=storage_state_arg,
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        try:
            yield pw, browser, context
        finally:
            await context.close()
            await browser.close()
