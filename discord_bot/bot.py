"""
discord_bot/bot.py — Discord Client initialization, Synchronizer wiring, and Command sync.
"""

import logging
import discord
from discord import app_commands
import config
from database.db import db_manager
from discord_bot.commands import register_commands
from monitoring.synchronizer import BlackboardSynchronizer
from monitoring.scheduler import setup_scheduler

logger = logging.getLogger("discord_bot")


class AcademicAssistantBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        allowed_mentions = discord.AllowedMentions(everyone=True, roles=True, users=True)

        super().__init__(intents=intents, allowed_mentions=allowed_mentions)
        self.tree = app_commands.CommandTree(self)
        self.synchronizer = BlackboardSynchronizer(discord_client=self)
        self.scheduler = None

    async def setup_hook(self):
        """Called automatically before the bot logs in."""
        # Initialize SQLite database
        await db_manager.init_db()

        guild_obj = discord.Object(id=config.DISCORD_GUILD_ID) if config.DISCORD_GUILD_ID else None

        # Register all slash commands passing the synchronizer instance
        register_commands(self.tree, synchronizer=self.synchronizer, guild_obj=guild_obj)

        if guild_obj:
            logger.info("Syncing slash commands to Guild ID: %s...", config.DISCORD_GUILD_ID)
            await self.tree.sync(guild=guild_obj)
            logger.info("Guild commands synced successfully!")
        else:
            await self.tree.sync()

        # Start periodic background synchronization scheduler
        self.scheduler = setup_scheduler(self.synchronizer)
        self.scheduler.start()
        logger.info("Automated background scheduler started successfully!")

    async def on_ready(self):
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        logger.info("Connected to %d guild(s)", len(self.guilds))
        for g in self.guilds:
            logger.info(" - %s (ID: %s)", g.name, g.id)


def create_bot() -> AcademicAssistantBot:
    return AcademicAssistantBot()
