"""
blackboard/courses.py — Enrolled Courses Extractor for Blackboard Ultra.

Extracts course code, course title, instructor, status, and direct outline URLs
from the Blackboard Ultra Course List page.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import List, Optional

import config
from blackboard.browser import get_browser_context

logger = logging.getLogger("blackboard.courses")


@dataclass
class Course:
    id: str  # e.g., 'CPE121-4_C1_2Q2627'
    name: str  # e.g., 'FEEDBACK AND CONTROL SYSTEMS'
    instructor: Optional[str] = None
    term: Optional[str] = "2Q2627"
    url: Optional[str] = None
    status: Optional[str] = "Open"
    internal_id: Optional[str] = None  # e.g., '_167790_1'


async def extract_courses() -> List[Course]:
    """
    Navigates to /ultra/course and parses all course cards for the current term.
    Directly extracts the internal ID to construct reliable direct URLs.
    """
    courses: List[Course] = []
    target_url = f"{config.BLACKBOARD_URL.rstrip('/')}/ultra/course"

    logger.info("Opening courses list at %s...", target_url)

    async with get_browser_context(headless=True, use_saved_state=True) as (_, _, context):
        page = await context.new_page()

        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=35000)
        except Exception as e:
            logger.warning("Navigation timeout: %s", e)

        # Allow Blackboard Ultra SPA to load course cards
        await asyncio.sleep(4)

        raw_data = await page.evaluate(r"""() => {
            const items = [];
            const cards = document.querySelectorAll(
                'bb-base-course-card, div[data-course-id], .course-element-card, [role="listitem"]'
            );

            cards.forEach(card => {
                const text = (card.innerText || '').trim();
                if (!text.includes('2Q2627') && !text.includes('CPE') && !text.includes('ECEA') && !text.includes('IE103')) {
                    return;
                }

                // Locate the element with course-id-_XXXXXX_1
                const idEl = card.querySelector('[id*="course-id-"]');
                const idAttr = idEl ? idEl.getAttribute('id') : '';
                
                // Fallback internal ID match
                const match = (card.innerHTML || '').match(/_([0-9]+)_1/);
                const internalId = idAttr.replace('course-id-', '') || (match ? match[0] : null);

                items.push({
                    text: text,
                    internalId: internalId
                });
            });

            return items;
        }""")

        seen_ids = set()
        for item in raw_data:
            lines = [l.strip() for l in item["text"].split("\n") if l.strip()]
            if not lines:
                continue

            # Identify course code (e.g. CPE121-4_C1_2Q2627)
            code = None
            code_idx = -1
            for i, line in enumerate(lines):
                if any(k in line for k in ["2Q2627", "CPE", "ECEA", "IE103"]) and ("_" in line or "-" in line):
                    code = line
                    code_idx = i
                    break

            if not code or code in seen_ids:
                continue
            seen_ids.add(code)

            # Determine title, instructor, status
            # Cards follow:
            # [Title, Code, Status, Instructor] OR [Status, Notice, Title, Code]
            title = None
            status = "Open"
            instructor = None

            for i, line in enumerate(lines):
                lower = line.lower()
                if lower in ["open", "closed"]:
                    status = line.capitalize()

            # If code was at index 1 and index 0 is not status/notice, index 0 is title
            if code_idx == 1 and lines[0].lower() not in ["open", "closed"]:
                title = lines[0]
            elif code_idx > 0 and lines[code_idx - 1].lower() not in ["open", "closed"]:
                title = lines[code_idx - 1]
            elif code_idx + 1 < len(lines) and lines[code_idx + 1].lower() not in ["open", "closed"]:
                title = lines[code_idx + 1]

            # Instructor
            for line in lines:
                lower = line.lower()
                if lower not in ["open", "closed"] and line != code and line != title:
                    if any(t in lower for t in ["ramos", "paglinawan", "gamboa", "instructor", "multiple"]):
                        instructor = line.strip()
                        break

            if not instructor:
                # Use last non-matching line as instructor
                for line in reversed(lines):
                    if line != code and line != title and line.lower() not in ["open", "closed", "view"]:
                        instructor = line.strip()
                        break

            internal_id = item.get("internalId")
            direct_url = (
                f"{config.BLACKBOARD_URL.rstrip('/')}/ultra/courses/{internal_id}/outline"
                if internal_id
                else None
            )

            courses.append(
                Course(
                    id=code,
                    name=title or code,
                    instructor=instructor or "Not listed",
                    term="2Q2627",
                    url=direct_url,
                    status=status,
                    internal_id=internal_id,
                )
            )

    return courses
