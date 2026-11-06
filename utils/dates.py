"""
utils/dates.py — Shared due-date and timestamp parsing.

Blackboard emits dates in several formats, and SQLite stores
CURRENT_TIMESTAMP values in UTC. Keeping the parsing in one place avoids
subtle inconsistencies between the database layer and the slash commands.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

import pytz

import config

# Formats seen in Blackboard gradebooks, To Do panels, and the database.
DUE_DATE_FORMATS = (
    "%m/%d/%y %I:%M %p",
    "%m/%d/%Y %I:%M %p",
    "%m/%d/%y %H:%M",
    "%m/%d/%Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%m/%d/%y",
    "%m/%d/%Y",
    "%b %d, %Y %I:%M %p",
    "%B %d, %Y %I:%M %p",
    "%b %d, %Y",
    "%B %d, %Y",
)

_DATE_ONLY_RE = re.compile(
    r"^(?:\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})$"
)


def local_timezone():
    """Return the configured timezone, falling back to UTC if it is invalid."""
    try:
        return pytz.timezone(config.TIMEZONE)
    except Exception:
        return pytz.utc


def now_local() -> datetime:
    """Naive local datetime in the configured timezone."""
    return datetime.now(local_timezone()).replace(tzinfo=None)


def parse_due_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse a Blackboard due-date string into a naive local datetime."""
    if not value:
        return None
    cleaned = " ".join(str(value).split())
    if not cleaned or cleaned.lower() in {"ongoing", "no deadline", "none"}:
        return None

    for fmt in DUE_DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue

    # Last resort: use just the first token (e.g. "11/8/26 11:59 PM").
    first_token = cleaned.split()[0]
    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(first_token, fmt)
        except ValueError:
            continue
    return None


def due_sort_datetime(value: Optional[str]) -> datetime:
    """Sort key for due dates: parsed dates first, then ongoing/unknown."""
    parsed = parse_due_datetime(value)
    if parsed is not None:
        return parsed
    return datetime.max


def is_date_only(value: Optional[str]) -> bool:
    """True when a due date has no time component (e.g. '11/8/26')."""
    if not value:
        return False
    return bool(_DATE_ONLY_RE.fullmatch(str(value).strip()))


def parse_db_timestamp(value: Optional[str]) -> Optional[datetime]:
    """
    Parse a SQLite timestamp (stored as UTC by CURRENT_TIMESTAMP) and convert
    it to a naive datetime in the configured local timezone.
    """
    if not value:
        return None

    cleaned = str(value).strip()
    parsed: Optional[datetime] = None

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(cleaned, fmt)
            break
        except ValueError:
            continue

    if parsed is None:
        return parse_due_datetime(cleaned)

    # SQLite CURRENT_TIMESTAMP is UTC.
    return pytz.utc.localize(parsed).astimezone(local_timezone()).replace(tzinfo=None)
