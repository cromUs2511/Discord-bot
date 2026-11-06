"""
database/db.py — Asynchronous database layer using aiosqlite.
"""

import aiosqlite
import logging
from typing import List, Tuple, Optional, Dict, Any

import config
from database.models import SCHEMA_SQL
from blackboard.courses import Course
from blackboard.announcements import Announcement
from blackboard.activities import Activity

logger = logging.getLogger("database")


def is_genuine_deadline_change(old_due: Optional[str], new_due: Optional[str]) -> bool:
    """
    Checks if a deadline change is genuine rather than just a formatting difference
    (e.g. '11/8/26' vs '11/8/26 11:59 PM').
    """
    if not old_due or not new_due:
        return old_due != new_due

    o = old_due.strip()
    n = new_due.strip()

    if o == n:
        return False

    # If one is just the prefix date of the other (same date, one has time), not a change!
    if o.startswith(n) or n.startswith(o):
        return False

    from datetime import datetime
    def parse_dt(s):
        for fmt in ("%m/%d/%y %I:%M %p", "%m/%d/%Y %I:%M %p", "%m/%d/%y", "%m/%d/%Y"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    dt_old = parse_dt(o)
    dt_new = parse_dt(n)

    if dt_old and dt_new:
        # If one lacks time, compare date only
        if ":" not in o or ":" not in n:
            return dt_old.date() != dt_new.date()
        return dt_old != dt_new

    return o != n


class DatabaseManager:
    def __init__(self, db_path=config.DATABASE_PATH):
        self.db_path = str(db_path)

    async def init_db(self):
        """Initializes tables in the SQLite database."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA_SQL)
            await db.commit()
        logger.info("Database initialized at %s", self.db_path)

    async def upsert_courses(self, courses: List[Course]):
        """Inserts or updates courses."""
        async with aiosqlite.connect(self.db_path) as db:
            for c in courses:
                await db.execute(
                    """
                    INSERT INTO courses (id, name, instructor, term, status, url, last_synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,
                        instructor=excluded.instructor,
                        status=excluded.status,
                        url=COALESCE(excluded.url, courses.url),
                        last_synced_at=CURRENT_TIMESTAMP
                    """,
                    (c.id, c.name, c.instructor, c.term, c.status, c.url),
                )
            await db.commit()

    async def get_all_courses(self) -> List[Dict[str, Any]]:
        """Returns all enrolled courses."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM courses ORDER BY status DESC, name ASC")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def process_announcements(self, announcements: List[Announcement]) -> List[Tuple[Announcement, str]]:
        """
        Processes detected announcements.
        Returns a list of tuples: (Announcement, event_type) where event_type is 'new' or 'updated'.
        Guarantees no false duplicate notifications.
        """
        changes: List[Tuple[Announcement, str]] = []

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            for ann in announcements:
                cursor = await db.execute("SELECT fingerprint, content FROM announcements WHERE id = ?", (ann.id,))
                row = await cursor.fetchone()

                if row is None:
                    # Brand new announcement
                    await db.execute(
                        """
                        INSERT INTO announcements (id, course_id, title, content, posted_date, author, url, fingerprint)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (ann.id, ann.course_id, ann.title, ann.content, ann.posted_date, ann.author, ann.url, ann.fingerprint),
                    )
                    changes.append((ann, "new"))
                elif row["fingerprint"] != ann.fingerprint:
                    # Content updated
                    await db.execute(
                        """
                        UPDATE announcements SET
                            content = ?,
                            fingerprint = ?,
                            last_detected_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (ann.content, ann.fingerprint, ann.id),
                    )
                    changes.append((ann, "updated"))
                else:
                    # Unchanged, touch timestamp
                    await db.execute("UPDATE announcements SET last_detected_at = CURRENT_TIMESTAMP WHERE id = ?", (ann.id,))

            await db.commit()

        return changes

    async def process_activities(self, activities: List[Activity]) -> List[Tuple[Activity, str, Optional[str]]]:
        """
        Processes detected activities.
        Returns a list of tuples: (Activity, event_type, old_deadline)
        where event_type is 'new' or 'deadline_changed'.
        """
        changes: List[Tuple[Activity, str, Optional[str]]] = []

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            for act in activities:
                cursor = await db.execute("SELECT due_date, status, fingerprint FROM activities WHERE id = ?", (act.id,))
                row = await cursor.fetchone()

                if row is None:
                    # Brand new activity
                    await db.execute(
                        """
                        INSERT INTO activities (id, course_id, title, due_date, status, points, url, fingerprint)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (act.id, act.course_id, act.title, act.due_date, act.status, act.points, act.url, act.fingerprint),
                    )
                    changes.append((act, "new", None))
                else:
                    old_due = row["due_date"]
                    # Retain richer timestamp if one source includes time and the other is date-only
                    resolved_due = act.due_date
                    if old_due and act.due_date:
                        if ":" in old_due and ":" not in act.due_date and old_due.startswith(act.due_date):
                            resolved_due = old_due  # Keep existing time!

                    if is_genuine_deadline_change(old_due, act.due_date):
                        # Genuinely changed calendar date!
                        await db.execute(
                            """
                            UPDATE activities SET
                                due_date = ?,
                                status = ?,
                                points = ?,
                                fingerprint = ?,
                                last_detected_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                            """,
                            (resolved_due, act.status, act.points, act.fingerprint, act.id),
                        )
                        changes.append((act, "deadline_changed", old_due))
                    elif row["fingerprint"] != act.fingerprint or row["due_date"] != resolved_due:
                        # Status/points updated or time enriched quietly without triggering a false alert
                        await db.execute(
                            """
                            UPDATE activities SET
                                due_date = ?,
                                status = ?,
                                points = ?,
                                fingerprint = ?,
                                last_detected_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                            """,
                            (resolved_due, act.status, act.points, act.fingerprint, act.id),
                        )

            await db.commit()

        return changes

    async def get_recent_announcements(self, course_id: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """Queries announcements, optionally filtered by course."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if course_id:
                cursor = await db.execute(
                    """
                    SELECT a.*, c.name as course_name FROM announcements a
                    JOIN courses c ON a.course_id = c.id
                    WHERE a.course_id = ?
                    ORDER BY a.first_detected_at DESC LIMIT ?
                    """,
                    (course_id, limit),
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT a.*, c.name as course_name FROM announcements a
                    JOIN courses c ON a.course_id = c.id
                    ORDER BY a.first_detected_at DESC LIMIT ?
                    """,
                    (limit,),
                )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_activities(self, course_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Queries activities, optionally filtered by course."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if course_id:
                cursor = await db.execute(
                    """
                    SELECT act.*, c.name as course_name FROM activities act
                    JOIN courses c ON act.course_id = c.id
                    WHERE act.course_id = ?
                    ORDER BY act.due_date ASC, act.title ASC
                    """,
                    (course_id,),
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT act.*, c.name as course_name FROM activities act
                    JOIN courses c ON act.course_id = c.id
                    ORDER BY act.due_date ASC, act.title ASC
                    """
                )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_pending_activities(self) -> List[Dict[str, Any]]:
        """Returns activities that have NOT been submitted or graded."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT act.*, c.name as course_name FROM activities act
                JOIN courses c ON act.course_id = c.id
                WHERE (
                    act.status IS NULL 
                    OR (
                        LOWER(act.status) NOT IN ('submitted', 'graded')
                        AND LOWER(act.status) NOT LIKE '%attempt submitted%'
                        AND LOWER(act.status) NOT LIKE '%1 attempt submitted%'
                    )
                )
                ORDER BY act.due_date ASC, act.title ASC
                """
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_overdue_activities(self) -> List[Dict[str, Any]]:
        """Returns activities whose status contains 'past due' or 'overdue',
        or whose due_date is in the past and status is not submitted/graded."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT act.*, c.name as course_name FROM activities act
                JOIN courses c ON act.course_id = c.id
                ORDER BY act.due_date ASC
                """
            )
            rows = await cursor.fetchall()
            items = [dict(r) for r in rows]

        from datetime import datetime
        now = datetime.now()
        overdue = []
        for a in items:
            status = (a.get("status") or "").lower()
            if "submitted" in status or "graded" in status:
                continue

            if "past due" in status or "overdue" in status:
                overdue.append(a)
                continue

            dd = a.get("due_date")
            if dd and dd.lower() != "ongoing":
                for fmt in ("%m/%d/%y %I:%M %p", "%m/%d/%Y %I:%M %p", "%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d"):
                    try:
                        due_dt = datetime.strptime(dd.strip(), fmt)
                        if due_dt < now:
                            overdue.append(a)
                        break
                    except ValueError:
                        continue

        return overdue

    async def get_course_by_id(self, course_id: str) -> Optional[Dict[str, Any]]:
        """Returns a single course by its ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM courses WHERE id = ?", (course_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def search_courses(self, query: str) -> List[Dict[str, Any]]:
        """Searches courses by name or ID (for autocomplete)."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM courses
                WHERE LOWER(id) LIKE ? OR LOWER(name) LIKE ?
                ORDER BY status DESC, name ASC
                LIMIT 25
                """,
                (f"%{query.lower()}%", f"%{query.lower()}%"),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


db_manager = DatabaseManager()
