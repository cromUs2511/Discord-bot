"""
discord_bot/notifications.py — Sends announcement and activity notifications to the configured channel.
"""

import logging
import discord
import config
from discord_bot.embeds import create_announcement_embed, create_activity_embed
from blackboard.announcements import Announcement
from blackboard.activities import Activity

logger = logging.getLogger("discord.notifications")


async def send_announcement_notification(client: discord.Client, announcement: Announcement, is_update: bool = False):
    channel = client.get_channel(config.DISCORD_NOTIFICATION_CHANNEL_ID)
    if not channel:
        logger.error("Notification channel %s not found or bot lacks access.", config.DISCORD_NOTIFICATION_CHANNEL_ID)
        return

    embed = create_announcement_embed(announcement, is_update=is_update)
    try:
        await channel.send(embed=embed)
        logger.info("Sent announcement notification: %s", announcement.title)
    except Exception as e:
        logger.error("Failed to send announcement notification: %s", e)


async def send_activity_notification(
    client: discord.Client,
    activity: Activity,
    event_type: str = "new",
    old_deadline: str | None = None
):
    channel = client.get_channel(config.DISCORD_NOTIFICATION_CHANNEL_ID)
    if not channel:
        logger.error("Notification channel %s not found or bot lacks access.", config.DISCORD_NOTIFICATION_CHANNEL_ID)
        return

    embed = create_activity_embed(activity, event_type=event_type, old_deadline=old_deadline)
    try:
        await channel.send(embed=embed)
        logger.info("Sent activity notification: %s (Type: %s)", activity.title, event_type)
    except Exception as e:
        logger.error("Failed to send activity notification: %s", e)
