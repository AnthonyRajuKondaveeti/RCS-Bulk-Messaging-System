"""
Aggregator Factory

Creates the appropriate aggregator adapter(s) based on application configuration.

Two adapters are managed:
  - RCS adapter   (rcssms.in)      — primary channel
  - SMS adapter   (smsidea.co.in)  — fallback channel

The factory exposes:
  create_aggregator()   -> RCS adapter (primary, used by dispatcher)
  create_sms_adapter()  -> SMS adapter (fallback, used by SMSFallbackWorker)

Supports mock adapters for testing — set USE_MOCK_AGGREGATOR=true in .env.
"""

import logging
from typing import Optional

from apps.core.config import get_settings, Settings
from apps.core.ports.aggregator import AggregatorPort
from apps.adapters.aggregators.rcssms_adapter import RcsSmsAdapter
from apps.adapters.aggregators.mock_adapter import MockAdapter

logger = logging.getLogger(__name__)


class AggregatorFactory:
    """Factory for creating aggregator adapters."""

    @staticmethod
    def create_aggregator(settings: Settings = None) -> AggregatorPort:
        """
        Create the primary RCS aggregator (rcssms.in).

        Used by: MessageDispatcher, DeliveryService

        Args:
            settings: Application settings. If None, uses cached settings.

        Returns:
            RcsSmsAdapter or MockAdapter
        """
        if settings is None:
            settings = get_settings()

        use_mock = settings.use_mock_aggregator
        if settings.rcssms and settings.rcssms.use_mock:
            use_mock = True

        if use_mock:
            logger.info("🧪 Creating MOCK RCS Aggregator Adapter")
            return MockAdapter(
                success_rate=0.95,
                delay=0.1,
                rcs_capable_rate=0.8,
            )

        if settings.rcssms:
            logger.info("📡 Creating rcssms.in RCS Aggregator Adapter")
            return RcsSmsAdapter(
                username=settings.rcssms.username,
                password=settings.rcssms.password,
                rcs_id=settings.rcssms.rcs_id,
                client_secret=settings.rcssms.client_secret,
                use_bearer=settings.rcssms.use_bearer,
                timeout=settings.rcssms.timeout,
                send_url=settings.rcssms.send_url,
                token_url=settings.rcssms.token_url,
                template_url=settings.rcssms.template_url,
            )

        logger.error("❌ No RCS aggregator configured and MOCK is disabled")
        raise ValueError(
            "No RCS aggregator configured. Set RCS_USERNAME/RCS_PASSWORD/RCS_ID "
            "in .env or set USE_MOCK_AGGREGATOR=true for testing."
        )

    @staticmethod
    def create_sms_adapter(settings: Settings = None) -> Optional[AggregatorPort]:
        """
        Create the SMS fallback adapter (smsidea.co.in).

        Used by: SMSFallbackWorker

        Returns None when SMS is not configured (fallback disabled or mock mode).
        Callers should handle None gracefully — log a warning and skip the send.

        Args:
            settings: Application settings. If None, uses cached settings.

        Returns:
            SmsIdeaAdapter, MockAdapter (in mock mode), or None
        """
        if settings is None:
            settings = get_settings()

        use_mock = settings.use_mock_aggregator
        if settings.rcssms and settings.rcssms.use_mock:
            use_mock = True

        if use_mock:
            logger.info("🧪 Creating MOCK SMS Fallback Adapter")
            return MockAdapter(
                success_rate=0.95,
                delay=0.1,
                rcs_capable_rate=0.0,
            )

        if settings.smsidea:
            from apps.adapters.aggregators.smsidea_adapter import SmsIdeaAdapter

            logger.info("📱 Creating smsidea.co.in SMS Fallback Adapter")
            return SmsIdeaAdapter(
                username=settings.smsidea.username,
                password=settings.smsidea.password,
                sender_id=settings.smsidea.sender_id,
                peid=settings.smsidea.peid,
                timeout=settings.smsidea.timeout,
                send_url=settings.smsidea.send_url,
                balance_url=settings.smsidea.balance_url,
            )

        logger.warning(
            "⚠️  SMS fallback adapter not configured — "
            "set SMS_USERNAME / SMS_PASSWORD / SMS_SENDER_ID in .env to enable. "
            "SMS fallback will be skipped."
        )
        return None
