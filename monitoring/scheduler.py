"""
monitoring/scheduler.py — Asynchronous APScheduler integration for automated monitoring.
"""

import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

import config
from monitoring.synchronizer import BlackboardSynchronizer

logger = logging.getLogger("monitoring.scheduler")


def setup_scheduler(synchronizer: BlackboardSynchronizer) -> AsyncIOScheduler:
    """Configures and returns an AsyncIOScheduler for periodic sync."""
    scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)

    interval_minutes = max(5, config.SYNC_INTERVAL_MINUTES)
    logger.info("Setting up background sync job every %d minutes (Timezone: %s)", interval_minutes, config.TIMEZONE)

    scheduler.add_job(
        synchronizer.run_sync,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id="blackboard_sync_job",
        name="Blackboard Ultra Background Sync",
        replace_existing=True,
    )

    return scheduler
