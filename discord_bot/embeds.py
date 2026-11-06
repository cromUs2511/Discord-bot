"""
discord_bot/embeds.py — Discord Rich Embed Builders.
"""

import discord
from typing import Optional

import config
from blackboard.announcements import Announcement
from blackboard.activities import Activity


def create_announcement_embed(
    announcement: Announcement, is_update: bool = False
) -> discord.Embed:
    """Builds a Discord embed for a new or updated announcement."""
    title_prefix = "📢 UPDATED ANNOUNCEMENT" if is_update else "📢 NEW ANNOUNCEMENT"
    color = discord.Color.gold() if is_update else discord.Color.blue()

    embed = discord.Embed(
        title=f"{title_prefix}: {announcement.title}"[:256],
        description=announcement.content[:1800]
        if announcement.content
        else "No preview text provided.",
        color=color,
    )
    embed.add_field(
        name="Course",
        value=f"**{announcement.course_name}** (`{announcement.course_id}`)",
        inline=False,
    )
    if announcement.author:
        embed.add_field(name="Instructor / Author", value=announcement.author, inline=True)
    if announcement.posted_date:
        embed.add_field(name="Posted", value=announcement.posted_date, inline=True)
    if announcement.url:
        embed.add_field(
            name="Link", value=f"[Open in Blackboard]({announcement.url})", inline=False
        )

    embed.set_footer(text=f"{config.INSTITUTION_NAME} Blackboard Ultra Academic Assistant")
    return embed


def create_activity_embed(
    activity: Activity, event_type: str = "new", old_deadline: Optional[str] = None
) -> discord.Embed:
    """Builds a Discord embed for a new activity or a changed deadline."""
    if event_type == "deadline_changed":
        title = f"⏰ DEADLINE CHANGED: {activity.title}"
        color = discord.Color.orange()
    else:
        title = f"📝 NEW ASSIGNMENT DETECTED: {activity.title}"
        color = discord.Color.purple()

    embed = discord.Embed(title=title[:256], color=color)
    embed.add_field(
        name="Course",
        value=f"**{activity.course_name}** (`{activity.course_id}`)",
        inline=False,
    )

    if event_type == "deadline_changed":
        embed.add_field(
            name="Previous Due Date", value=f"~~{old_deadline or 'None'}~~", inline=True
        )
        embed.add_field(
            name="New Due Date",
            value=f"**{activity.due_date or 'No deadline'}**",
            inline=True,
        )
    else:
        embed.add_field(
            name="Due Date",
            value=f"**{activity.due_date or 'No deadline specified'}**",
            inline=True,
        )

    embed.add_field(
        name="Submission Status", value=activity.status or "Not submitted", inline=True
    )
    if activity.points:
        embed.add_field(name="Points / Score", value=activity.points, inline=True)
    if activity.url:
        embed.add_field(
            name="Source", value=f"[View in Blackboard]({activity.url})", inline=False
        )

    embed.set_footer(text=f"{config.INSTITUTION_NAME} Blackboard Ultra Academic Assistant")
    return embed
