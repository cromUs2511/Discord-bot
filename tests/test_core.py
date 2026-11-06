"""Regression tests for the Blackboard assistant's core logic.

Run with:
    python -m unittest discover -s tests -v
"""

import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite

from blackboard.activities import (
    Activity,
    extract_course_activities,
    extract_todo_activities,
    stable_activity_id,
)
from blackboard.announcements import Announcement, extract_course_announcements
from blackboard.courses import Course, parse_course_cards
from database.db import DatabaseManager, is_completed_status, is_genuine_deadline_change
import config
import monitoring.synchronizer as synchronizer_module
from monitoring.synchronizer import BlackboardSynchronizer
from utils.dates import is_date_only, parse_db_timestamp


class _FakeLocator:
    def __init__(self):
        self.first = self

    async def count(self):
        return 1

    async def click(self, force=False):
        return None


class _FakePage:
    def __init__(self, payload):
        self.payload = payload
        self.url = "https://example.blackboard.com/ultra/courses/_170077_1/outline"

    def locator(self, selector):
        return _FakeLocator()

    async def evaluate(self, script):
        return self.payload


class CourseParserTests(unittest.TestCase):
    def test_parses_courses_structurally_without_hardcoded_prefixes(self):
        raw = [
            {
                "text": "FEEDBACK AND CONTROL SYSTEMS\nCPE121-4_C1_2Q2627\nOpen\nInstructor: Jane Doe",
                "internalId": "_170077_1",
            },
            {
                "text": "Open course\nOperating Systems\nCS101-1_C1_2Q2627\nInstructor: John Smith",
                "internalId": "_170078_1",
            },
            {
                "text": "CLOSED COURSE\nData Structures\nCS102-1_C1_2Q2627\nMultiple Instructors",
                "internalId": "_170079_1",
            },
        ]
        courses = parse_course_cards(raw)
        self.assertEqual(len(courses), 3)

        by_id = {c.id: c for c in courses}
        feedback = by_id["CPE121-4_C1_2Q2627"]
        self.assertEqual(feedback.name, "FEEDBACK AND CONTROL SYSTEMS")
        self.assertEqual(feedback.instructor, "Jane Doe")
        self.assertEqual(feedback.status, "Open")
        self.assertEqual(feedback.term, "2Q2627")
        self.assertEqual(
            feedback.url,
            f"{config.BLACKBOARD_URL}/ultra/courses/_170077_1/outline",
        )

        # A title beginning with "Open" must not be mistaken for status metadata.
        operating = by_id["CS101-1_C1_2Q2627"]
        self.assertEqual(operating.name, "Operating Systems")
        self.assertEqual(operating.status, "Open")

        data_structures = by_id["CS102-1_C1_2Q2627"]
        self.assertEqual(data_structures.status, "Closed")
        self.assertEqual(data_structures.name, "Data Structures")

    def test_academic_term_filter(self):
        raw = [
            {
                "text": "Old Course\nOLD101-1_C1_1Q2526\nOpen\nInstructor: A",
                "internalId": "_1_1",
            },
            {
                "text": "Current Course\nNEW101-1_C1_2Q2627\nOpen\nInstructor: B",
                "internalId": "_2_1",
            },
        ]
        with patch.object(config, "ACADEMIC_TERM", "2Q2627"):
            courses = parse_course_cards(raw)
        self.assertEqual([c.id for c in courses], ["NEW101-1_C1_2Q2627"])


class DeadlineTests(unittest.TestCase):
    def test_date_only_and_timestamp_are_same_deadline(self):
        self.assertFalse(
            is_genuine_deadline_change("11/8/26 11:59 PM", "11/8/26")
        )

    def test_real_date_change_is_detected(self):
        self.assertTrue(is_genuine_deadline_change("11/8/26", "11/9/26"))

    def test_prefix_dates_are_not_false_negatives(self):
        self.assertTrue(is_genuine_deadline_change("1/1", "1/10"))

    def test_ongoing_transition_is_a_change(self):
        self.assertTrue(is_genuine_deadline_change("Ongoing", "11/8/26"))

    def test_completed_status_detection(self):
        self.assertTrue(is_completed_status("Graded"))
        self.assertTrue(is_completed_status("1 attempt submitted"))
        self.assertTrue(is_completed_status("Submitted late"))
        self.assertFalse(is_completed_status("Not submitted"))
        self.assertFalse(is_completed_status("Not graded"))


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DatabaseManager(self.db_path)
        await self.db.init_db()
        self.course = Course(
            id="CPE121-4_C1_2Q2627",
            name="FEEDBACK AND CONTROL SYSTEMS",
            internal_id="_170077_1",
        )
        await self.db.upsert_courses([self.course])

    async def asyncTearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    async def _add(self, **kwargs):
        defaults = dict(
            course_id=self.course.id,
            course_name=self.course.name,
            status="Not submitted",
        )
        defaults.update(kwargs)
        defaults.setdefault("id", stable_activity_id(defaults["course_id"], defaults["title"]))
        await self.db.process_activities([Activity(**defaults)])

    async def test_activities_are_sorted_chronologically(self):
        await self._add(title="Nov", due_date="11/30/27")
        await self._add(title="Feb", due_date="2/1/27")
        await self._add(title="ThisMonth", due_date="11/8/26")
        rows = await self.db.get_activities(course_id=self.course.id)
        self.assertEqual(
            [r["due_date"] for r in rows], ["11/8/26", "2/1/27", "11/30/27"]
        )

    async def test_overdue_handles_not_submitted_and_today(self):
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%m/%d/%y")
        today = datetime.now().strftime("%m/%d/%y")
        await self._add(title="Past", due_date=yesterday, status="Not submitted")
        await self._add(title="Today", due_date=today, status="Not submitted")
        await self._add(title="Done", due_date=yesterday, status="Submitted")

        overdue = await self.db.get_overdue_activities()
        self.assertEqual([r["title"] for r in overdue], ["Past"])

    async def test_channel_prefix_scope_excludes_lab(self):
        lab = Course(
            id="ECEA101-1L_C1_2Q2627",
            name="ELECTRONIC CIRCUITS LAB",
            internal_id="_2_1",
        )
        lecture = Course(
            id="ECEA101-1_C1_2Q2627",
            name="ELECTRONIC CIRCUITS",
            internal_id="_3_1",
        )
        await self.db.upsert_courses([lab, lecture])
        await self._add(
            course_id=lab.id,
            course_name=lab.name,
            title="Lab",
            due_date="11/8/26",
        )
        await self._add(
            course_id=lecture.id,
            course_name=lecture.name,
            title="Lecture",
            due_date="11/8/26",
        )

        lecture_rows = await self.db.get_activities(
            course_prefix="ECEA101-1", exclude_prefix="ECEA101-1L"
        )
        lab_rows = await self.db.get_activities(course_prefix="ECEA101-1L")
        self.assertEqual([r["title"] for r in lecture_rows], ["Lecture"])
        self.assertEqual([r["title"] for r in lab_rows], ["Lab"])

    async def test_completed_status_not_overwritten_by_todo(self):
        await self._add(title="Scored", due_date="11/8/26", status="Graded", points="10/10")
        await self._add(
            title="Scored",
            due_date="11/8/26 11:59 PM",
            status="Not submitted",
            points=None,
        )
        rows = await self.db.get_activities(course_id=self.course.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "Graded")
        self.assertEqual(rows[0]["points"], "10/10")

    async def test_reconciliation_deactivates_and_reactivates(self):
        await self._add(title="Kept", due_date="11/8/26")
        await self._add(title="Removed", due_date="11/9/26")
        rows = await self.db.get_activities(course_id=self.course.id)
        kept_id = next(r["id"] for r in rows if r["title"] == "Kept")

        removed = await self.db.deactivate_missing_activities(self.course.id, [kept_id])
        self.assertEqual(removed, 1)
        titles = [r["title"] for r in await self.db.get_activities(course_id=self.course.id)]
        self.assertEqual(titles, ["Kept"])

        # Re-discovering the item reports it as new and reactivates it.
        changes = await self.db.process_activities(
            [
                Activity(
                    id=stable_activity_id(self.course.id, "Removed"),
                    course_id=self.course.id,
                    course_name=self.course.name,
                    title="Removed",
                    due_date="11/9/26",
                    status="Not submitted",
                )
            ]
        )
        self.assertEqual(changes[0][1], "new")
        titles = [r["title"] for r in await self.db.get_activities(course_id=self.course.id)]
        self.assertIn("Removed", titles)

    async def test_old_schema_is_migrated(self):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        try:
            async with aiosqlite.connect(path) as db:
                await db.execute(
                    """
                    CREATE TABLE activities (
                        id TEXT PRIMARY KEY,
                        course_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        due_date TEXT,
                        status TEXT,
                        points TEXT,
                        url TEXT,
                        fingerprint TEXT NOT NULL,
                        first_detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                await db.commit()
            manager = DatabaseManager(path)
            await manager.init_db()
            async with aiosqlite.connect(path) as db:
                cursor = await db.execute("PRAGMA table_info(activities)")
                columns = {row[1] for row in await cursor.fetchall()}
            self.assertIn("is_active", columns)
        finally:
            os.remove(path)


class ExtractorTests(unittest.IsolatedAsyncioTestCase):
    async def test_points_and_due_date_are_parsed(self):
        page = _FakePage([["Coursework 1.1", "11/8/26", "Not submitted", "1/1"]])
        activities = await extract_course_activities(
            page, "CPE121-4_C1_2Q2627", "FEEDBACK AND CONTROL SYSTEMS"
        )
        self.assertEqual(activities[0].points, "1/1")
        self.assertEqual(activities[0].due_date, "11/8/26")

    async def test_todo_code_only_metadata_resolves_course(self):
        course = Course(
            id="CPE121-4_C1_2Q2627",
            name="FEEDBACK AND CONTROL SYSTEMS",
            internal_id="_170077_1",
        )
        page = _FakePage(
            [
                "Nov\n8\nCoursework 1.1\n"
                "Assignment · CPE121-4_C1_2Q2627\n"
                "Sun · 11/8/26 · 11:59 PM"
            ]
        )
        activities = await extract_todo_activities(page, [course])
        self.assertEqual(len(activities), 1)
        self.assertEqual(activities[0].course_id, course.id)
        self.assertEqual(activities[0].due_date, "11/8/26 11:59 PM")

    async def test_todo_date_only_deadline_is_parsed(self):
        course = Course(
            id="CPE121-4_C1_2Q2627",
            name="FEEDBACK AND CONTROL SYSTEMS",
            internal_id="_170077_1",
        )
        page = _FakePage(
            [
                "Nov\n8\nCoursework 1.2\n"
                "Assignment · CPE121-4_C1_2Q2627\n"
                "Sun · 11/9/26"
            ]
        )
        activities = await extract_todo_activities(page, [course])
        self.assertEqual(len(activities), 1)
        self.assertEqual(activities[0].due_date, "11/9/26")

    async def test_announcement_metadata_is_preserved(self):
        page = _FakePage(
            [
                {
                    "fullText": "Welcome\nHello class\nPosted by Jane Doe\nPosted on Nov 8, 2026",
                    "title": "Welcome",
                    "author": "Jane Doe",
                    "posted": "Nov 8, 2026",
                }
            ]
        )
        announcements = await extract_course_announcements(
            page, "CPE121-4_C1_2Q2627", "FEEDBACK AND CONTROL SYSTEMS"
        )
        self.assertEqual(len(announcements), 1)
        self.assertEqual(announcements[0].title, "Welcome")
        self.assertEqual(announcements[0].author, "Jane Doe")
        self.assertEqual(announcements[0].posted_date, "Nov 8, 2026")

    async def test_todo_and_gradebook_ids_match(self):
        course_id = "CPE121-4_C1_2Q2627"
        self.assertEqual(
            stable_activity_id(course_id, "Coursework 1.1"),
            stable_activity_id(course_id, "Coursework 1.1 Submission"),
        )

    async def test_announcement_extractor_has_no_page_fallback(self):
        page = _FakePage([])
        announcements = await extract_course_announcements(
            page, "CPE121-4_C1_2Q2627", "FEEDBACK AND CONTROL SYSTEMS"
        )
        self.assertEqual(announcements, [])


class _FakeBrowserContext:
    async def new_page(self):
        return _FakeSyncPage()


class _FakeSyncPage:
    def __init__(self):
        self.url = "https://example.blackboard.com/ultra/course"

    async def goto(self, url, **kwargs):
        self.url = url


class SyncPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DatabaseManager(self.db_path)
        await self.db.init_db()
        self.course = Course(
            id="CPE121-4_C1_2Q2627",
            name="FEEDBACK AND CONTROL SYSTEMS",
            internal_id="_170077_1",
            url="https://example.blackboard.com/ultra/courses/_170077_1/outline",
            status="Open",
        )

    async def asyncTearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    @asynccontextmanager
    async def _fake_browser(self, **kwargs):
        yield (None, None, _FakeBrowserContext())

    async def test_successful_sync_counts_and_persists(self):
        announcement = Announcement(
            id="ann1",
            course_id=self.course.id,
            course_name=self.course.name,
            title="Welcome",
            content="Hello",
        )
        gradebook_item = Activity(
            id=stable_activity_id(self.course.id, "Coursework 1.1"),
            course_id=self.course.id,
            course_name=self.course.name,
            title="Coursework 1.1",
            due_date="11/8/26",
            status="Not submitted",
        )
        todo_item = Activity(
            id=stable_activity_id(self.course.id, "Coursework 1.1"),
            course_id=self.course.id,
            course_name=self.course.name,
            title="Coursework 1.1",
            due_date="11/8/26 11:59 PM",
            status="Not submitted",
        )

        synchronizer = BlackboardSynchronizer(None)
        with patch.object(
            synchronizer_module, "SESSION_STORAGE_PATH", Path(__file__)
        ), patch.object(
            synchronizer_module, "db_manager", self.db
        ), patch.object(
            synchronizer_module,
            "extract_courses",
            AsyncMock(return_value=[self.course]),
        ), patch.object(
            synchronizer_module,
            "extract_todo_activities",
            AsyncMock(return_value=[todo_item]),
        ), patch.object(
            synchronizer_module,
            "extract_course_announcements",
            AsyncMock(return_value=[announcement]),
        ), patch.object(
            synchronizer_module,
            "extract_course_activities",
            AsyncMock(return_value=[gradebook_item]),
        ), patch.object(
            synchronizer_module, "get_browser_context", self._fake_browser
        ):
            result = await synchronizer.run_sync(notify=False)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["courses_discovered"], 1)
        self.assertEqual(result["new_announcements"], 1)
        self.assertEqual(result["new_activities"], 1)
        self.assertTrue(synchronizer.session_valid)
        self.assertIs(synchronizer.last_result, result)

        self.assertEqual(len(await self.db.get_recent_announcements()), 1)
        activities = await self.db.get_activities()
        self.assertEqual(len(activities), 1)
        self.assertEqual(activities[0]["due_date"], "11/8/26 11:59 PM")

    async def test_scrape_error_marks_sync_partial(self):
        synchronizer = BlackboardSynchronizer(None)
        with patch.object(
            synchronizer_module, "SESSION_STORAGE_PATH", Path(__file__)
        ), patch.object(
            synchronizer_module, "db_manager", self.db
        ), patch.object(
            synchronizer_module,
            "extract_courses",
            AsyncMock(return_value=[self.course]),
        ), patch.object(
            synchronizer_module,
            "extract_todo_activities",
            AsyncMock(return_value=[]),
        ), patch.object(
            synchronizer_module,
            "extract_course_announcements",
            AsyncMock(side_effect=RuntimeError("boom")),
        ), patch.object(
            synchronizer_module,
            "extract_course_activities",
            AsyncMock(return_value=[]),
        ), patch.object(
            synchronizer_module, "get_browser_context", self._fake_browser
        ):
            result = await synchronizer.run_sync(notify=False)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("boom", result["errors"][0])


class DateUtilityTests(unittest.TestCase):
    def test_date_only_detection(self):
        self.assertTrue(is_date_only("11/8/26"))
        self.assertTrue(is_date_only("2026-11-08"))
        self.assertFalse(is_date_only("11/8/26 11:59 PM"))

    def test_db_timestamp_is_converted_from_utc(self):
        with patch.object(config, "TIMEZONE", "Asia/Manila"):
            parsed = parse_db_timestamp("2026-11-08 04:00:00")
        self.assertIsNotNone(parsed)
        # Asia/Manila is UTC+8.
        self.assertEqual(parsed.hour, 12)


class SynchronizerTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_session_returns_error(self):
        synchronizer = BlackboardSynchronizer(None)
        missing = Path(tempfile.gettempdir()) / "definitely_missing_session.json"
        with patch.object(synchronizer_module, "SESSION_STORAGE_PATH", missing):
            result = await synchronizer.run_sync(notify=False)

        self.assertEqual(result["status"], "error")
        self.assertIn("session", result["message"].lower())
        self.assertFalse(synchronizer.session_valid)

    async def test_invalid_session_when_no_courses(self):
        synchronizer = BlackboardSynchronizer(None)
        with patch.object(
            synchronizer_module, "extract_courses", AsyncMock(return_value=[])
        ), patch.object(
            synchronizer_module, "verify_session", AsyncMock(return_value=False)
        ), patch.object(
            synchronizer_module,
            "SESSION_STORAGE_PATH",
            Path(__file__),
        ):
            result = await synchronizer.run_sync(notify=False)

        self.assertEqual(result["status"], "error")
        self.assertFalse(synchronizer.session_valid)

    async def test_skipped_when_already_running(self):
        synchronizer = BlackboardSynchronizer(None)
        synchronizer._is_syncing = True
        result = await synchronizer.run_sync(notify=False)
        self.assertEqual(result["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
