"""
Aggregator Factory

Creates appropriate aggregator adapters (Gupshup, Mock, etc.)
based on application configuration.
"""

import logging
from apps.core.config import get_settings, Settings
from apps.core.ports.aggregator import AggregatorPort
from apps.adapters.aggregators.gupshup_adapter import GupshupAdapter
from apps.adapters.aggregators.mock_adapter import MockAdapter

logger = logging.getLogger(__name__)

class AggregatorFactory:
    """Factory for creating aggregator adapters"""
    
    @staticmethod
    def create_aggregator(settings: Settings = None) -> AggregatorPort:
        """
        Create aggregator based on settings
        
        Args:
            settings: Application settings. If None, gets cached settings.
            
        Returns:
            Aggregator adapter (Mock or Gupshup)
        """
        if settings is None:
            settings = get_settings()
            
        # Check if mock mode is globally enabled or specifically for Gupshup
        use_mock = settings.use_mock_aggregator
        if settings.gupshup and settings.gupshup.use_mock:
            use_mock = True
            
        if use_mock:
            logger.info("🧪 Creating MOCK Aggregator Adapter")
            return MockAdapter(
                success_rate=0.95,
                delay=0.1,
                rcs_capable_rate=0.8
            )
        
        if settings.gupshup:
            logger.info("📡 Creating Gupshup Aggregator Adapter")
            return GupshupAdapter(
                api_key=settings.gupshup.api_key,
                app_name=settings.gupshup.app_name,
                webhook_secret=settings.gupshup.webhook_secret,
                base_url=settings.gupshup.base_url,
            )
        
        logger.error("❌ No aggregator configured and MOCK is disabled")
        raise ValueError("No aggregator configured in settings")
