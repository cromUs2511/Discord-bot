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
from discord_bot.embeds import create_announcement_embed, create_activity_embed
from blackboard.announcements import Announcement
from blackboard.activities import Activity

logger = logging.getLogger("discord.notifications")


async def send_announcement_notification(client: discord.Client, announcement: Announcement, is_update: bool = False):
    """
    Sends an announcement STRICTLY to its respective course channel only.
    """
    target_channel_id = config.get_course_channel_id(announcement.course_id, announcement.course_name)

    channel = client.get_channel(target_channel_id)
    if not channel:
        logger.warning("Channel ID %s not found for course [%s].", target_channel_id, announcement.course_id)
        return

    embed = create_announcement_embed(announcement, is_update=is_update)
    try:
        await channel.send(embed=embed)
        logger.info("Sent announcement strictly to channel #%s: %s", channel.name, announcement.title)
    except Exception as e:
        logger.error("Failed to send announcement to channel %s: %s", channel.name, e)


async def send_activity_notification(
    client: discord.Client,
    activity: Activity,
    event_type: str = "new",
    old_deadline: str | None = None
):
    """
    Sends activity/deadline notifications STRICTLY to its respective course channel only.
    """
    target_channel_id = config.get_course_channel_id(activity.course_id, activity.course_name)
    channel = client.get_channel(target_channel_id)

    if not channel:
        logger.warning("Channel ID %s not found for course [%s].", target_channel_id, activity.course_id)
        return

    embed = create_activity_embed(activity, event_type=event_type, old_deadline=old_deadline)
    try:
        await channel.send(embed=embed)
        logger.info("Sent activity notification strictly to channel #%s: %s (Type: %s)", channel.name, activity.title, event_type)
    except Exception as e:
        logger.error("Failed to send activity notification to channel %s: %s", channel.name, e)
