"""
blackboard/announcements.py — Course Announcements Extractor.

Extracts title, content/body, author, posted date, and course ID for all announcements
in a given Blackboard Ultra course.
"""

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import List, Optional

import config
from blackboard.browser import get_browser_context

logger = logging.getLogger("blackboard.announcements")


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
        """Stable hash used for change detection."""
        raw = f"{self.course_id}:{self.title}:{self.content.strip()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def extract_course_announcements(page, course_id: str, course_name: str) -> List[Announcement]:
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

    # Extract announcements from the DOM
    raw_items = await page.evaluate(r"""() => {
        const items = [];
        
        // Blackboard Ultra announcements container
        const cards = document.querySelectorAll(
            'bb-announcement-card, .announcement-card, [role="article"], .announcement-item, tbody tr'
        );

        cards.forEach(card => {
            const text = (card.innerText || '').trim();
            if (text.length > 5) {
                // Try to find title heading
                const titleEl = card.querySelector('h3, h4, .announcement-title, [role="heading"]');
                const title = titleEl ? titleEl.innerText.trim() : '';

                items.push({
                    fullText: text,
                    title: title
                });
            }
        });

        // Fallback: If individual cards were not split, parse the text block
        if (items.length === 0) {
            const main = document.querySelector('main, #main-content-inner, .announcements-container');
            if (main) {
                items.push({
                    fullText: main.innerText,
                    title: ''
                });
            }
        }

        return items;
    }""")

    for item in raw_items:
        lines = [l.strip() for l in item["fullText"].split("\n") if l.strip()]
        # Skip header metadata lines like "2 Total", "Course Announcements", "Announcement", "Posted"
        filtered_lines = [
            l for l in lines
            if l.lower() not in ["2 total", "course announcements", "announcement", "posted", "view"]
            and not l.isdigit()
        ]

        if not filtered_lines:
            continue

        title = item["title"] or filtered_lines[0]
        content = "\n".join(filtered_lines[1:]) if len(filtered_lines) > 1 else ""

        # Generate a stable ID
        raw_id = f"{course_id}_{title}"
        stable_id = hashlib.md5(raw_id.encode("utf-8")).hexdigest()[:16]

        announcements.append(
            Announcement(
                id=stable_id,
                course_id=course_id,
                course_name=course_name,
                title=title,
                content=content,
                url=page.url,
            )
        )

    return announcements
