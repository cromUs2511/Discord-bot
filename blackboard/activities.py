"""
blackboard/activities.py — Course Activities, Assignments, and Deadlines Extractor.

Extracts activity title, due date, status, points, and course ID from both:
1. Blackboard Ultra "To Do / Due Soon" global feed on the Courses page.
2. Individual Course Gradebooks.
"""

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from utils.dates import convert_utc_due_to_local, parse_due_datetime

logger = logging.getLogger("blackboard.activities")

# Tokens that identify table headers rather than real coursework.
_HEADER_TITLES = {
    "coursework",
    "coursework name",
    "item",
    "item name",
    "assignment",
    "assignment name",
    "due date",
    "status",
    "grade",
    "grading",
    "points",
    "points possible",
    "submission",
    "submissions",
    "attempts",
    "student",
    "name",
    "overall grade",
    "last activity",
}

_DATE_RE = re.compile(
    r"^\d{1,2}/\d{1,2}/\d{2,4}(?:\s+\d{1,2}:\d{2}\s*[AP]M)?$", re.IGNORECASE
)
_POINTS_RE = re.compile(r"^\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?$")
_TITLE_SUFFIX_RE = re.compile(r"\s+(?:submission|assignment)$", re.IGNORECASE)


def canonical_title(title: str) -> str:
    """
    Normalize a title so the To Do feed and the gradebook produce the same ID.

    Blackboard often labels the same item "Coursework 1.1" in To Do and
    "Coursework 1.1 Submission" in the gradebook.
    """
    cleaned = " ".join((title or "").split()).strip()
    return _TITLE_SUFFIX_RE.sub("", cleaned).strip()


def stable_activity_id(course_id: str, title: str) -> str:
    raw_id = f"{course_id}_{canonical_title(title)}"
    return hashlib.md5(raw_id.encode("utf-8")).hexdigest()[:16]


def activity_fingerprint(
    course_id: str,
    title: str,
    due_date: Optional[str],
    status: Optional[str],
) -> str:
    # Use the canonical title so the To Do panel and gradebook do not fight
    # over "Coursework 1.1" vs "Coursework 1.1 Submission".
    raw = f"{course_id}:{canonical_title(title)}:{due_date}:{status}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class Activity:
    id: str  # Hash or unique Blackboard identifier
    course_id: str
    course_name: str
    title: str
    due_date: Optional[str] = None  # e.g., '11/8/26 11:59 PM' or '10/31/26'
    status: Optional[str] = None  # e.g., 'Not submitted', 'Submitted', 'Graded'
    points: Optional[str] = None  # e.g., '1/1' or '20'
    url: Optional[str] = None

    @property
    def fingerprint(self) -> str:
        """Stable hash used for change detection."""
        return activity_fingerprint(
            self.course_id, self.title, self.due_date, self.status
        )


def _course_lookup(courses: list) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Build lookup tables for internal ID, full course ID, and course name."""
    by_internal: Dict[str, Any] = {}
    by_id: Dict[str, Any] = {}
    by_name: Dict[str, Any] = {}
    for course in courses:
        internal_id = getattr(course, "internal_id", None)
        if internal_id:
            by_internal[str(internal_id).strip().lower()] = course
        if getattr(course, "id", None):
            by_id[str(course.id).strip().lower()] = course
        if getattr(course, "name", None):
            by_name[str(course.name).strip().lower()] = course
    return by_internal, by_id, by_name


def _match_course(
    meta_line: str,
    course_name_hint: str,
    internal_id_hint: str,
    courses: list,
) -> Optional[Any]:
    """Resolve a To Do metadata line to a known course."""
    by_internal, by_id, by_name = _course_lookup(courses)

    for key in (internal_id_hint, course_name_hint):
        normalized = (key or "").strip().lower()
        if normalized:
            if normalized in by_internal:
                return by_internal[normalized]
            if normalized in by_id:
                return by_id[normalized]
            if normalized in by_name:
                return by_name[normalized]

    meta_lower = meta_line.lower()
    for course in courses:
        course_id = str(getattr(course, "id", "") or "").lower()
        course_name = str(getattr(course, "name", "") or "").lower()
        internal_id = str(getattr(course, "internal_id", "") or "").lower()
        if course_id and course_id in meta_lower:
            return course
        if course_name and course_name in meta_lower:
            return course
        if internal_id and internal_id in meta_lower:
            return course

    return None


async def extract_todo_activities(page, courses: list) -> List[Activity]:
    """
    Extracts pending/upcoming activities directly from the 'To Do / Due Soon'
    panel on the Blackboard Courses page (/ultra/course).
    """
    activities: List[Activity] = []

    raw_items = await page.evaluate(
        r"""() => {
        const results = [];
        const elements = document.querySelectorAll('li, div[role="listitem"], .stream-entry');
        elements.forEach(el => {
            const text = (el.innerText || '').trim();
            if ((text.includes('·') || text.includes('•') || text.includes('Assignment') || text.includes('Test'))
                && (text.includes('PM') || text.includes('AM'))) {
                if (text.length > 20 && text.length < 350) {
                    results.push(text);
                }
            }
        });
        return [...new Set(results)];
    }"""
    )

    for item in raw_items:
        lines = [line.strip() for line in item.split("\n") if line.strip()]
        if len(lines) < 3:
            continue

        meta_line = ""
        date_line = ""
        for line in lines:
            if "·" in line or "•" in line:
                if re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", line):
                    date_line = line
                if any(
                    keyword in line
                    for keyword in ("Assignment", "Test", "Quiz", "Exam", "_")
                ):
                    meta_line = line

        if not meta_line:
            continue

        meta_parts = [
            part.strip()
            for part in re.split(r"[·•]", meta_line)
            if part.strip()
        ]
        course_name_hint = meta_parts[1] if len(meta_parts) > 1 else ""
        internal_id_hint = meta_parts[2] if len(meta_parts) > 2 else ""

        matched_course = _match_course(
            meta_line, course_name_hint, internal_id_hint, courses
        )
        if not matched_course:
            # Never insert an orphan row that JOIN-based queries cannot display.
            logger.warning(
                "Skipping To Do item with unrecognized course metadata: %s",
                meta_line,
            )
            continue

        # Choose a title line, skipping the month/day tokens and metadata lines.
        title_candidates = [
            line
            for line in lines
            if line not in (meta_line, date_line)
            and not re.fullmatch(r"[A-Z][a-z]{2,8}", line)
            and not re.fullmatch(r"\d{1,2}", line)
        ]
        title = title_candidates[0] if title_candidates else lines[0]
        if not title:
            continue

        due_date_str: Optional[str] = None
        if date_line:
            match = re.search(
                r"(\d{1,2}/\d{1,2}/\d{2,4})(?:\s*[·•]\s*(\d{1,2}:\d{2}\s*[AP]M))?",
                date_line,
                re.IGNORECASE,
            )
            if match:
                due_date_str = match.group(1)
                if match.group(2):
                    due_date_str = f"{due_date_str} {match.group(2)}"
            else:
                date_parts = [
                    part.strip()
                    for part in re.split(r"[·•]", date_line)
                    if part.strip()
                ]
                if len(date_parts) >= 3:
                    due_date_str = f"{date_parts[1]} {date_parts[2]}"
                elif date_parts:
                    due_date_str = " ".join(date_parts)

        # Blackboard on this deployment renders due times in UTC even when
        # the browser requests Asia/Manila, so shift scraped datetimes to
        # local time here (date-only and "Ongoing" pass through untouched).
        if due_date_str:
            due_date_str = convert_utc_due_to_local(due_date_str)

        activities.append(
            Activity(
                id=stable_activity_id(matched_course.id, title),
                course_id=matched_course.id,
                course_name=matched_course.name,
                title=title,
                due_date=due_date_str,
                status="Not submitted",
                points=None,
                url=page.url,
            )
        )

    logger.info("Extracted %d pending activity(ies) from To Do panel.", len(activities))
    return activities


async def extract_course_activities(
    page, course_id: str, course_name: str
) -> List[Activity]:
    """
    Extracts activities and deadlines from the course Gradebook tab.
    Assumes `page` is currently loaded on the course outline page.
    """
    activities: List[Activity] = []

    # Click the Gradebook tab
    grade_tab = page.locator(
        '[role="tab"]:has-text("Gradebook"), a:has-text("Gradebook"), button:has-text("Gradebook")'
    ).first
    if await grade_tab.count() == 0:
        logger.warning("No Gradebook tab found for %s", course_id)
        return activities

    try:
        await grade_tab.click(force=True)
        await asyncio.sleep(4)
    except Exception as e:
        logger.warning("Failed to click Gradebook tab for %s: %s", course_id, e)
        return activities

    # Parse rows in the gradebook
    raw_rows = await page.evaluate(
        r"""() => {
        const rows = [];
        const trs = document.querySelectorAll('tbody tr, [role="row"], .gradebook-row');

        trs.forEach(tr => {
            const cells = [];
            tr.querySelectorAll('td, [role="gridcell"], [role="cell"]').forEach(td => {
                const txt = (td.innerText || '').trim();
                if (txt) cells.push(txt);
            });

            if (cells.length > 0) {
                rows.push(cells);
            } else {
                const txt = (tr.innerText || '').trim();
                if (txt) rows.push([txt]);
            }
        });

        return rows;
    }"""
    )

    for row in raw_rows:
        if not row:
            continue

        title = row[0].strip()
        if not title or title.lower() in _HEADER_TITLES:
            continue

        due_date = None
        points = None

        for cell in row[1:]:
            stripped = cell.strip()
            cell_lower = stripped.lower()

            if _POINTS_RE.fullmatch(stripped):
                points = stripped
            elif "%" in stripped:
                points = stripped
            elif "ongoing" in cell_lower:
                due_date = "Ongoing"
            elif _DATE_RE.fullmatch(stripped) or parse_due_datetime(stripped):
                due_date = stripped

        # Strict Submission & Grading Status Evaluation:
        # If any cell contains "submitted" or "attempt", it IS submitted!
        # Do not let "Not graded" overwrite the fact that student submitted it.
        is_submitted = any(
            "submitted" in c.lower() or "attempt" in c.lower() for c in row[1:]
        )
        is_graded = any(
            c.lower() == "graded" or "final grade" in c.lower() for c in row[1:]
        )

        if is_graded:
            status = "Graded"
        elif is_submitted:
            status = "Submitted"
        else:
            # Check for past due or not submitted
            found_status = None
            for cell in row[1:]:
                cell_lower = cell.lower()
                if "past due" in cell_lower or "overdue" in cell_lower:
                    found_status = "Past due"
                elif "not submitted" in cell_lower:
                    found_status = "Not submitted"
            status = found_status or "Not submitted"

        # See To Do extractor: gradebook wall-times also arrive in UTC on
        # this deployment and must be shifted to Asia/Manila (date-only
        # values pass through unchanged).
        if due_date:
            due_date = convert_utc_due_to_local(due_date)

        activities.append(
            Activity(
                id=stable_activity_id(course_id, title),
                course_id=course_id,
                course_name=course_name,
                title=title,
                due_date=due_date,
                status=status or "Not submitted",
                points=points,
                url=page.url,
            )
        )

    return activities
