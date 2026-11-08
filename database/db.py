"""
database/db.py — Asynchronous database layer using aiosqlite.
"""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import aiosqlite

import config
from database.models import INDEXES_SQL, SCHEMA_SQL
from blackboard.courses import Course
from blackboard.announcements import Announcement
from blackboard.activities import Activity, activity_fingerprint
from utils.dates import (
    convert_utc_due_to_local,
    due_sort_datetime,
    is_date_only,
    now_local,
    parse_due_datetime,
)

logger = logging.getLogger("database")


# ---------------------------------------------------------------------------
# Status / date helpers
# ---------------------------------------------------------------------------
def is_completed_status(status: Optional[str]) -> bool:
    """
    True only for statuses that mean the student is finished.

    Important: "Not submitted" must return False. Substring checks such as
    ``"submitted" in status`` incorrectly match "not submitted".
    """
    normalized = " ".join((status or "").lower().split())
    if not normalized:
        return False
    if normalized in {"submitted", "graded"}:
        return True
    if normalized.startswith("submitted") or normalized.startswith("graded"):
        return True
    if "attempt submitted" in normalized:
        return True
    return False


def _date_prefix_matches(shorter: str, longer: str) -> bool:
    """True when one value is a date-only prefix of the other with a time suffix."""
    if not longer.startswith(shorter):
        return False
    remainder = longer[len(shorter):]
    return not remainder or remainder[0].isspace()


def is_genuine_deadline_change(old_due: Optional[str], new_due: Optional[str]) -> bool:
    """
    Checks if a deadline change is genuine rather than just a formatting
    difference (e.g. '11/8/26' vs '11/8/26 11:59 PM').
    """
    if not old_due or not new_due:
        return old_due != new_due

    o = " ".join(str(old_due).strip().split())
    n = " ".join(str(new_due).strip().split())

    if o == n:
        return False

    # If the only difference is that one value carries a time suffix, ignore it.
    if _date_prefix_matches(o, n) or _date_prefix_matches(n, o):
        return False

    dt_old = parse_due_datetime(o)
    dt_new = parse_due_datetime(n)

    if dt_old and dt_new:
        # Compare dates only when either value lacks a time component.
        if is_date_only(o) or is_date_only(n):
            return dt_old.date() != dt_new.date()
        return dt_old != dt_new

    return o != n


class DatabaseManager:
    def __init__(self, db_path=config.DATABASE_PATH):
        self.db_path = str(db_path)

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------
    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        db = await aiosqlite.connect(self.db_path)
        db.row_factory = aiosqlite.Row
        try:
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("PRAGMA foreign_keys=ON")
            yield db
        finally:
            await db.close()

    async def init_db(self):
        """Initializes tables in the SQLite database and applies migrations."""
        async with self._connect() as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(SCHEMA_SQL)
            await self._migrate(db)
            await db.executescript(INDEXES_SQL)
            await db.commit()
        logger.info("Database initialized at %s", self.db_path)

    async def _migrate(self, db: aiosqlite.Connection):
        """Add columns introduced after the first release without losing data."""
        for table in ("announcements", "activities"):
            exists = await db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            if not await exists.fetchone():
                continue
            cursor = await db.execute(f"PRAGMA table_info({table})")
            columns = {row["name"] for row in await cursor.fetchall()}
            if "is_active" not in columns:
                await db.execute(
                    f"ALTER TABLE {table} ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
                )
                logger.info("Migrated %s: added is_active column", table)

        # Timezone correction: legacy rows scraped in UTC (e.g. Manila
        # 11:59 PM stored as '3:59 PM') are converted to Asia/Manila here.
        # This must run with proper UTC->local arithmetic (+8h, with
        # date-rollover) for EVERY datetime, and must update the stored
        # fingerprint alongside due_date. The previous string REPLACE only
        # fixed exactly '3:59 PM', and because fingerprints were left
        # stale the next sync treated the corrected row as a genuine
        # deadline change and reverted it back to UTC — hence the persistent
        # 3:59 PM display. Rows ending '11:59 PM' are skipped because the
        # earlier REPLACE already corrected those (converting them again
        # would push them to 7:59 AM next day). The whole correction runs
        # exactly once (guarded by schema_migrations) so later restarts
        # never double-shift already-local rows.
        try:
            cursor = await db.execute(
                "SELECT 1 FROM schema_migrations WHERE name = ?", ("utc_due_to_mnl_v1",)
            )
            if not await cursor.fetchone():
                cursor = await db.execute(
                    "SELECT id, course_id, title, due_date, status FROM activities "
                    "WHERE due_date IS NOT NULL AND due_date LIKE '%:%' "
                    "AND LOWER(due_date) != 'ongoing'"
                )
                legacy_rows = await cursor.fetchall()
                for row in legacy_rows:
                    old_value = row["due_date"]
                    if (old_value or "").strip().endswith("11:59 PM"):
                        continue
                    converted = convert_utc_due_to_local(old_value)
                    if converted and converted != old_value:
                        await db.execute(
                            "UPDATE activities SET due_date = ?, fingerprint = ? WHERE id = ?",
                            (
                                converted,
                                activity_fingerprint(
                                    row["course_id"], row["title"], converted, row["status"]
                                ),
                                row["id"],
                            ),
                        )
                        logger.info(
                            "Timezone migration: %s '%s' -> '%s'",
                            row["title"], old_value, converted,
                        )
                await db.execute(
                    "INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)",
                    ("utc_due_to_mnl_v1",),
                )
        except Exception:
            logger.exception("Timezone due-date migration failed (ignored)")

    # ------------------------------------------------------------------
    # Courses
    # ------------------------------------------------------------------
    async def upsert_courses(self, courses: List[Course]):
        """Inserts or updates courses."""
        async with self._connect() as db:
            for c in courses:
                await db.execute(
                    """
                    INSERT INTO courses (id, name, instructor, term, status, url, last_synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,
                        instructor=excluded.instructor,
                        term=COALESCE(excluded.term, courses.term),
                        status=excluded.status,
                        url=COALESCE(excluded.url, courses.url),
                        last_synced_at=CURRENT_TIMESTAMP
                    """,
                    (c.id, c.name, c.instructor, c.term, c.status, c.url),
                )
            await db.commit()

    async def get_all_courses(self) -> List[Dict[str, Any]]:
        """Returns all enrolled courses."""
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT * FROM courses ORDER BY status DESC, name ASC"
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_course_by_id(self, course_id: str) -> Optional[Dict[str, Any]]:
        """Returns a single course by its ID."""
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT * FROM courses WHERE id = ?", (course_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def search_courses(self, query: str) -> List[Dict[str, Any]]:
        """Searches courses by name or ID (for autocomplete)."""
        async with self._connect() as db:
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

    # ------------------------------------------------------------------
    # Change detection
    # ------------------------------------------------------------------
    async def process_announcements(
        self, announcements: List[Announcement]
    ) -> List[Tuple[Announcement, str]]:
        """
        Processes detected announcements.

        Returns (Announcement, event_type) where event_type is 'new' or
        'updated'. Reactivated (previously pruned) items are reported as 'new'.
        """
        changes: List[Tuple[Announcement, str]] = []

        async with self._connect() as db:
            for ann in announcements:
                cursor = await db.execute(
                    "SELECT fingerprint, is_active FROM announcements WHERE id = ?",
                    (ann.id,),
                )
                row = await cursor.fetchone()

                if row is None:
                    await db.execute(
                        """
                        INSERT INTO announcements
                            (id, course_id, title, content, posted_date, author,
                             url, fingerprint, is_active)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            ann.id,
                            ann.course_id,
                            ann.title,
                            ann.content,
                            ann.posted_date,
                            ann.author,
                            ann.url,
                            ann.fingerprint,
                        ),
                    )
                    changes.append((ann, "new"))
                elif not row["is_active"]:
                    # Item reappeared after being pruned/deleted.
                    await db.execute(
                        """
                        UPDATE announcements SET
                            course_id = ?,
                            title = ?,
                            content = ?,
                            posted_date = ?,
                            author = ?,
                            url = ?,
                            fingerprint = ?,
                            is_active = 1,
                            last_detected_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (
                            ann.course_id,
                            ann.title,
                            ann.content,
                            ann.posted_date,
                            ann.author,
                            ann.url,
                            ann.fingerprint,
                            ann.id,
                        ),
                    )
                    changes.append((ann, "new"))
                elif row["fingerprint"] != ann.fingerprint:
                    cursor_row = await db.execute(
                        "SELECT title, content FROM announcements WHERE id = ?",
                        (ann.id,),
                    )
                    existing = await cursor_row.fetchone()
                    is_genuine = True
                    if existing:
                        from blackboard.announcements import clean_announcement_content
                        old_body = clean_announcement_content(existing["content"])
                        new_body = clean_announcement_content(ann.content)
                        if old_body == new_body and (existing["title"] or "").strip() == (ann.title or "").strip():
                            is_genuine = False

                    await db.execute(
                        """
                        UPDATE announcements SET
                            content = ?,
                            posted_date = COALESCE(?, posted_date),
                            author = COALESCE(?, author),
                            fingerprint = ?,
                            is_active = 1,
                            last_detected_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (
                            ann.content,
                            ann.posted_date,
                            ann.author,
                            ann.fingerprint,
                            ann.id,
                        ),
                    )
                    if is_genuine:
                        changes.append((ann, "updated"))
                    else:
                        logger.info(
                            "Ignored non-genuine announcement update (timestamp/formatting) for: %s",
                            ann.title,
                        )
                else:
                    await db.execute(
                        """
                        UPDATE announcements SET
                            is_active = 1,
                            last_detected_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (ann.id,),
                    )

            await db.commit()

        return changes

    async def process_activities(
        self, activities: List[Activity]
    ) -> List[Tuple[Activity, str, Optional[str]]]:
        """
        Processes detected activities.

        Returns (Activity, event_type, old_deadline) where event_type is
        'new' or 'deadline_changed'. Status/points updates are persisted
        silently so routine grade changes do not spam Discord.
        """
        changes: List[Tuple[Activity, str, Optional[str]]] = []

        async with self._connect() as db:
            for act in activities:
                cursor = await db.execute(
                    """
                    SELECT due_date, status, points, fingerprint, is_active
                    FROM activities WHERE id = ?
                    """,
                    (act.id,),
                )
                row = await cursor.fetchone()

                if row is None:
                    await db.execute(
                        """
                        INSERT INTO activities
                            (id, course_id, title, due_date, status, points,
                             url, fingerprint, is_active)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            act.id,
                            act.course_id,
                            act.title,
                            act.due_date,
                            act.status,
                            act.points,
                            act.url,
                            activity_fingerprint(
                                act.course_id, act.title, act.due_date, act.status
                            ),
                        ),
                    )
                    changes.append((act, "new", None))
                    continue

                old_due = row["due_date"]
                old_status = row["status"]
                old_points = row["points"]

                # A partial source (e.g. the To Do panel) must not overwrite a
                # completed status or an existing score with "Not submitted"/None.
                new_status = act.status
                if new_status is None:
                    new_status = old_status
                elif is_completed_status(old_status) and not is_completed_status(new_status):
                    new_status = old_status
                new_points = act.points if act.points is not None else old_points

                # Retain a richer timestamp if one source includes time and the
                # other is date-only. A missing due date from a partial source
                # must not erase a known deadline.
                resolved_due = act.due_date if act.due_date is not None else old_due
                if old_due and act.due_date:
                    if (
                        ":" in old_due
                        and ":" not in act.due_date
                        and old_due.startswith(act.due_date)
                    ):
                        resolved_due = old_due

                stored_fingerprint = activity_fingerprint(
                    act.course_id, act.title, resolved_due, new_status
                )

                if not row["is_active"]:
                    await db.execute(
                        """
                        UPDATE activities SET
                            course_id = ?,
                            title = ?,
                            due_date = ?,
                            status = ?,
                            points = ?,
                            url = ?,
                            fingerprint = ?,
                            is_active = 1,
                            last_detected_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (
                            act.course_id,
                            act.title,
                            resolved_due,
                            new_status,
                            new_points,
                            act.url,
                            stored_fingerprint,
                            act.id,
                        ),
                    )
                    act.due_date = resolved_due
                    act.status = new_status
                    act.points = new_points
                    changes.append((act, "new", None))
                elif is_genuine_deadline_change(old_due, act.due_date):
                    await db.execute(
                        """
                        UPDATE activities SET
                            due_date = ?,
                            status = ?,
                            points = ?,
                            fingerprint = ?,
                            is_active = 1,
                            last_detected_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (
                            resolved_due,
                            new_status,
                            new_points,
                            stored_fingerprint,
                            act.id,
                        ),
                    )
                    act.due_date = resolved_due
                    act.status = new_status
                    act.points = new_points
                    changes.append((act, "deadline_changed", old_due))
                elif row["fingerprint"] != stored_fingerprint or old_points != new_points:
                    await db.execute(
                        """
                        UPDATE activities SET
                            due_date = ?,
                            status = ?,
                            points = ?,
                            fingerprint = ?,
                            is_active = 1,
                            last_detected_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (
                            resolved_due,
                            new_status,
                            new_points,
                            stored_fingerprint,
                            act.id,
                        ),
                    )
                else:
                    await db.execute(
                        """
                        UPDATE activities SET
                            is_active = 1,
                            last_detected_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (act.id,),
                    )

            await db.commit()

        return changes

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------
    async def deactivate_missing_announcements(
        self, course_id: str, active_ids: List[str]
    ) -> int:
        """Marks announcements no longer returned by a successful scrape as inactive."""
        if not active_ids:
            return 0
        placeholders = ",".join("?" for _ in active_ids)
        async with self._connect() as db:
            cursor = await db.execute(
                f"""
                UPDATE announcements
                SET is_active = 0
                WHERE course_id = ? AND is_active = 1
                  AND id NOT IN ({placeholders})
                """,
                (course_id, *active_ids),
            )
            await db.commit()
            return cursor.rowcount or 0

    async def deactivate_missing_activities(
        self, course_id: str, active_ids: List[str]
    ) -> int:
        """Marks activities no longer returned by a successful scrape as inactive."""
        if not active_ids:
            return 0
        placeholders = ",".join("?" for _ in active_ids)
        async with self._connect() as db:
            cursor = await db.execute(
                f"""
                UPDATE activities
                SET is_active = 0
                WHERE course_id = ? AND is_active = 1
                  AND id NOT IN ({placeholders})
                """,
                (course_id, *active_ids),
            )
            await db.commit()
            return cursor.rowcount or 0

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    @staticmethod
    def _scope_clause(
        alias: str,
        course_id: Optional[str],
        course_prefix: Optional[str],
        exclude_prefix: Optional[str],
    ) -> tuple[List[str], List[Any]]:
        clauses: List[str] = []
        params: List[Any] = []

        if course_id:
            clauses.append(f"{alias}.course_id = ?")
            params.append(course_id)
        elif course_prefix:
            clauses.append(f"{alias}.course_id LIKE ?")
            params.append(f"{course_prefix}%")
            if exclude_prefix:
                clauses.append(f"{alias}.course_id NOT LIKE ?")
                params.append(f"{exclude_prefix}%")

        return clauses, params

    async def get_recent_announcements(
        self,
        course_id: Optional[str] = None,
        limit: int = 10,
        course_prefix: Optional[str] = None,
        exclude_prefix: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Queries active announcements, optionally filtered by course or channel scope."""
        clauses = ["COALESCE(a.is_active, 1) = 1"]
        scope_clauses, params = self._scope_clause(
            "a", course_id, course_prefix, exclude_prefix
        )
        clauses.extend(scope_clauses)

        sql = f"""
            SELECT a.*, c.name AS course_name
            FROM announcements a
            JOIN courses c ON a.course_id = c.id
            WHERE {' AND '.join(clauses)}
        """

        async with self._connect() as db:
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            items = [dict(r) for r in rows]

        def announcement_date(item: Dict[str, Any]) -> datetime:
            posted_date = item.get("posted_date")
            posted_datetime = parse_due_datetime(posted_date)
            if posted_datetime is None and posted_date:
                # Blackboard may separate a posted date and time with a comma.
                normalized_date = re.sub(
                    r",(?=\s+\d{1,2}:\d{2}\s*[AP]M$)",
                    "",
                    posted_date,
                    flags=re.IGNORECASE,
                )
                posted_datetime = parse_due_datetime(normalized_date)

            if posted_datetime is not None:
                return posted_datetime
            return parse_due_datetime(item.get("first_detected_at")) or datetime.min

        items.sort(key=announcement_date, reverse=True)
        return items[:limit]

    async def get_activities(
        self,
        course_id: Optional[str] = None,
        course_prefix: Optional[str] = None,
        exclude_prefix: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Queries active activities, optionally filtered by course or channel scope."""
        clauses = ["COALESCE(act.is_active, 1) = 1"]
        scope_clauses, params = self._scope_clause(
            "act", course_id, course_prefix, exclude_prefix
        )
        clauses.extend(scope_clauses)

        sql = f"""
            SELECT act.*, c.name AS course_name
            FROM activities act
            JOIN courses c ON act.course_id = c.id
            WHERE {' AND '.join(clauses)}
        """
        async with self._connect() as db:
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            items = [dict(r) for r in rows]

        items.sort(
            key=lambda item: (
                due_sort_datetime(item.get("due_date")),
                (item.get("title") or "").lower(),
            )
        )
        return items

    async def get_pending_activities(
        self,
        course_prefix: Optional[str] = None,
        exclude_prefix: Optional[str] = None,
        course_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Returns activities that have NOT been submitted or graded."""
        clauses = [
            "COALESCE(act.is_active, 1) = 1",
            """(
                act.status IS NULL
                OR (
                    LOWER(TRIM(act.status)) NOT IN ('submitted', 'graded')
                    AND LOWER(act.status) NOT LIKE 'submitted%'
                    AND LOWER(act.status) NOT LIKE 'graded%'
                    AND LOWER(act.status) NOT LIKE '%attempt submitted%'
                )
            )""",
        ]
        scope_clauses, params = self._scope_clause(
            "act", course_id, course_prefix, exclude_prefix
        )
        clauses.extend(scope_clauses)

        sql = f"""
            SELECT act.*, c.name AS course_name
            FROM activities act
            JOIN courses c ON act.course_id = c.id
            WHERE {' AND '.join(clauses)}
        """
        async with self._connect() as db:
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            items = [dict(r) for r in rows]

        items.sort(
            key=lambda item: (
                due_sort_datetime(item.get("due_date")),
                (item.get("title") or "").lower(),
            )
        )
        return items

    async def get_overdue_activities(self) -> List[Dict[str, Any]]:
        """Returns active, uncompleted activities whose deadline has passed."""
        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT act.*, c.name AS course_name
                FROM activities act
                JOIN courses c ON act.course_id = c.id
                WHERE COALESCE(act.is_active, 1) = 1
                """
            )
            rows = await cursor.fetchall()
            items = [dict(r) for r in rows]

        now = now_local()
        overdue: List[Dict[str, Any]] = []
        for item in items:
            status = (item.get("status") or "").lower()
            if is_completed_status(status):
                continue

            if "past due" in status or "overdue" in status:
                overdue.append(item)
                continue

            dd = item.get("due_date")
            if not dd or dd.lower() == "ongoing":
                continue

            due_dt = parse_due_datetime(dd)
            if due_dt is None:
                continue

            # A date-only deadline expires at the end of its day, not at 00:00.
            if is_date_only(dd):
                is_overdue = due_dt.date() < now.date()
            else:
                is_overdue = due_dt < now

            if is_overdue:
                overdue.append(item)

        overdue.sort(
            key=lambda item: (
                due_sort_datetime(item.get("due_date")),
                (item.get("title") or "").lower(),
            )
        )
        return overdue

    # ------------------------------------------------------------------
    # Notification audit trail & deduplication
    # ------------------------------------------------------------------
    async def has_notification_been_sent(
        self, item_type: str, item_id: str, fingerprint: Optional[str] = None
    ) -> bool:
        """Returns True if this notification has already been sent to Discord."""
        async with self._connect() as db:
            if fingerprint:
                cursor = await db.execute(
                    """
                    SELECT 1 FROM notification_history
                    WHERE item_type = ? AND item_id = ? AND fingerprint = ?
                    LIMIT 1
                    """,
                    (item_type, item_id, fingerprint),
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT 1 FROM notification_history
                    WHERE item_type = ? AND item_id = ?
                    LIMIT 1
                    """,
                    (item_type, item_id),
                )
            row = await cursor.fetchone()
            return row is not None

    async def record_notification(
        self, item_type: str, item_id: str, fingerprint: str
    ) -> None:
        """Records a successfully sent notification for auditing and deduplication."""
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO notification_history (item_type, item_id, fingerprint)
                VALUES (?, ?, ?)
                """,
                (item_type, item_id, fingerprint),
            )
            await db.commit()


db_manager = DatabaseManager()
