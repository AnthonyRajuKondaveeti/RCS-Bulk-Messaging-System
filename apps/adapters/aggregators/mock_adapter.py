"""
Mock Aggregator Adapter

In-memory adapter for local development and testing.
Simulates rcssms.in behaviour with configurable success/failure rates.

Usage (via .env):
    USE_MOCK_AGGREGATOR=true
"""

import asyncio
import logging
import random
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from apps.core.ports.aggregator import (
    AggregatorException,
    AggregatorPort,
    CapabilityCheckResult,
    DeliveryStatus,
    RateLimitException,
    SendMessageRequest,
    SendMessageResponse,
)

logger = logging.getLogger(__name__)


class MockAdapter(AggregatorPort):
    """
    Mock aggregator for development and testing.

    Simulates send latency, configurable success/failure rates, and
    RCS capability checks without making real HTTP calls.

    Args:
        success_rate:      Fraction of sends that succeed (0.0–1.0).
        delay:             Simulated network latency in seconds.
        rcs_capable_rate:  Fraction of numbers reported as RCS-capable.
    """

    def __init__(
        self,
        success_rate: float = 0.95,
        delay: float = 0.1,
        rcs_capable_rate: float = 0.8,
    ):
        self.success_rate = success_rate
        self.delay = delay
        self.rcs_capable_rate = rcs_capable_rate
        self._sent_messages: List[Dict[str, Any]] = []
    
    async def connect(self) -> None:
        """Connect to aggregator (no-op for mock)"""
        logger.info("MockAdapter: connect called (no-op)")

    # ------------------------------------------------------------------
    # AggregatorPort interface
    # ------------------------------------------------------------------

    async def send_rcs_message(
        self,
        request: SendMessageRequest,
    ) -> SendMessageResponse:
        await asyncio.sleep(self.delay)

        if random.random() > self.success_rate:
            logger.warning(
                "MockAdapter: simulated RCS send failure",
                extra={"message_id": str(request.message_id)},
            )
            return SendMessageResponse(
                success=False,
                error_code="MOCK_FAILURE",
                error_message="Simulated delivery failure",
            )

        external_id = f"mock-rcs-{uuid4().hex[:12]}"
        self._sent_messages.append(
            {
                "external_id": external_id,
                "message_id": str(request.message_id),
                "channel": "rcs",
                "recipient": request.recipient_phone,
                "template_id": (request.metadata or {}).get("template_id"),
                "sent_at": datetime.utcnow().isoformat(),
            }
        )

        logger.info(
            "MockAdapter: RCS message sent",
            extra={
                "message_id": str(request.message_id),
                "external_id": external_id,
                "recipient": request.recipient_phone,
            },
        )

        return SendMessageResponse(success=True, external_id=external_id)

    async def send_sms_message(
        self,
        request: SendMessageRequest,
    ) -> SendMessageResponse:
        await asyncio.sleep(self.delay)

        if random.random() > self.success_rate:
            logger.warning(
                "MockAdapter: simulated SMS send failure",
                extra={"message_id": str(request.message_id)},
            )
            return SendMessageResponse(
                success=False,
                error_code="MOCK_FAILURE",
                error_message="Simulated SMS delivery failure",
            )

        external_id = f"mock-sms-{uuid4().hex[:12]}"
        self._sent_messages.append(
            {
                "external_id": external_id,
                "message_id": str(request.message_id),
                "channel": "sms",
                "recipient": request.recipient_phone,
                "sent_at": datetime.utcnow().isoformat(),
            }
        )

        logger.info(
            "MockAdapter: SMS message sent",
            extra={
                "message_id": str(request.message_id),
                "external_id": external_id,
                "recipient": request.recipient_phone,
            },
        )

        return SendMessageResponse(success=True, external_id=external_id)

    async def check_rcs_capability(
        self,
        phone_numbers: List[str],
    ) -> List[CapabilityCheckResult]:
        await asyncio.sleep(self.delay / 2)
        return [
            CapabilityCheckResult(
                phone_number=phone,
                rcs_enabled=random.random() < self.rcs_capable_rate,
                last_checked=datetime.utcnow(),
                features=["rich_cards", "suggestions"],
            )
            for phone in phone_numbers
        ]

    async def get_delivery_status(
        self,
        external_id: str,
    ) -> Optional[DeliveryStatus]:
        for msg in self._sent_messages:
            if msg["external_id"] == external_id:
                return DeliveryStatus(
                    message_id=external_id,
                    external_id=external_id,
                    status="delivered",
                    timestamp=datetime.utcnow(),
                )
        return None

    async def handle_webhook(
        self,
        payload: Dict[str, Any],
        headers: Dict[str, str],
    ) -> Optional[DeliveryStatus]:
        external_id = payload.get("msgid") or payload.get("external_id")
        raw_status = (payload.get("status") or "DELIVERED").upper()
        status_map = {
            "DELIVERED": "delivered",
            "SENT": "sent",
            "FAILED": "failed",
            "READ": "read",
        }
        return DeliveryStatus(
            message_id=external_id,
            external_id=external_id,
            status=status_map.get(raw_status, "unknown"),
            timestamp=datetime.utcnow(),
            metadata=payload,
        )

    def validate_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> bool:
        return True  # Mock always accepts

    async def get_account_balance(self) -> Dict[str, Any]:
        return {
            "balance": 99999.0,
            "currency": "INR",
            "credits": 1000000,
            "note": "Mock balance — not real.",
        }

    def get_name(self) -> str:
        return "mock"

    async def close(self) -> None:
        logger.info("MockAdapter: closed (no-op)")

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------

    def get_sent_messages(self) -> List[Dict[str, Any]]:
        """Return all messages sent through this adapter (for assertions)."""
        return list(self._sent_messages)

    def reset(self) -> None:
        """Clear sent message history."""
        self._sent_messages.clear()

    def print_stats(self) -> None:
        """Print statistics about sent messages (for testing)."""
        total = len(self._sent_messages)
        rcs_count = sum(1 for m in self._sent_messages if m.get('channel') == 'rcs')
        sms_count = sum(1 for m in self._sent_messages if m.get('channel') == 'sms')
        
        print(f"\n📊 Mock Adapter Stats:")
        print(f"   Total messages: {total}")
        print(f"   RCS: {rcs_count}")
        print(f"   SMS: {sms_count}")
        print(f"   Success rate: {self.success_rate * 100:.0f}%")
