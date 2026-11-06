"""
blackboard/authentication.py — Blackboard Ultra Interactive Login and Session Verification.

Handles:
- Interactive login: Spawns visible browser for user to authenticate with Microsoft/MFA.
- Session export: Saves cookies & localStorage to session.json.
- Session check: Validates whether existing storage state can access Ultra dashboard.
"""

import asyncio
import logging
import time

import config
from blackboard.browser import (
    SESSION_STORAGE_PATH,
    get_browser_context,
)

logger = logging.getLogger("blackboard.auth")


async def is_authenticated_url(url: str) -> bool:
    """
    Checks if a given URL indicates an active, logged-in Blackboard Ultra session.
    Blackboard Ultra URLs typically contain /ultra/ (e.g. /ultra/institution-page, /ultra/course).
    Login screens typically contain /webapps/login or login.microsoftonline.com.
    """
    url_lower = url.lower()
    if "login.microsoftonline.com" in url_lower:
        return False
    if "webapps/login" in url_lower:
        return False
    if "/ultra" in url_lower:
        return True
    return False


async def login_interactive() -> bool:
    """
    Launches a visible Chromium window for manual login.
    Waits for the student to log in, complete Microsoft MFA, and reach the Ultra dashboard.
    Once authenticated, saves session state to `data/auth_state/session.json`.
    """
    logger.info("Starting interactive login...")
    logger.info("Opening visible browser to: %s", config.BLACKBOARD_URL)

    print("\n" + "=" * 65)
    print("👉 A browser window will now open.")
    print(f"👉 Log into your {config.INSTITUTION_NAME} Blackboard account as you normally do.")
    print("👉 Complete any Microsoft / MFA / 2FA prompts.")
    print("👉 Check 'Stay signed in' if prompted.")
    print("👉 The script will automatically detect when you reach the dashboard!")
    print("=" * 65 + "\n")

    async with get_browser_context(headless=False, use_saved_state=False) as (_, _, context):
        page = await context.new_page()

        try:
            await page.goto(config.BLACKBOARD_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            logger.warning("Initial navigation notice: %s. Continuing anyway...", e)

        # Wait loop: Poll until the page URL navigates into Blackboard Ultra
        # Give the student up to 5 minutes (300 seconds) to complete login & MFA.
        max_wait_seconds = 300
        start_time = time.time()
        authenticated = False

        while time.time() - start_time < max_wait_seconds:
            current_url = page.url
            
            # Check if student reached the Ultra interface
            if await is_authenticated_url(current_url):
                # Extra check: wait for blackboard ultra navigation bar or main content
                try:
                    await page.wait_for_selector(
                        "nav, [role='navigation'], #main-content-inner, .base-layout",
                        timeout=5000,
                    )
                except Exception:
                    pass

                logger.info("Successfully reached Blackboard Ultra! Current URL: %s", current_url)
                authenticated = True
                break

            await asyncio.sleep(2)

        if not authenticated:
            logger.error("Login timed out or was not completed within %d seconds.", max_wait_seconds)
            return False

        # Ensure directory exists and save state
        SESSION_STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=str(SESSION_STORAGE_PATH))
        logger.info("✅ Session state saved to: %s", SESSION_STORAGE_PATH)
        print("\n✅ Authentication successful! Session saved.\n")
        return True


async def verify_session() -> bool:
    """
    Headlessly tests if the stored session.json is still valid and can access Blackboard.
    Returns True if valid, False if expired or missing.
    """
    if not SESSION_STORAGE_PATH.exists():
        logger.warning("No session file found at: %s", SESSION_STORAGE_PATH)
        return False

    logger.info("Verifying saved session state headlessly...")

    async with get_browser_context(headless=True, use_saved_state=True) as (_, _, context):
        page = await context.new_page()

        # Target the courses page directly or ultra root
        test_url = f"{config.BLACKBOARD_URL.rstrip('/')}/ultra/course"

        try:
            await page.goto(test_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)  # Wait for single-page app (SPA) router to redirect if expired
        except Exception as e:
            logger.error("Failed to navigate during session verification: %s", e)
            return False

        final_url = page.url
        logger.info("Session check final URL: %s", final_url)

        if await is_authenticated_url(final_url):
            logger.info("✅ Stored session is valid!")
            return True
        else:
            logger.warning("❌ Session has expired or redirected to login: %s", final_url)
            return False


if __name__ == "__main__":
    # Can be run directly as a CLI script: python -m blackboard.authentication
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
    
    action = sys.argv[1] if len(sys.argv) > 1 else "login"
    if action == "login":
        asyncio.run(login_interactive())
    elif action == "verify":
        valid = asyncio.run(verify_session())
        sys.exit(0 if valid else 1)
