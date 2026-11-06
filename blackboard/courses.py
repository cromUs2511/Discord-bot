"""
blackboard/courses.py — Enrolled Courses Extractor for Blackboard Ultra.

Extracts course code, course title, instructor, status, and direct outline URLs
from the Blackboard Ultra Course List page.

No term, subject prefix, or instructor name is hardcoded: every course card is
examined and course codes are detected structurally.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import List, Optional

import config
from blackboard.browser import get_browser_context
from utils.dates import parse_due_datetime

logger = logging.getLogger("blackboard.courses")

# Examples: CPE121-4_C1_2Q2627, ECEA101-1L_C1_2Q2627, IE103-1_C1_2Q2627
_COURSE_CODE_RE = re.compile(
    r"^[A-Z]{2,}\d+[A-Z0-9]*(?:-[A-Z0-9]+)*(?:_[A-Z0-9]+)+$"
)
_TERM_RE = re.compile(r"_(\d+Q\d+)$", re.IGNORECASE)
_DATE_TOKEN_RE = re.compile(r"^\d{1,2}/\d{1,2}(?:/\d{2,4})?$")


@dataclass
class Course:
    id: str  # e.g., 'CPE121-4_C1_2Q2627'
    name: str  # e.g., 'FEEDBACK AND CONTROL SYSTEMS'
    instructor: Optional[str] = None
    term: Optional[str] = None
    url: Optional[str] = None
    status: Optional[str] = "Open"
    internal_id: Optional[str] = None  # e.g., '_167790_1'


def _is_metadata_line(line: str, code: str) -> bool:
    lower = line.strip().lower()
    if not lower:
        return True
    if line.strip() == code:
        return True
    if lower in {"open", "closed", "open course", "closed course", "view", "course"}:
        return True
    if lower in {"multiple instructors", "instructor", "instructors"}:
        return True
    if _DATE_TOKEN_RE.fullmatch(lower):
        return True
    if parse_due_datetime(line) is not None:
        return True
    return False


async def extract_courses() -> List[Course]:
    """
    Navigates to /ultra/course and parses all course cards.
    Directly extracts the internal ID to construct reliable direct URLs.
    """
    base_url = (config.BLACKBOARD_URL or "https://mapua.blackboard.com").rstrip("/")
    target_url = f"{base_url}/ultra/course"

    logger.info("Opening courses list at %s...", target_url)

    async with get_browser_context(headless=True, use_saved_state=True) as (_, _, context):
        page = await context.new_page()

        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=35000)
        except Exception as e:
            logger.warning("Navigation timeout: %s", e)

        # Allow Blackboard Ultra SPA to load course cards
        await asyncio.sleep(4)

        raw_data = await page.evaluate(
            r"""() => {
            const items = [];
            const cards = document.querySelectorAll(
                'bb-base-course-card, div[data-course-id], .course-element-card, [role="listitem"]'
            );

            cards.forEach(card => {
                const text = (card.innerText || '').trim();
                if (!text) return;

                const idEl = card.querySelector('[id*="course-id-"]');
                const idAttr = idEl ? idEl.getAttribute('id') : '';
                const htmlMatch = (card.innerHTML || '').match(/course-id-(_[0-9]+_1)/);
                const fallbackMatch = (card.innerHTML || '').match(/_([0-9]+)_1/);

                items.push({
                    text: text,
                    internalId: idAttr.replace('course-id-', '')
                        || (htmlMatch ? htmlMatch[1] : null)
                        || (fallbackMatch ? fallbackMatch[0] : null)
                });
            });

            return items;
        }"""
        )

        courses = parse_course_cards(raw_data)

    logger.info("Extracted %d course(s) from the course dashboard.", len(courses))
    return courses


def parse_course_cards(raw_data: List[dict]) -> List[Course]:
    """Parse raw course-card text/ID data into ``Course`` objects."""
    courses: List[Course] = []
    seen_ids: set[str] = set()

    for item in raw_data:
        lines = [line.strip() for line in item.get("text", "").split("\n") if line.strip()]
        if not lines:
            continue

        # Identify the course code structurally (e.g. CPE121-4_C1_2Q2627).
        code = None
        term = None
        for line in lines:
            if not _COURSE_CODE_RE.fullmatch(line):
                continue
            term_match = _TERM_RE.search(line)
            line_term = term_match.group(1) if term_match else None
            if config.ACADEMIC_TERM and line_term != config.ACADEMIC_TERM:
                continue
            code = line
            term = line_term
            break

        if not code or code in seen_ids:
            continue
        seen_ids.add(code)

        code_idx = lines.index(code)

        # Determine status
        status = "Open"
        for line in lines:
            normalized = line.strip().lower()
            if normalized in {"open", "open course"}:
                status = "Open"
                break
            if normalized in {"closed", "closed course"}:
                status = "Closed"
                break

        # Determine title, preferring the line immediately before the code.
        title = None
        if code_idx > 0 and not _is_metadata_line(lines[code_idx - 1], code):
            title = lines[code_idx - 1]
        elif code_idx + 1 < len(lines) and not _is_metadata_line(
            lines[code_idx + 1], code
        ):
            title = lines[code_idx + 1]
        else:
            for line in lines:
                if not _is_metadata_line(line, code) and line.lower() not in {
                    "open",
                    "closed",
                }:
                    title = line
                    break

        # Determine instructor without hardcoded names.
        instructor = None
        for line in lines:
            lower = line.lower()
            if "instructor" in lower:
                cleaned = re.sub(
                    r"^.*?instructor[s]?\s*[:\-]?\s*", "", line, flags=re.IGNORECASE
                ).strip()
                instructor = cleaned or line.strip()
                break

        if not instructor:
            for line in reversed(lines):
                if line == code or line == title:
                    continue
                if _is_metadata_line(line, code):
                    continue
                instructor = line.strip()
                break

        internal_id = item.get("internalId")
        direct_url = (
            f"{config.BLACKBOARD_URL}/ultra/courses/{internal_id}/outline"
            if internal_id
            else None
        )

        courses.append(
            Course(
                id=code,
                name=title or code,
                instructor=instructor or "Not listed",
                term=term or config.ACADEMIC_TERM or None,
                url=direct_url,
                status=status,
                internal_id=internal_id,
            )
        )

    return courses
