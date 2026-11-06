"""
monitoring/synchronizer.py — Blackboard Ultra Course, Announcement, and Activity Synchronizer.
"""

import asyncio
import logging
from typing import Optional
import discord

import config
from blackboard.browser import get_browser_context
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

    async def run_sync(self, notify: bool = True) -> dict:
        """
        Executes a full synchronization pass:
        1. Discovers courses and their direct outline URLs.
        2. Scrapes announcements and activities for each open course.
        3. Identifies changes and updates SQLite database.
        4. Broadcasts Discord notifications if notify=True.
        """
        if self._is_syncing:
            logger.warning("Sync is already running. Skipping overlapping run.")
            return {"status": "skipped", "message": "Sync already in progress."}

        self._is_syncing = True
        summary = {
            "courses_discovered": 0,
            "new_announcements": 0,
            "updated_announcements": 0,
            "new_activities": 0,
            "deadline_changes": 0,
        }

        try:
            logger.info("Starting Blackboard synchronization pass...")
            
            # Step 1: Discover enrolled courses
            courses = await extract_courses()
            summary["courses_discovered"] = len(courses)
            logger.info("Discovered %d course(s). Updating database...", len(courses))
            await db_manager.upsert_courses(courses)

            # Step 2: Open browser context to crawl open courses
            open_courses = [c for c in courses if c.status == "Open"]
            logger.info("Found %d open course(s) to inspect for coursework and announcements.", len(open_courses))

            if not open_courses:
                logger.info("No open courses to scan.")
                return summary

            async with get_browser_context(headless=True, use_saved_state=True) as (_, _, context):
                page = await context.new_page()

                # Step 1.5: Extract 'To Do / Due Soon' items directly from the Courses page
                try:
                    logger.info("Extracting 'To Do / Due Soon' panel from courses dashboard...")
                    await page.goto(f"{config.BLACKBOARD_URL.rstrip('/')}/ultra/course", wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(4)
                    todo_acts = await extract_todo_activities(page, courses)
                    act_changes = await db_manager.process_activities(todo_acts)
                    for act, change_type, old_deadline in act_changes:
                        if change_type == "new":
                            summary["new_activities"] += 1
                            if notify and self.client:
                                await send_activity_notification(self.client, act, event_type="new")
                        elif change_type == "deadline_changed":
                            summary["deadline_changes"] += 1
                            if notify and self.client:
                                await send_activity_notification(self.client, act, event_type="deadline_changed", old_deadline=old_deadline)
                except Exception as e:
                    logger.error("Error extracting To Do activities: %s", e)

                for course in open_courses:
                    logger.info("Processing course: [%s] %s...", course.id, course.name)
                    
                    if not course.url:
                        logger.warning("No direct outline URL for course [%s]. Skipping.", course.id)
                        continue

                    # 1. Extract Announcements
                    try:
                        await page.goto(course.url, wait_until="domcontentloaded", timeout=30000)
                        await asyncio.sleep(3)
                        announcements = await extract_course_announcements(page, course.id, course.name)
                        ann_changes = await db_manager.process_announcements(announcements)
                        for ann, change_type in ann_changes:
                            if change_type == "new":
                                summary["new_announcements"] += 1
                                if notify and self.client:
                                    await send_announcement_notification(self.client, ann, is_update=False)
                            elif change_type == "updated":
                                summary["updated_announcements"] += 1
                                if notify and self.client:
                                    await send_announcement_notification(self.client, ann, is_update=True)
                    except Exception as e:
                        logger.error("Error extracting announcements for [%s]: %s", course.id, e)

                    # 2. Extract Activities & Deadlines
                    try:
                        await page.goto(course.url, wait_until="domcontentloaded", timeout=30000)
                        await asyncio.sleep(3)
                        activities = await extract_course_activities(page, course.id, course.name)
                        act_changes = await db_manager.process_activities(activities)
                        for act, change_type, old_deadline in act_changes:
                            if change_type == "new":
                                summary["new_activities"] += 1
                                if notify and self.client:
                                    await send_activity_notification(self.client, act, event_type="new")
                            elif change_type == "deadline_changed":
                                summary["deadline_changes"] += 1
                                if notify and self.client:
                                    await send_activity_notification(self.client, act, event_type="deadline_changed", old_deadline=old_deadline)
                    except Exception as e:
                        logger.error("Error extracting activities for [%s]: %s", course.id, e)

            logger.info("Synchronization completed successfully! Summary: %s", summary)

        except Exception as e:
            logger.error("Synchronization pass failed: %s", e, exc_info=True)
        finally:
            self._is_syncing = False

        return summary
