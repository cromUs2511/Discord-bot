"""
utils/dates.py — Shared due-date and timestamp parsing.

Blackboard emits dates in several formats, and SQLite stores
CURRENT_TIMESTAMP values in UTC. Keeping the parsing in one place avoids
subtle inconsistencies between the database layer and the slash commands.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
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


def _format_due_local(dt: datetime) -> str:
    """
    Format a datetime in Blackboard style: 'M/D/YY h:MM AM' (no leading
    zeros on month/day/hour), e.g. '11/9/26 11:59 PM'.
    """
    hour12 = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.month}/{dt.day}/{dt.year % 100:02d} {hour12}:{dt.minute:02d} {ampm}"


def scraped_times_mode() -> str:
    """Return 'auto' | 'utc' | 'local' (delegates to config helper)."""
    try:
        return config.scraped_times_mode()
    except Exception:
        return "auto"


def should_convert_scraped_times(browser_offset_minutes: Optional[int] = None) -> bool:
    """
    Decide whether a scraped wall-time must be shifted UTC -> local.

    Modes:
      'utc'   -> always convert (old behaviour).
      'local' -> never convert (browser already renders local time).
      'auto'  -> convert only when the browser's measured offset differs
                 from the desired local offset (e.g. browser in UTC 0 vs
                 Manila -480 -> convert; browser already in Singapore/Manila
                 -480 -> do NOT double-convert). When no offset is
                 available (e.g. DB migration, tests with fake pages),
                 default to converting to preserve the UTC->Manila fix.

    ``browser_offset_minutes`` is JS ``new Date().getTimezoneOffset()``:
    minutes to ADD to local to get UTC (Manila/Singapore = -480, UTC = 0).
    """
    mode = scraped_times_mode()
    if mode == "utc":
        return True
    if mode == "local":
        return False
    # auto
    if browser_offset_minutes is None:
        return True
    try:
        tz = local_timezone()
        offset = datetime.now(tz).utcoffset()
        # Desired minutes-to-add-to-local-to-get-UTC (e.g. Manila: -480).
        desired = -offset.total_seconds() // 60 if offset else 0
        return int(browser_offset_minutes) != int(desired)
    except Exception:
        return True


def convert_utc_due_to_local(
    value: Optional[str], force: Optional[bool] = None
) -> Optional[str]:
    """
    Convert a UTC due-date string (as scraped from Blackboard on Railway)
    into the configured local timezone (Asia/Manila, UTC+8).

    Why this exists: Blackboard Ultra serves due times relative to the
    account/course server timezone, which was UTC on US Railway regions
    even though the Playwright browser requested Asia/Manila. A Manila
    deadline of 11:59 PM therefore arrived as "3:59 PM" UTC. Shifting
    UTC datetimes by +8h (with date-rollover) restores the correct local
    deadline. On Singapore (SGT, UTC+8) the browser already renders
    local time, so callers in 'auto' mode pass ``force=False`` to
    avoid double-converting 11:59 PM -> 7:59 AM next day.

    Passthrough rules:
      - None / empty / "Ongoing" / date-only strings are returned unchanged
        (date-only deadlines have no time to shift).
      - If ``force`` is False, or (when ``force`` is None) the configured
        mode is 'local', values are returned unchanged.
      - Unparseable values are returned unchanged (never replace a deadline
        with something worse).
    """
    if not value:
        return value
    cleaned = " ".join(str(value).split())
    if not cleaned or cleaned.lower() in {"ongoing", "no deadline", "none"}:
        return value
    if force is not None:
        if not force:
            return value
    elif not should_convert_scraped_times(None):
        return value
    if is_date_only(cleaned):
        return value
    if ":" not in cleaned:
        return value

    parsed = parse_due_datetime(cleaned)
    if parsed is None:
        return value

    try:
        utc_dt = pytz.utc.localize(parsed)
        local_dt = utc_dt.astimezone(local_timezone()).replace(tzinfo=None)
    except Exception:
        return value
    return _format_due_local(local_dt)


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


def is_announcement_from_yesterday_or_today(
    date_str: Optional[str],
    content: Optional[str] = "",
    now_dt: Optional[datetime] = None,
) -> bool:
    """
    Checks if an announcement was posted yesterday, today, or newer.
    Prevents notification spam for old historical announcements upon initial sync or redeployment.
    """
    target = " ".join(str(date_str or "").strip().split()).lower()

    # Immediate relative keywords in date_str
    if any(k in target for k in ("today", "yesterday", "just now", "second", "minute", "hour")):
        return True

    # "1 day ago" is yesterday
    if re.search(r"\b1\s+day\s+ago\b", target):
        return True

    # "2 days ago", "5 days ago", "1 week ago", "2 months ago", etc.
    if re.search(r"\b(?:\d+\s+(?:day|week|month|year)s?\s+ago)\b", target):
        return False

    # Also check start of content for relative keywords if date_str had none
    if content:
        c_sample = content[:150].lower()
        if any(k in c_sample for k in ("today", "yesterday", "just now", "hours ago", "minutes ago")):
            return True
        if re.search(r"\b(?:\d+\s+(?:day|week|month|year)s?\s+ago)\b", c_sample):
            return False

    now = now_dt or now_local()
    yesterday = (now - timedelta(days=1)).date()

    clean_date = re.sub(
        r"^(?:posted\s*(?:on)?|published\s*(?:on)?)\s*", "", target, flags=re.I
    ).strip()

    formats = (
        "%b %d, %Y", "%B %d, %Y",
        "%b %d, %Y %I:%M %p", "%B %d, %Y %I:%M %p",
        "%m/%d/%Y", "%m/%d/%y",
        "%m/%d/%Y %I:%M %p", "%m/%d/%y %I:%M %p",
        "%Y-%m-%d", "%Y-%m-%d %H:%M:%S",
    )

    for fmt in formats:
        try:
            parsed = datetime.strptime(clean_date, fmt).date()
            return parsed >= yesterday
        except ValueError:
            pass

    m = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4})", clean_date)
    if m:
        for fmt in ("%m/%d/%Y", "%m/%d/%y"):
            try:
                parsed = datetime.strptime(m.group(1), fmt).date()
                return parsed >= yesterday
            except ValueError:
                pass

    m2 = re.search(r"([A-Za-z]{3,9}\s+\d{1,2},?\s*\d{4})", clean_date)
    if m2:
        for fmt in ("%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y"):
            try:
                parsed = datetime.strptime(
                    m2.group(1).replace(",", ""), fmt.replace(",", "")
                ).date()
                return parsed >= yesterday
            except ValueError:
                pass

    return False


def is_activity_worthy_of_notification(
    due_date: Optional[str],
    status: Optional[str] = "",
    now_dt: Optional[datetime] = None,
) -> bool:
    """
    Checks if an activity/assignment should trigger a Discord notification.
    Excludes past-due expired coursework from weeks/months ago and already-submitted items.
    Allows active upcoming deadlines, and items due yesterday/today.
    """
    status_lower = (status or "").lower()
    if "submitted" in status_lower or "graded" in status_lower:
        return False

    if due_date:
        due_dt = parse_due_datetime(due_date)
        if due_dt:
            now = now_dt or now_local()
            yesterday_start = datetime.combine(now.date() - timedelta(days=1), datetime.min.time())
            # If due date is strictly before yesterday, it is an old past deadline
            if due_dt < yesterday_start:
                return False

    return True
