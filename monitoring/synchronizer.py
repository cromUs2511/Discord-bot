"""
monitoring/synchronizer.py — Blackboard Ultra Course, Announcement, and Activity Synchronizer.
"""

import asyncio
import logging
from typing import Optional

import discord

import config
from blackboard.authentication import is_authenticated_url, verify_session
from blackboard.browser import SESSION_STORAGE_PATH, get_browser_context
from blackboard.courses import extract_courses
from blackboard.announcements import extract_course_announcements
from blackboard.activities import extract_course_activities, extract_todo_activities
from database.db import db_manager
from discord_bot.notifications import (
    send_announcement_notification,
    send_activity_notification,
)

logger = logging.getLogger("monitoring.synchronizer")


class BlackboardSynchronizer:
    def __init__(self, discord_client: Optional[discord.Client] = None):
        self.client = discord_client
        self._is_syncing = False
        self.session_valid: Optional[bool] = None
        self.last_result: Optional[dict] = None
        self.sync_task: Optional[asyncio.Task] = None

    @staticmethod
    def _empty_summary(status: str = "ok", message: str = "") -> dict:
        return {
            "status": status,
            "message": message,
            "errors": [],
            "courses_discovered": 0,
            "new_announcements": 0,
            "updated_announcements": 0,
            "new_activities": 0,
            "deadline_changes": 0,
            "deactivated_announcements": 0,
            "deactivated_activities": 0,
        }

    async def run_sync(self, notify: bool = True) -> dict:
        """
        Executes a full synchronization pass:
        1. Discovers courses and their direct outline URLs.
        2. Scrapes announcements and activities for each open course.
        3. Identifies changes and updates SQLite database.
        4. Broadcasts Discord notifications if notify=True.

        Returns a summary whose ``status`` is one of: ok, partial, skipped, error.
        """
        if self._is_syncing:
            logger.warning("Sync is already running. Skipping overlapping run.")
            summary = self._empty_summary(
                status="skipped", message="Sync already in progress."
            )
            self.last_result = summary
            return summary

        self._is_syncing = True
        summary = self._empty_summary()
        errors: list[str] = []
        todo_ids_by_course: dict[str, set[str]] = {}

        try:
            if not SESSION_STORAGE_PATH.exists():
                summary["status"] = "error"
                summary["message"] = (
                    "No saved Blackboard session found. Run "
                    "`python -m blackboard.authentication login` first."
                )
                self.session_valid = False
                return summary

            logger.info("Starting Blackboard synchronization pass...")

            # Step 1: Discover enrolled courses
            courses = await extract_courses()
            summary["courses_discovered"] = len(courses)

            if not courses:
                # Distinguish "expired session" from "genuinely zero courses".
                valid = await verify_session()
                self.session_valid = valid
                if not valid:
                    summary["status"] = "error"
                    summary["message"] = (
                        "Blackboard returned no courses and the saved session is "
                        "expired or invalid. Re-run the interactive login."
                    )
                else:
                    summary["message"] = (
                        "No courses were found on the dashboard, but the session "
                        "is valid."
                    )
                return summary

            self.session_valid = True
            logger.info("Discovered %d course(s). Updating database...", len(courses))
            await db_manager.upsert_courses(courses)

            # Step 2: Open browser context to crawl open courses
            open_courses = [c for c in courses if (c.status or "").lower() == "open"]
            logger.info(
                "Found %d open course(s) to inspect for coursework and announcements.",
                len(open_courses),
            )

            if not open_courses:
                summary["message"] = "No open courses to scan."
                return summary

            async with get_browser_context(headless=True, use_saved_state=True) as (
                _,
                _,
                context,
            ):
                page = await context.new_page()

                # Step 2a: Extract 'To Do / Due Soon' items from the dashboard.
                try:
                    logger.info(
                        "Extracting 'To Do / Due Soon' panel from courses dashboard..."
                    )
                    await page.goto(
                        f"{config.BLACKBOARD_URL}/ultra/course",
                        wait_until="domcontentloaded",
                        timeout=30000,
                    )
                    await asyncio.sleep(4)
                    if not await is_authenticated_url(page.url):
                        raise RuntimeError(
                            "session appears to have expired during the crawl"
                        )
                    todo_acts = await extract_todo_activities(page, courses)
                    for todo in todo_acts:
                        todo_ids_by_course.setdefault(todo.course_id, set()).add(todo.id)
                    act_changes = await db_manager.process_activities(todo_acts)
                    for act, change_type, old_deadline in act_changes:
                        if change_type == "new":
                            summary["new_activities"] += 1
                            if notify and self.client:
                                await send_activity_notification(
                                    self.client, act, event_type="new"
                                )
                        elif change_type == "deadline_changed":
                            summary["deadline_changes"] += 1
                            if notify and self.client:
                                await send_activity_notification(
                                    self.client,
                                    act,
                                    event_type="deadline_changed",
                                    old_deadline=old_deadline,
                                )
                except Exception as e:
                    message = f"To Do panel: {e}"
                    logger.error("Error extracting To Do activities: %s", e)
                    errors.append(message)

                for course in open_courses:
                    logger.info("Processing course: [%s] %s...", course.id, course.name)

                    if not course.url:
                        errors.append(
                            f"{course.id}: no direct outline URL (skipped)"
                        )
                        logger.warning(
                            "No direct outline URL for course [%s]. Skipping.", course.id
                        )
                        continue

                    # 1. Extract Announcements
                    try:
                        await page.goto(
                            course.url, wait_until="domcontentloaded", timeout=30000
                        )
                        await asyncio.sleep(3)
                        if not await is_authenticated_url(page.url):
                            raise RuntimeError(
                                "session appears to have expired during the crawl"
                            )

                        announcements = await extract_course_announcements(
                            page, course.id, course.name
                        )
                        ann_changes = await db_manager.process_announcements(announcements)
                        for ann, change_type in ann_changes:
                            if change_type == "new":
                                summary["new_announcements"] += 1
                                if notify and self.client:
                                    await send_announcement_notification(
                                        self.client, ann, is_update=False
                                    )
                            elif change_type == "updated":
                                summary["updated_announcements"] += 1
                                if notify and self.client:
                                    await send_announcement_notification(
                                        self.client, ann, is_update=True
                                    )

                        # Reconcile only after a successful, non-empty scrape.
                        if announcements:
                            summary[
                                "deactivated_announcements"
                            ] += await db_manager.deactivate_missing_announcements(
                                course.id, [a.id for a in announcements]
                            )
                    except Exception as e:
                        message = f"{course.id} announcements: {e}"
                        logger.error(
                            "Error extracting announcements for [%s]: %s", course.id, e
                        )
                        errors.append(message)

                    # 2. Extract Activities & Deadlines
                    try:
                        await page.goto(
                            course.url, wait_until="domcontentloaded", timeout=30000
                        )
                        await asyncio.sleep(3)
                        if not await is_authenticated_url(page.url):
                            raise RuntimeError(
                                "session appears to have expired during the crawl"
                            )

                        activities = await extract_course_activities(
                            page, course.id, course.name
                        )
                        act_changes = await db_manager.process_activities(activities)
                        for act, change_type, old_deadline in act_changes:
                            if change_type == "new":
                                summary["new_activities"] += 1
                                if notify and self.client:
                                    await send_activity_notification(
                                        self.client, act, event_type="new"
                                    )
                            elif change_type == "deadline_changed":
                                summary["deadline_changes"] += 1
                                if notify and self.client:
                                    await send_activity_notification(
                                        self.client,
                                        act,
                                        event_type="deadline_changed",
                                        old_deadline=old_deadline,
                                    )

                        if activities:
                            active_ids = [a.id for a in activities]
                            active_ids.extend(todo_ids_by_course.get(course.id, set()))
                            summary[
                                "deactivated_activities"
                            ] += await db_manager.deactivate_missing_activities(
                                course.id, active_ids
                            )
                    except Exception as e:
                        message = f"{course.id} activities: {e}"
                        logger.error(
                            "Error extracting activities for [%s]: %s", course.id, e
                        )
                        errors.append(message)

            summary["errors"] = errors
            summary["status"] = "partial" if errors else "ok"
            if any("expired" in error.lower() for error in errors):
                self.session_valid = False
            if errors:
                summary["message"] = (
                    f"Completed with {len(errors)} error(s); see logs for details."
                )
            logger.info("Synchronization completed. Summary: %s", summary)

        except Exception as e:
            logger.error("Synchronization pass failed: %s", e, exc_info=True)
            summary["status"] = "error"
            summary["message"] = f"Synchronization failed: {e}"
            errors.append(str(e))
            summary["errors"] = errors
        finally:
            self._is_syncing = False
            self.last_result = summary

        return summary
