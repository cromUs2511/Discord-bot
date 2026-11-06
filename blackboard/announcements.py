"""
blackboard/announcements.py — Course Announcements Extractor.

Extracts title, content/body, author, posted date, and course ID for all
announcements in a given Blackboard Ultra course.
"""

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger("blackboard.announcements")

_HEADER_LINE_RE = re.compile(
    r"^(?:\d+\s+total|course announcements|announcements?|posted|view|"
    r"show more|collapse)$",
    re.IGNORECASE,
)
_POSTED_DATE_RE = re.compile(
    r"(?:posted|published)\s*(?:on)?\s*([A-Z][a-z]{2,8}\s+\d{1,2},\s*\d{4}"
    r"|\d{1,2}/\d{1,2}/\d{2,4}(?:\s+\d{1,2}:\d{2}\s*[AP]M)?)",
    re.IGNORECASE,
)
_AUTHOR_RE = re.compile(
    r"(?:posted|published)\s+by\s+([^\n\r]{2,80})", re.IGNORECASE
)

_DATE_PATTERNS = [
    re.compile(
        r"^\d+\s+(?:second|minute|hour|day|week|month)s?\s+ago(?:\s*,\s*at\s+\d{1,2}:\d{2}\s*[AP]M)?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:just now|yesterday|today)(?:\s*,\s*at\s+\d{1,2}:\d{2}\s*[AP]M)?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\d{1,2}/\d{1,2}/\d{2,4}(?:\s*,\s*\d{1,2}:\d{2}\s*[AP]M)?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^[A-Z][a-z]{2,8}\s+\d{1,2},?\s*\d{4}(?:\s*,\s*\d{1,2}:\d{2}\s*[AP]M)?$",
        re.IGNORECASE,
    ),
]


def is_date_line(line: str) -> bool:
    line = line.strip()
    return any(p.match(line) for p in _DATE_PATTERNS)


def normalize_posted_date(line: str) -> str:
    cleaned = line.strip()
    match = re.search(r"at\s+(\d{1,2}:\d{2}\s*[AP]M)", cleaned, re.IGNORECASE)
    if match and "ago" in cleaned.lower():
        return f"Today, {match.group(1)}"
    return cleaned


def clean_announcement_content(text: str) -> str:
    lines = [line.strip() for line in (text or "").split("\n") if line.strip()]
    content_lines = [
        l for l in lines if not is_date_line(l) and not _HEADER_LINE_RE.fullmatch(l)
    ]
    return "\n".join(content_lines).strip()


@dataclass
class Announcement:
    id: str  # Hash or unique Blackboard identifier
    course_id: str
    course_name: str
    title: str
    content: str
    posted_date: Optional[str] = None
    author: Optional[str] = None
    url: Optional[str] = None

    @property
    def fingerprint(self) -> str:
        """Stable hash used for change detection. Strips dynamic relative timestamps."""
        body = clean_announcement_content(self.content)
        raw = f"{self.course_id}:{self.title.strip()}:{body}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _clean_lines(text: str) -> List[str]:
    lines = [line.strip() for line in (text or "").split("\n") if line.strip()]
    cleaned: List[str] = []
    for line in lines:
        if _HEADER_LINE_RE.fullmatch(line):
            continue
        if line.isdigit():
            continue
        cleaned.append(line)
    return cleaned


async def extract_course_announcements(
    page, course_id: str, course_name: str
) -> List[Announcement]:
    """
    Extracts announcements from the currently open course outline page.
    Assumes `page` is already loaded on `/ultra/courses/<course_internal_id>/outline`.
    """
    announcements: List[Announcement] = []

    # Click the Announcements tab
    ann_tab = page.locator(
        '[role="tab"]:has-text("Announcements"), a:has-text("Announcements"), button:has-text("Announcements")'
    ).first
    if await ann_tab.count() == 0:
        logger.warning("No Announcements tab found for %s", course_id)
        return announcements

    try:
        await ann_tab.click(force=True)
        await asyncio.sleep(3)
    except Exception as e:
        logger.warning("Failed to click Announcements tab for %s: %s", course_id, e)
        return announcements

    # Extract announcements from the DOM. There is deliberately no
    # "whole page" fallback: capturing main.innerText when the tab failed to
    # load would create a bogus announcement notification.
    raw_items = await page.evaluate(
        r"""() => {
        const items = [];

        const cards = document.querySelectorAll(
            'bb-announcement-card, .announcement-card, .announcement-list-item, [role="article"], .announcement-item, tbody tr'
        );

        cards.forEach(card => {
            const text = (card.innerText || '').trim();
            if (text.length <= 5) return;

            const titleEl = card.querySelector(
                'h3, h4, .announcement-title, [role="heading"]'
            );
            const authorEl = card.querySelector(
                '[class*="author"], [class*="creator"], .user-name, bb-user-profile'
            );
            const dateEl = card.querySelector(
                'time, [class*="date"], [class*="posted"]'
            );

            items.push({
                fullText: text,
                title: titleEl ? titleEl.innerText.trim() : '',
                author: authorEl ? authorEl.innerText.trim() : '',
                posted: dateEl
                    ? (dateEl.getAttribute('datetime') || dateEl.innerText.trim())
                    : ''
            });
        });

        return items;
    }"""
    )

    if not raw_items:
        logger.info("No announcement cards found for %s.", course_id)
        return announcements

    seen_ids: set[str] = set()

    for item in raw_items:
        lines = _clean_lines(item.get("fullText", ""))
        if not lines:
            continue

        title = (item.get("title") or "").strip() or lines[0]
        if not title:
            continue

        # Avoid duplicating the title at the start of the preview.
        content_lines = list(lines)
        if content_lines and content_lines[0] == title:
            content_lines = content_lines[1:]

        # Extract date lines so they don't pollute content or trigger false updates
        extracted_date = None
        remaining_content_lines = []
        for line in content_lines:
            if is_date_line(line):
                if not extracted_date:
                    extracted_date = line
            else:
                remaining_content_lines.append(line)

        content = "\n".join(remaining_content_lines).strip()

        posted_date = (item.get("posted") or "").strip() or extracted_date or None
        if posted_date:
            posted_date = normalize_posted_date(posted_date)
        elif not posted_date:
            match = _POSTED_DATE_RE.search(item.get("fullText", ""))
            if match:
                posted_date = match.group(1)

        author = (item.get("author") or "").strip() or None
        if not author:
            match = _AUTHOR_RE.search(item.get("fullText", ""))
            if match:
                author = match.group(1).strip()

        stable_id = hashlib.md5(
            f"{course_id}_{title}".encode("utf-8")
        ).hexdigest()[:16]
        if stable_id in seen_ids:
            continue
        seen_ids.add(stable_id)

        announcements.append(
            Announcement(
                id=stable_id,
                course_id=course_id,
                course_name=course_name,
                title=title,
                content=content,
                posted_date=posted_date,
                author=author,
                url=page.url,
            )
        )

    return announcements
