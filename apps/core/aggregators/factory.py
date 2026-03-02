"""
Aggregator Factory

Creates the appropriate aggregator adapter based on application configuration.
Supports rcssms.in and mock adapter for testing.
"""

import logging
from apps.core.config import get_settings, Settings
from apps.core.ports.aggregator import AggregatorPort
from apps.adapters.aggregators.rcssms_adapter import RcsSmsAdapter
from apps.adapters.aggregators.mock_adapter import MockAdapter

logger = logging.getLogger(__name__)


class AggregatorFactory:
    """Factory for creating aggregator adapters"""

    @staticmethod
    def create_aggregator(settings: Settings = None) -> AggregatorPort:
        """
        Create aggregator based on settings.

        Args:
            settings: Application settings. If None, gets cached settings.

        Returns:
            Aggregator adapter (Mock or RcsSms)
        """
        if settings is None:
            settings = get_settings()

        use_mock = settings.use_mock_aggregator
        if settings.rcssms and settings.rcssms.use_mock:
            use_mock = True

        if use_mock:
            logger.info("🧪 Creating MOCK Aggregator Adapter")
            return MockAdapter(
                success_rate=0.95,
                delay=0.1,
                rcs_capable_rate=0.8,
            )

        if settings.rcssms:
            logger.info("📡 Creating rcssms.in Aggregator Adapter")
            return RcsSmsAdapter(
                username=settings.rcssms.username,
                password=settings.rcssms.password,
                rcs_id=settings.rcssms.rcs_id,
                client_secret=settings.rcssms.client_secret,
                use_bearer=settings.rcssms.use_bearer,
                timeout=settings.rcssms.timeout,
            )

        logger.error("❌ No aggregator configured and MOCK is disabled")
        raise ValueError(
            "No aggregator configured. Set RCS_USERNAME/RCS_PASSWORD/RCS_ID in .env "
            "or set USE_MOCK_AGGREGATOR=true for testing."
        )
