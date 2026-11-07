"""
discord_bot/notifications.py — Strict Channel Dispatcher.

Rules:
- Announcements & Activities are STRICTLY routed ONLY to their respective course channel.
- If an item cannot be mapped to any course channel, it is routed to the general notification channel.
- No cross-posting to general if a course channel exists.
"""

import logging
import discord
import config
from database.db import db_manager
from discord_bot.embeds import create_announcement_embed, create_activity_embed
from blackboard.announcements import Announcement
from blackboard.activities import Activity
from utils.dates import (
    is_announcement_from_yesterday_or_today,
    is_activity_worthy_of_notification,
)

logger = logging.getLogger("discord.notifications")


async def _resolve_channel(client: discord.Client, channel_id: int):
    """Get a channel from cache, falling back to an API fetch."""
    if not channel_id:
        return None
    channel = client.get_channel(channel_id)
    if channel is not None:
        return channel
    try:
        return await client.fetch_channel(channel_id)
    except Exception:
        return None


async def send_announcement_notification(client: discord.Client, announcement: Announcement, is_update: bool = False):
    """
    Sends an announcement STRICTLY to its respective course channel only with mention/ping.
    Guarantees zero duplicate notifications via notification_history deduplication.
    Only announces items posted yesterday, today, or newer (ignores old historical posts).
    """
    # Filter: If new announcement is older than yesterday, silently skip and record
    if not is_update and not is_announcement_from_yesterday_or_today(announcement.posted_date, announcement.content):
        logger.info(
            "Skipping old announcement notification for [%s] (posted %s): %s",
            announcement.id,
            announcement.posted_date,
            announcement.title,
        )
        try:
            await db_manager.record_notification("announcement", announcement.id, announcement.fingerprint)
        except Exception:
            pass
        return

    # Deduplicate: if this announcement with this exact fingerprint was already sent, skip
    if await db_manager.has_notification_been_sent("announcement", announcement.id, announcement.fingerprint):
        logger.info(
            "Skipping duplicate announcement notification for [%s]: %s",
            announcement.id,
            announcement.title,
        )
        return

    target_channel_id = config.get_course_channel_id(announcement.course_id, announcement.course_name)
    channel = await _resolve_channel(client, target_channel_id)
    if not channel:
        logger.warning("Channel ID %s not found for course [%s].", target_channel_id, announcement.course_id)
        return

    embed = create_announcement_embed(announcement, is_update=is_update)
    content = config.NOTIFICATION_PING.strip() if config.NOTIFICATION_PING else None

    try:
        await channel.send(
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(everyone=True, roles=True, users=True),
        )
        logger.info("Sent announcement strictly to channel #%s: %s (Ping: %s)", channel.name, announcement.title, content)
        try:
            await db_manager.record_notification("announcement", announcement.id, announcement.fingerprint)
        except Exception as e:
            logger.warning("Could not record notification history: %s", e)
    except Exception as e:
        logger.error("Failed to send announcement to channel %s: %s", channel.name, e)


async def send_activity_notification(
    client: discord.Client,
    activity: Activity,
    event_type: str = "new",
    old_deadline: str | None = None
):
    """
    Sends activity/deadline notifications STRICTLY to its respective course channel only with mention/ping.
    Guarantees zero duplicate notifications via notification_history deduplication.
    """
    item_type = "deadline_change" if event_type == "deadline_changed" else "activity"

    # Filter: If new activity has past-due deadline older than yesterday or is already submitted, skip
    if event_type == "new" and not is_activity_worthy_of_notification(activity.due_date, activity.status):
        logger.info(
            "Skipping expired/submitted activity notification for [%s] (Due: %s, Status: %s): %s",
            activity.id,
            activity.due_date,
            activity.status,
            activity.title,
        )
        try:
            await db_manager.record_notification(item_type, activity.id, activity.fingerprint)
        except Exception:
            pass
        return

    if await db_manager.has_notification_been_sent(item_type, activity.id, activity.fingerprint):
        logger.info(
            "Skipping duplicate %s notification for [%s]: %s",
            item_type,
            activity.id,
            activity.title,
        )
        return

    target_channel_id = config.get_course_channel_id(activity.course_id, activity.course_name)
    channel = await _resolve_channel(client, target_channel_id)

    if not channel:
        logger.warning("Channel ID %s not found for course [%s].", target_channel_id, activity.course_id)
        return

    embed = create_activity_embed(activity, event_type=event_type, old_deadline=old_deadline)
    content = config.NOTIFICATION_PING.strip() if config.NOTIFICATION_PING else None

    try:
        await channel.send(
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(everyone=True, roles=True, users=True),
        )
        logger.info("Sent activity notification strictly to channel #%s: %s (Type: %s, Ping: %s)", channel.name, activity.title, event_type, content)
        try:
            await db_manager.record_notification(item_type, activity.id, activity.fingerprint)
        except Exception as e:
            logger.warning("Could not record notification history: %s", e)
    except Exception as e:
        logger.error("Failed to send activity notification to channel %s: %s", channel.name, e)
