"""
blackboard/activities.py — Course Activities, Assignments, and Deadlines Extractor.

Extracts activity title, due date, status, points, and course ID from both:
1. Blackboard Ultra "To Do / Due Soon" global feed on the Courses page.
2. Individual Course Gradebooks.
"""

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import List, Optional, Dict

logger = logging.getLogger("blackboard.activities")


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
        """Stable hash used for change detection (e.g. deadline changes)."""
        raw = f"{self.course_id}:{self.title}:{self.due_date}:{self.status}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def extract_todo_activities(page, courses: list) -> List[Activity]:
    """
    Extracts pending/upcoming activities directly from the 'To Do / Due Soon' panel
    on the Blackboard Courses page (/ultra/course).
    """
    activities: List[Activity] = []

    # Map internal IDs (_170077_1) and names to course objects
    course_by_internal: Dict[str, any] = {}
    course_by_name: Dict[str, any] = {}
    for c in courses:
        if getattr(c, "internal_id", None):
            course_by_internal[c.internal_id] = c
        course_by_name[c.name.strip().lower()] = c

    raw_items = await page.evaluate(r"""() => {
        const results = [];
        const elements = document.querySelectorAll('li, div[role="listitem"], .stream-entry');
        elements.forEach(el => {
            const text = (el.innerText || '').trim();
            if ((text.includes('·') || text.includes('Assignment') || text.includes('Test')) && (text.includes('PM') || text.includes('AM'))) {
                if (text.length > 20 && text.length < 350) {
                    results.push(text);
                }
            }
        });
        return [...new Set(results)];
    }""")

    for item in raw_items:
        lines = [l.strip() for l in item.split("\n") if l.strip()]
        # Expected structure:
        # ['Nov', '8', 'Title', 'Type · CourseName · InternalID', 'Day · MM/DD/YY · Time']
        if len(lines) < 4:
            continue

        # Locate title: typically after month/day
        title = lines[2] if len(lines) >= 5 else lines[0]
        meta_line = ""
        date_line = ""

        for line in lines:
            if "·" in line and any(k in line for k in ["Assignment", "Test", "_"]):
                meta_line = line
            elif "·" in line and ("AM" in line or "PM" in line):
                date_line = line

        if not meta_line:
            continue

        meta_parts = [p.strip() for p in meta_line.split("·")]
        course_name_hint = meta_parts[1] if len(meta_parts) > 1 else ""
        internal_id_hint = meta_parts[2] if len(meta_parts) > 2 else ""

        # Find matching course
        matched_course = course_by_internal.get(internal_id_hint)
        if not matched_course and course_name_hint:
            matched_course = course_by_name.get(course_name_hint.lower())

        course_id = matched_course.id if matched_course else internal_id_hint or "UNKNOWN_COURSE"
        course_name = matched_course.name if matched_course else course_name_hint or course_id

        # Parse due date & time
        due_date_str = None
        if date_line:
            date_parts = [p.strip() for p in date_line.split("·")]
            # e.g. ['Sun', '11/8/26', '11:59 PM']
            if len(date_parts) >= 3:
                due_date_str = f"{date_parts[1]} {date_parts[2]}"
            elif len(date_parts) == 2:
                due_date_str = f"{date_parts[0]} {date_parts[1]}"
            else:
                due_date_str = date_line

        raw_id = f"{course_id}_{title}"
        stable_id = hashlib.md5(raw_id.encode("utf-8")).hexdigest()[:16]

        activities.append(
            Activity(
                id=stable_id,
                course_id=course_id,
                course_name=course_name,
                title=title,
                due_date=due_date_str,
                status="Not submitted",
                points=None,
                url=page.url,
            )
        )

    logger.info("Extracted %d pending activity(ies) from To Do panel.", len(activities))
    return activities


async def extract_course_activities(page, course_id: str, course_name: str) -> List[Activity]:
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
    raw_rows = await page.evaluate(r"""() => {
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
    }""")

    for row in raw_rows:
        row_text = " | ".join(row)
        # Skip student name header or empty rows
        if "LORENZO KOLLIN" in row_text.upper() or len(row) == 0:
            continue

        title = row[0]
        due_date = None
        points = None

        # Determine due date and points from cells
        for cell in row[1:]:
            cell_lower = cell.lower()
            if "/" in cell and any(c.isdigit() for c in cell) and not ("graded" in cell_lower or "submitted" in cell_lower):
                # Only treat as due_date if it's not a fraction like 1/1
                if any(delim in cell for delim in ["/26", "/25", "/24", "/27", "/23", "/22"]):
                    due_date = cell
                elif not due_date and len(cell.split("/")) == 3:
                    due_date = cell
            elif "ongoing" in cell_lower:
                due_date = "Ongoing"
            elif "/" in cell and cell.replace("/", "").strip().isdigit():
                points = cell
            elif "%" in cell:
                points = cell

        # Strict Submission & Grading Status Evaluation:
        # If any cell contains "submitted" or "attempt", it IS submitted!
        # Do not let "Not graded" overwrite the fact that student submitted it.
        is_submitted = any("submitted" in c.lower() or "attempt" in c.lower() for c in row[1:])
        is_graded = any(c.lower() == "graded" or "final grade" in c.lower() for c in row[1:])

        if is_graded:
            status = "Graded"
        elif is_submitted:
            status = "Submitted"
        else:
            # Check for past due or not submitted
            found_status = None
            for cell in row[1:]:
                cl = cell.lower()
                if "past due" in cl or "overdue" in cl:
                    found_status = "Past due"
                elif "not submitted" in cl:
                    found_status = "Not submitted"
            status = found_status or "Not submitted"

        stable_id = hashlib.md5(f"{course_id}_{title}".encode("utf-8")).hexdigest()[:16]

        activities.append(
            Activity(
                id=stable_id,
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
