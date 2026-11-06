"""
discord_bot/commands.py — Complete Slash Command Suite for Academic Assistant.

Commands:
  /ping          - Latency check
  /status        - System health dashboard
  /sync          - Trigger manual Blackboard scrape
  /courses       - List all enrolled courses
  /course        - Detailed view of a single course (announcements + activities)
  /announcements - Recent announcements (optionally by course)
  /activities    - All activities (optionally by course)
  /pending       - Activities not yet submitted
  /deadlines     - All dated deadlines chronologically
  /upcoming      - Assignments due within the next 7 days
  /overdue       - Past-due activities that aren't submitted/graded
  /summary       - Consolidated academic overview
"""

import asyncio
import time
from datetime import datetime, timedelta

import discord
from discord import app_commands
from typing import List, Optional

import config
from blackboard.browser import SESSION_STORAGE_PATH
from database.db import db_manager
from utils.dates import (
    due_sort_datetime,
    now_local,
    parse_db_timestamp,
    parse_due_datetime as parse_date,
)

START_TIME = time.time()


# ---------------------------------------------------------------------------
# Channel scoping helpers
# ---------------------------------------------------------------------------
def _channel_scope(interaction: discord.Interaction) -> Optional[dict]:
    channel = interaction.channel
    if isinstance(channel, discord.Thread) and channel.parent_id:
        return config.get_course_channel_scope(channel.parent_id)
    return config.get_course_channel_scope(interaction.channel_id)


def _matches_scope(course_id: Optional[str], scope: Optional[dict]) -> bool:
    if not scope:
        return True
    cid = (course_id or "").upper()
    prefix = (scope.get("prefix") or "").upper()
    exclude = (scope.get("exclude") or "").upper()
    if prefix and prefix not in cid:
        return False
    if exclude and cid.startswith(exclude):
        return False
    return True


# ---------------------------------------------------------------------------
# Autocomplete helper: lets users pick a course from a dropdown
# ---------------------------------------------------------------------------
async def course_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    """Called by Discord when the user starts typing in a course field."""
    courses = (
        await db_manager.search_courses(current)
        if current
        else await db_manager.get_all_courses()
    )
    return [
        app_commands.Choice(
            name=f"{c['name']} ({c['id']})"[:100],
            value=c["id"],
        )
        for c in courses[:25]
    ]


# ---------------------------------------------------------------------------
# Pagination helper for long embed lists
# ---------------------------------------------------------------------------
def _paginate_embed_fields(
    items: list,
    title: str,
    color: discord.Color,
    field_builder,
    empty_msg: str = "Nothing found.",
    per_page: int = 10,
) -> List[discord.Embed]:
    """Splits a list of items across multiple embeds if needed (Discord max 25 fields)."""
    if not items:
        embed = discord.Embed(title=title, description=empty_msg, color=color)
        return [embed]

    embeds: List[discord.Embed] = []
    for page_start in range(0, len(items), per_page):
        page_items = items[page_start : page_start + per_page]
        embed = discord.Embed(title=title, color=color)
        for item in page_items:
            name, value = field_builder(item)
            embed.add_field(name=name[:256], value=value[:1024], inline=False)
        page_num = page_start // per_page + 1
        total_pages = (len(items) - 1) // per_page + 1
        if total_pages > 1:
            embed.set_footer(
                text=f"Page {page_num}/{total_pages} • {len(items)} total item(s)"
            )
        else:
            embed.set_footer(text=f"{len(items)} item(s)")
        embeds.append(embed)
    return embeds


# ---------------------------------------------------------------------------
# Command registration
# ---------------------------------------------------------------------------
def register_commands(
    tree: app_commands.CommandTree,
    synchronizer,
    guild_obj: discord.Object | None = None,
):

    # ========================= /ping =========================
    @tree.command(name="ping", description="Check bot latency.", guild=guild_obj)
    async def ping_command(interaction: discord.Interaction):
        latency_ms = round(interaction.client.latency * 1000)
        embed = discord.Embed(
            title="🏓 Pong!",
            description=f"Gateway Latency: `{latency_ms} ms`",
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    # ========================= /status =========================
    @tree.command(
        name="status",
        description="Display system and authentication status.",
        guild=guild_obj,
    )
    async def status_command(interaction: discord.Interaction):
        uptime_seconds = int(time.time() - START_TIME)
        hours, rem = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(rem, 60)

        latency_ms = round(interaction.client.latency * 1000)
        channel = interaction.client.get_channel(config.DISCORD_NOTIFICATION_CHANNEL_ID)
        channel_status = (
            f"<#{config.DISCORD_NOTIFICATION_CHANNEL_ID}>" if channel else "`Unreachable`"
        )

        session_valid = getattr(synchronizer, "session_valid", None)
        if session_valid is True:
            session_status = "🟢 Valid (last check)"
        elif session_valid is False:
            session_status = "🔴 Expired — run login"
        elif SESSION_STORAGE_PATH.exists():
            session_status = "⚪ File present (not verified yet)"
        else:
            session_status = "🔴 Not logged in"

        if SESSION_STORAGE_PATH.exists():
            modified = datetime.fromtimestamp(SESSION_STORAGE_PATH.stat().st_mtime)
            age_minutes = int((datetime.now() - modified).total_seconds() // 60)
            session_age = f"{age_minutes} min ago"
        else:
            session_age = "never"

        last_result = getattr(synchronizer, "last_result", None) or {}
        last_sync_status = last_result.get("status", "not run yet")
        last_sync_counts = (
            f"{last_result.get('new_announcements', 0)} new announcements, "
            f"{last_result.get('new_activities', 0)} new activities"
            if last_result
            else "—"
        )

        embed = discord.Embed(
            title="🤖 Academic Assistant — Status", color=discord.Color.blue()
        )
        embed.add_field(name="Bot", value="🟢 Online", inline=True)
        embed.add_field(name="Latency", value=f"`{latency_ms} ms`", inline=True)
        embed.add_field(name="Uptime", value=f"{hours}h {minutes}m {seconds}s", inline=True)
        embed.add_field(name="General Feed", value=channel_status, inline=True)
        embed.add_field(name="Blackboard", value=f"`{config.BLACKBOARD_URL}`", inline=True)
        embed.add_field(name="Session", value=session_status, inline=True)
        embed.add_field(name="Session File", value=session_age, inline=True)
        embed.add_field(
            name="Polling", value=f"Every `{config.SYNC_INTERVAL_MINUTES}` min", inline=True
        )
        embed.add_field(name="Timezone", value=f"`{config.TIMEZONE}`", inline=True)
        embed.add_field(
            name="Last Sync",
            value=f"`{last_sync_status}`\n{last_sync_counts}",
            inline=False,
        )

        routing_summary = (
            f"• **Thesis 1:** <#{config.CHANNEL_THESIS_1}>\n"
            f"• **Circuits Lab:** <#{config.CHANNEL_CIRCUITS_LAB}>\n"
            f"• **Circuits Lecture:** <#{config.CHANNEL_CIRCUITS_LECTURE}>\n"
            f"• **Feedback & Control:** <#{config.CHANNEL_FEEDBACK_CONTROL}>\n"
            f"• **General Announcements:** <#{config.DISCORD_NOTIFICATION_CHANNEL_ID}>"
        )
        embed.add_field(name="📂 Category Channels", value=routing_summary, inline=False)
        await interaction.response.send_message(embed=embed)

    # ========================= /sync =========================
    @tree.command(
        name="sync",
        description="Trigger an immediate Blackboard synchronization.",
        guild=guild_obj,
    )
    async def sync_command(interaction: discord.Interaction):
        embed_starting = discord.Embed(
            title="🔄 Blackboard Sync Started",
            description=(
                "Scraping announcements and coursework across all enrolled courses in the background.\n"
                "This typically takes ~30–45 seconds. I will update this message as soon as it's finished!"
            ),
            color=discord.Color.yellow(),
        )
        await interaction.response.send_message(embed=embed_starting)

        async def _do_sync():
            try:
                summary = await synchronizer.run_sync(notify=True)
                status = summary.get("status", "ok")

                if status == "error":
                    embed_done = discord.Embed(
                        title="❌ Sync Failed",
                        description=summary.get("message", "Unknown error."),
                        color=discord.Color.red(),
                    )
                elif status == "skipped":
                    embed_done = discord.Embed(
                        title="⏳ Sync Already Running",
                        description=summary.get(
                            "message", "A synchronization pass is already in progress."
                        ),
                        color=discord.Color.yellow(),
                    )
                else:
                    is_partial = status == "partial"
                    embed_done = discord.Embed(
                        title=(
                            "⚠️ Sync Completed with Errors"
                            if is_partial
                            else "✅ Sync Complete"
                        ),
                        color=discord.Color.orange() if is_partial else discord.Color.green(),
                    )
                    embed_done.add_field(
                        name="📚 Courses",
                        value=str(summary.get("courses_discovered", 0)),
                        inline=True,
                    )
                    embed_done.add_field(
                        name="📢 New Announcements",
                        value=str(summary.get("new_announcements", 0)),
                        inline=True,
                    )
                    embed_done.add_field(
                        name="📢 Updated",
                        value=str(summary.get("updated_announcements", 0)),
                        inline=True,
                    )
                    embed_done.add_field(
                        name="📝 New Activities",
                        value=str(summary.get("new_activities", 0)),
                        inline=True,
                    )
                    embed_done.add_field(
                        name="⏰ Deadline Changes",
                        value=str(summary.get("deadline_changes", 0)),
                        inline=True,
                    )
                    errors = summary.get("errors") or []
                    if errors:
                        embed_done.add_field(
                            name=f"Errors ({len(errors)})",
                            value="\n".join(f"• {e[:180]}" for e in errors[:3]),
                            inline=False,
                        )
                    elif summary.get("message"):
                        embed_done.description = summary["message"]

                await interaction.edit_original_response(embed=embed_done)
            except Exception as e:
                embed_err = discord.Embed(
                    title="❌ Sync Failed",
                    description=f"Error encountered: `{e}`",
                    color=discord.Color.red(),
                )
                await interaction.edit_original_response(embed=embed_err)

        # Keep a strong reference to the task so it is not garbage-collected.
        synchronizer.sync_task = asyncio.create_task(_do_sync())

    # ========================= /courses =========================
    @tree.command(
        name="courses", description="List all enrolled courses.", guild=guild_obj
    )
    async def courses_command(interaction: discord.Interaction):
        courses = await db_manager.get_all_courses()
        if not courses:
            await interaction.response.send_message(
                "ℹ️ No courses yet. Run `/sync` first!", ephemeral=True
            )
            return

        terms = sorted({c["term"] for c in courses if c.get("term")})
        suffix = f" ({', '.join(terms)})" if terms else ""
        title = f"📚 Enrolled Courses{suffix}"

        def build_field(c):
            icon = "🟢" if c["status"] == "Open" else "🔒"
            return (
                f"{icon} {c['name']}",
                (
                    f"**ID:** `{c['id']}`\n"
                    f"**Instructor:** {c['instructor'] or 'N/A'}\n"
                    f"**Term:** {c['term'] or 'N/A'} | **Status:** {c['status']}\n"
                    f"**Last Synced:** {c['last_synced_at'] or 'Never'}"
                ),
            )

        embeds = _paginate_embed_fields(
            courses, title, discord.Color.teal(), build_field
        )
        await interaction.response.send_message(embed=embeds[0])
        for embed in embeds[1:]:
            await interaction.followup.send(embed=embed)

    # ========================= /course =========================
    @tree.command(
        name="course", description="Detailed view of a specific course.", guild=guild_obj
    )
    @app_commands.describe(course="Select a course")
    @app_commands.autocomplete(course=course_autocomplete)
    async def course_command(interaction: discord.Interaction, course: str):
        c = await db_manager.get_course_by_id(course)
        if not c:
            await interaction.response.send_message(
                f"❌ Course `{course}` not found.", ephemeral=True
            )
            return

        anns = await db_manager.get_recent_announcements(course_id=course, limit=5)
        acts = await db_manager.get_activities(course_id=course)

        icon = "🟢" if c["status"] == "Open" else "🔒"
        embed = discord.Embed(
            title=f"{icon} {c['name']}",
            description=(
                f"**ID:** `{c['id']}`\n"
                f"**Instructor:** {c['instructor'] or 'N/A'}\n"
                f"**Term:** {c['term'] or 'N/A'} | **Status:** {c['status']}"
            ),
            color=discord.Color.teal(),
        )

        if anns:
            ann_text = "\n".join(
                f"• **{a['title']}**\n  {(a['content'] or '')[:120]}"
                f"{'...' if len(a['content'] or '') > 120 else ''}"
                for a in anns
            )
            embed.add_field(
                name=f"📢 Announcements ({len(anns)})", value=ann_text[:1024], inline=False
            )
        else:
            embed.add_field(name="📢 Announcements", value="No announcements.", inline=False)

        if acts:
            act_text = "\n".join(
                f"• **{a['title']}** — Due: `{a['due_date'] or 'None'}` | Status: `{a['status']}`"
                for a in acts
            )
            embed.add_field(
                name=f"📝 Activities ({len(acts)})", value=act_text[:1024], inline=False
            )
        else:
            embed.add_field(name="📝 Activities", value="No activities.", inline=False)

        embed.set_footer(text=f"Last synced: {c['last_synced_at'] or 'Never'}")
        await interaction.response.send_message(embed=embed)

    # ========================= /announcements =========================
    @tree.command(
        name="announcements",
        description="View announcements (past 3 days or all).",
        guild=guild_obj,
    )
    @app_commands.describe(
        scope="Choose 'recent' (past 3 days) or 'all' (all announcements)",
        course="Filter by course (optional if inside course channel)",
    )
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="📢 Recent (Past 3 Days) [Default]", value="recent"),
            app_commands.Choice(name="📚 All Announcements", value="all"),
        ]
    )
    @app_commands.autocomplete(course=course_autocomplete)
    async def announcements_command(
        interaction: discord.Interaction,
        scope: Optional[app_commands.Choice[str]] = None,
        course: Optional[str] = None,
    ):
        channel_scope = _channel_scope(interaction)
        scope_mode = scope.value if scope else "recent"
        limit = 50 if scope_mode == "all" else 20
        items = await db_manager.get_recent_announcements(
            course_id=course,
            limit=limit,
            course_prefix=None if course else (channel_scope or {}).get("prefix"),
            exclude_prefix=None if course else (channel_scope or {}).get("exclude"),
        )

        now = now_local()
        three_days_ago_date = (now - timedelta(days=3)).date()

        if scope_mode == "recent":
            filtered = []
            for a in items:
                det_str = a.get("first_detected_at") or a.get("posted_date")
                det_dt = parse_db_timestamp(det_str) or parse_date(det_str)
                if det_dt:
                    if det_dt.date() >= three_days_ago_date:
                        filtered.append(a)
                else:
                    filtered.append(a)
            items = filtered
            title = "📢 Recent Announcements (Past 3 Days)"
            empty_msg = (
                "No announcements in the past 3 days for this subject. "
                "Use `/announcements scope: All Announcements` to view previous posts."
            )
        else:
            title = "📚 All Course Announcements"
            empty_msg = "No announcements found for this subject."

        def build_field(a):
            preview = (a["content"] or "")[:200]
            if len(a["content"] or "") > 200:
                preview += "..."
            meta = []
            if a.get("author"):
                meta.append(f"By {a['author']}")
            if a.get("posted_date"):
                meta.append(f"Posted {a['posted_date']}")
            meta_text = f"\n*{' • '.join(meta)}*" if meta else ""
            return (
                f"📢 {a['title']} — {a['course_name']}",
                (
                    f"{preview or 'No content.'}{meta_text}\n"
                    f"*Detected: {parse_db_timestamp(a['first_detected_at']) or a['first_detected_at']}*"
                ),
            )

        embeds = _paginate_embed_fields(
            items, title, discord.Color.blue(), build_field, empty_msg=empty_msg
        )
        await interaction.response.send_message(embed=embeds[0])
        for embed in embeds[1:]:
            await interaction.followup.send(embed=embed)

    # ========================= /activities =========================
    @tree.command(
        name="activities", description="View all coursework activities.", guild=guild_obj
    )
    @app_commands.describe(course="Filter by course (optional)")
    @app_commands.autocomplete(course=course_autocomplete)
    async def activities_command(
        interaction: discord.Interaction, course: Optional[str] = None
    ):
        channel_scope = _channel_scope(interaction)
        items = await db_manager.get_activities(
            course_id=course,
            course_prefix=None if course else (channel_scope or {}).get("prefix"),
            exclude_prefix=None if course else (channel_scope or {}).get("exclude"),
        )

        def build_field(a):
            return (
                f"📝 {a['title']}",
                (
                    f"**Course:** {a['course_name']}\n"
                    f"**Due:** `{a['due_date'] or 'No deadline'}` | "
                    f"**Status:** `{a['status']}` | "
                    f"**Points:** `{a['points'] or 'N/A'}`"
                ),
            )

        embeds = _paginate_embed_fields(
            items,
            "📝 All Activities & Assignments",
            discord.Color.purple(),
            build_field,
            empty_msg="No activities found for this subject.",
        )
        await interaction.response.send_message(embed=embeds[0])
        for embed in embeds[1:]:
            await interaction.followup.send(embed=embed)

    # ========================= /pending =========================
    @tree.command(
        name="pending",
        description="Show unsubmitted activities (nearest 3-day window or all).",
        guild=guild_obj,
    )
    @app_commands.describe(
        scope="Choose 'nearest' (due within 3 days) or 'all' (all pending in course)",
        course="Filter by course (optional if inside course channel)",
    )
    @app_commands.choices(
        scope=[
            app_commands.Choice(
                name="⏰ Nearest Due (Next 3 Days) [Default]", value="nearest"
            ),
            app_commands.Choice(name="📋 All Pending Coursework", value="all"),
        ]
    )
    @app_commands.autocomplete(course=course_autocomplete)
    async def pending_command(
        interaction: discord.Interaction,
        scope: Optional[app_commands.Choice[str]] = None,
        course: Optional[str] = None,
    ):
        channel_scope = _channel_scope(interaction)
        items = await db_manager.get_pending_activities(
            course_id=course,
            course_prefix=None if course else (channel_scope or {}).get("prefix"),
            exclude_prefix=None if course else (channel_scope or {}).get("exclude"),
        )

        scope_mode = scope.value if scope else "nearest"
        now = now_local()
        start_date = (now - timedelta(days=3)).date()
        end_date = (now + timedelta(days=3)).date()

        if scope_mode == "nearest":
            filtered_items = []
            for a in items:
                dd = a.get("due_date")
                if not dd or dd.lower() == "ongoing":
                    continue
                due_dt = parse_date(dd)
                if due_dt:
                    if start_date <= due_dt.date() <= end_date:
                        a["_due_dt"] = due_dt
                        filtered_items.append(a)
            filtered_items.sort(
                key=lambda x: (x.get("_due_dt", datetime.max), x.get("title", "").lower())
            )
            items = filtered_items
            title = "⏰ Pending Activities (Due in Next 3 Days)"
            empty_msg = (
                "🎉 No pending activities due in the next 3 days for this subject! "
                "Use `/pending scope: All Pending Coursework` to view all."
            )
        else:
            title = "📋 All Pending Activities (Unsubmitted)"
            empty_msg = "🎉 Nothing pending for this subject — you're all caught up!"

        def build_field(a):
            return (
                f"⚠️ {a['title']}",
                (
                    f"**Course:** {a['course_name']}\n"
                    f"**Due:** `{a['due_date'] or 'No deadline'}` | "
                    f"**Status:** `{a['status']}`"
                ),
            )

        embeds = _paginate_embed_fields(
            items, title, discord.Color.red(), build_field, empty_msg=empty_msg
        )
        await interaction.response.send_message(embed=embeds[0])
        for embed in embeds[1:]:
            await interaction.followup.send(embed=embed)

    # ========================= /deadlines =========================
    @tree.command(
        name="deadlines", description="View all deadlines chronologically.", guild=guild_obj
    )
    async def deadlines_command(interaction: discord.Interaction):
        channel_scope = _channel_scope(interaction)
        items = await db_manager.get_activities(
            course_prefix=(channel_scope or {}).get("prefix"),
            exclude_prefix=(channel_scope or {}).get("exclude"),
        )
        dated = [
            a
            for a in items
            if a["due_date"] and a["due_date"].lower() != "ongoing"
        ]

        def build_field(a):
            return (
                f"📅 {a['due_date']} — {a['title']}",
                f"**Course:** {a['course_name']} | **Status:** `{a['status']}`",
            )

        embeds = _paginate_embed_fields(
            dated,
            "📅 All Deadlines (Chronological)",
            discord.Color.orange(),
            build_field,
            empty_msg="No dated deadlines found for this subject.",
        )
        await interaction.response.send_message(embed=embeds[0])
        for embed in embeds[1:]:
            await interaction.followup.send(embed=embed)

    # ========================= /upcoming =========================
    @tree.command(
        name="upcoming",
        description="Assignments due within the next 7 days.",
        guild=guild_obj,
    )
    async def upcoming_command(interaction: discord.Interaction):
        channel_scope = _channel_scope(interaction)
        items = await db_manager.get_activities(
            course_prefix=(channel_scope or {}).get("prefix"),
            exclude_prefix=(channel_scope or {}).get("exclude"),
        )
        now = now_local()
        end_date = (now + timedelta(days=7)).date()

        upcoming = []
        for a in items:
            dd = a.get("due_date")
            if not dd or dd.lower() == "ongoing":
                continue
            due_dt = parse_date(dd)
            if due_dt and now.date() <= due_dt.date() <= end_date:
                a["_due_dt"] = due_dt
                upcoming.append(a)

        upcoming.sort(
            key=lambda x: (x.get("_due_dt", datetime.max), x.get("title", "").lower())
        )

        def build_field(a):
            days_left = (a["_due_dt"].date() - now.date()).days
            if days_left == 0:
                urgency = "🔴"
                time_label = "DUE TODAY"
            elif days_left <= 1:
                urgency = "🔴"
                time_label = "Tomorrow"
            elif days_left <= 3:
                urgency = "🟡"
                time_label = f"In {days_left} days"
            else:
                urgency = "🟢"
                time_label = f"In {days_left} days"

            return (
                f"{urgency} {a['due_date']} — {a['title']}",
                (
                    f"**Course:** {a['course_name']}\n"
                    f"**Status:** `{a['status']}` | "
                    f"**Timeline:** `{time_label}`"
                ),
            )

        embeds = _paginate_embed_fields(
            upcoming,
            "📆 Upcoming (Next 7 Days)",
            discord.Color.gold(),
            build_field,
            empty_msg="🎉 Nothing due in the next 7 days for this subject!",
        )
        await interaction.response.send_message(embed=embeds[0])
        for embed in embeds[1:]:
            await interaction.followup.send(embed=embed)

    # ========================= /overdue =========================
    @tree.command(
        name="overdue",
        description="Past-due activities not yet submitted.",
        guild=guild_obj,
    )
    async def overdue_command(interaction: discord.Interaction):
        items = await db_manager.get_overdue_activities()
        channel_scope = _channel_scope(interaction)
        if channel_scope:
            items = [
                a for a in items if _matches_scope(a.get("course_id"), channel_scope)
            ]

        def build_field(a):
            return (
                f"🚨 {a['title']}",
                (
                    f"**Course:** {a['course_name']}\n"
                    f"**Was due:** `{a['due_date']}` | "
                    f"**Status:** `{a['status']}`"
                ),
            )

        embeds = _paginate_embed_fields(
            items,
            "🚨 Overdue Activities",
            discord.Color.dark_red(),
            build_field,
            empty_msg="✅ No overdue items — you're on track!",
        )
        await interaction.response.send_message(embed=embeds[0])
        for embed in embeds[1:]:
            await interaction.followup.send(embed=embed)

    # ========================= /summary =========================
    @tree.command(
        name="summary", description="Consolidated academic overview.", guild=guild_obj
    )
    async def summary_command(interaction: discord.Interaction):
        courses = await db_manager.get_all_courses()
        activities = await db_manager.get_activities()
        announcements = await db_manager.get_recent_announcements(limit=5)
        pending = await db_manager.get_pending_activities()
        overdue = await db_manager.get_overdue_activities()

        open_count = sum(1 for c in courses if c["status"] == "Open")

        embed = discord.Embed(
            title="📊 Academic Summary Dashboard", color=discord.Color.dark_blue()
        )
        embed.add_field(
            name="📚 Courses",
            value=f"{len(courses)} enrolled ({open_count} open)",
            inline=True,
        )
        embed.add_field(name="📝 Total Activities", value=str(len(activities)), inline=True)
        embed.add_field(name="⚠️ Pending", value=str(len(pending)), inline=True)
        embed.add_field(name="🚨 Overdue", value=str(len(overdue)), inline=True)
        embed.add_field(
            name="📢 Announcements", value=str(len(announcements)), inline=True
        )

        if pending:
            pending_sorted = sorted(
                pending,
                key=lambda p: (
                    due_sort_datetime(p.get("due_date")),
                    (p.get("title") or "").lower(),
                ),
            )
            pending_text = "\n".join(
                f"• **{p['title']}** — Due: `{p['due_date'] or 'None'}` ({p['course_name']})"
                for p in pending_sorted[:5]
            )
            embed.add_field(name="⚠️ Pending Items", value=pending_text[:1024], inline=False)

        if announcements:
            ann_text = "\n".join(
                f"• **{a['title']}** (*{a['course_name']}*)" for a in announcements[:3]
            )
            embed.add_field(
                name="📢 Latest Announcements", value=ann_text[:1024], inline=False
            )

        await interaction.response.send_message(embed=embed)

    # ========================= /clear =========================
    @tree.command(
        name="clear",
        description="Clear bot messages or purge the channel.",
        guild=guild_obj,
    )
    @app_commands.describe(
        amount="Number of recent messages to check (1-100, default: 50)",
        target="Choose whether to delete only the bot's messages or the entire chat",
    )
    @app_commands.choices(
        target=[
            app_commands.Choice(name="🤖 Bot Messages Only (Default)", value="bot_only"),
            app_commands.Choice(name="🧹 Entire Chat (All Messages)", value="all"),
        ]
    )
    async def clear_command(
        interaction: discord.Interaction,
        amount: Optional[int] = 50,
        target: Optional[app_commands.Choice[str]] = None,
    ):
        target_mode = target.value if target else "bot_only"
        limit = max(1, min(100, amount or 50))

        # Defer ephemerally so the user gets an instant acknowledgement that only they see
        await interaction.response.defer(ephemeral=True)

        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            await interaction.followup.send(
                "❌ This command can only be used in text channels.", ephemeral=True
            )
            return

        def check_filter(msg: discord.Message) -> bool:
            if target_mode == "bot_only":
                return msg.author == interaction.client.user
            return True

        try:
            deleted = await channel.purge(limit=limit, check=check_filter)
            count = len(deleted)
            mode_desc = (
                "bot message(s)" if target_mode == "bot_only" else "message(s) from the chat"
            )
            await interaction.followup.send(
                f"🧹 Successfully cleared **{count}** {mode_desc} (scanned {limit} recent messages).",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Missing Permissions: The bot needs the **Manage Messages** permission to delete messages sent by other users. "
                "(Deleting bot's own messages should still work).",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Error while clearing messages: `{e}`", ephemeral=True
            )
