"""
Scheduled Campaign Poller

Polls the database every 60 seconds for campaigns that are scheduled
and ready to be activated (scheduled_for <= now).

Responsibilities:
    - Query scheduled campaigns
    - Activate campaigns that are due
    - Handle errors gracefully
    - Log all activations

Usage:
    worker = ScheduledCampaignPoller()
    await worker.start()
"""

import asyncio
from datetime import datetime
from typing import List

import structlog

from apps.adapters.db.postgres import get_database
from apps.adapters.db.unit_of_work import SQLAlchemyUnitOfWork
from apps.adapters.queue.rabbitmq import RabbitMQAdapter
from apps.core.config import get_settings
from apps.core.services.campaign_service import CampaignService
from apps.core.domain.campaign import Campaign


logger = structlog.get_logger(__name__)


class ScheduledCampaignPoller:
    """
    Polls for scheduled campaigns and activates them when due.
    
    Runs a background loop every 60 seconds to check for campaigns
    with status='scheduled' and scheduled_for <= now().
    """
    
    def __init__(self, poll_interval: int = 60):
        """
        Initialize poller
        
        Args:
            poll_interval: Seconds between polls (default: 60)
        """
        self.poll_interval = poll_interval
        self.settings = get_settings()
        self.running = False
        self._task = None
        
    async def start(self):
        """Start the poller"""
        self.running = True
        logger.info(
            "scheduled_campaign_poller_started",
            poll_interval=self.poll_interval,
        )
        
        try:
            await self._poll_loop()
        except asyncio.CancelledError:
            logger.info("scheduled_campaign_poller_cancelled")
            raise
        except Exception:
            logger.exception("scheduled_campaign_poller_error")
            raise
        finally:
            self.running = False
            logger.info("scheduled_campaign_poller_stopped")
    
    async def stop(self):
        """Stop the poller"""
        logger.info("scheduled_campaign_poller_stopping")
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
    
    async def _poll_loop(self):
        """Main polling loop"""
        while self.running:
            try:
                await self._poll_and_activate()
            except Exception:
                logger.exception("poll_cycle_error")
            
            # Wait for next poll interval
            await asyncio.sleep(self.poll_interval)
    
    async def _poll_and_activate(self):
        """Poll for scheduled campaigns and activate them"""
        # Get database connection
        db = get_database()
        
        async with db.session() as session:
            uow = SQLAlchemyUnitOfWork(session)
            
            # Get scheduled campaigns that are due
            now = datetime.utcnow()
            scheduled_campaigns = await uow.campaigns.get_scheduled_campaigns(
                before=now
            )
            
            if not scheduled_campaigns:
                logger.debug(
                    "no_scheduled_campaigns_due",
                    checked_at=now.isoformat(),
                )
                return
            
            logger.info(
                "found_scheduled_campaigns",
                count=len(scheduled_campaigns),
                checked_at=now.isoformat(),
            )
            
            # Activate each campaign
            for campaign in scheduled_campaigns:
                await self._activate_campaign(campaign, uow)
    
    async def _activate_campaign(
        self,
        campaign: Campaign,
        uow: SQLAlchemyUnitOfWork,
    ):
        """
        Activate a scheduled campaign
        
        Args:
            campaign: Campaign to activate
            uow: Unit of work for persistence
        """
        queue = None
        try:
            # Connect to queue
            queue = RabbitMQAdapter(url=self.settings.rabbitmq.url)
            await queue.connect()
            
            # Create service and activate
            service = CampaignService(uow, queue)
            activated = await service.activate_campaign(campaign.id)
            
            logger.info(
                "campaign_activated_by_scheduler",
                campaign_id=str(campaign.id),
                campaign_name=campaign.name,
                tenant_id=str(campaign.tenant_id),
                scheduled_for=campaign.scheduled_for.isoformat() if campaign.scheduled_for else None,
                activated_at=datetime.utcnow().isoformat(),
            )
            
        except Exception as e:
            logger.error(
                "campaign_activation_failed",
                campaign_id=str(campaign.id),
                campaign_name=campaign.name,
                error=str(e),
                exc_info=True,
            )
        finally:
            if queue:
                await queue.close()
