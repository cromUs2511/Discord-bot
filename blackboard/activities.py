"""
blackboard/activities.py — Course Activities, Assignments, and Deadlines Extractor.

Extracts activity title, due date, status, points, and course ID from the Gradebook.
"""

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger("blackboard.activities")


@dataclass
class Activity:
    id: str  # Hash or unique Blackboard identifier
    course_id: str
    course_name: str
    title: str
    due_date: Optional[str] = None  # e.g., '10/31/26' or 'Ongoing'
    status: Optional[str] = None  # e.g., 'Submitted', 'Not submitted', 'Graded'
    points: Optional[str] = None  # e.g., '1/1'
    url: Optional[str] = None

    @property
    def fingerprint(self) -> str:
        """Stable hash used for change detection (e.g. deadline changes)."""
        raw = f"{self.course_id}:{self.title}:{self.due_date}:{self.status}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
        # In Mapua Blackboard Gradebook rows:
        # e.g. ["Coursework 1.1 Submission", "10/31/26", "Submitted", "Not graded"]
        # or ["Student Agreement and Honor Code", "1 attempt submitted", "Graded", "1/1", ...]
        due_date = None
        status = None
        points = None

        for cell in row[1:]:
            cell_lower = cell.lower()
            # Check for date format (e.g., MM/DD/YY or Ongoing)
            if "/" in cell and any(c.isdigit() for c in cell):
                due_date = cell
            elif "ongoing" in cell_lower:
                due_date = "Ongoing"
            elif any(s in cell_lower for s in ["submitted", "not submitted", "graded", "past due", "overdue"]):
                status = cell
            elif "/" in cell and cell.replace("/", "").isdigit():
                points = cell
            elif "%" in cell:
                points = cell

        # Stable ID
        raw_id = f"{course_id}_{title}"
        stable_id = hashlib.md5(raw_id.encode("utf-8")).hexdigest()[:16]

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
