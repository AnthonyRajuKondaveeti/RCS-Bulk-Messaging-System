"""
Mock Aggregator Adapter for Testing

Use this instead of real Gupshup for local/sandbox testing.
No actual messages are sent - all operations are simulated.

Usage:
    from apps.adapters.aggregators.mock_adapter import MockAdapter
    
    adapter = MockAdapter(success_rate=0.9)  # 90% success rate
    response = await adapter.send_rcs_message(request)
"""

import asyncio
import logging
import random
from typing import List, Dict, Any, Optional
from uuid import uuid4
from datetime import datetime

from apps.core.ports.aggregator import (
    AggregatorPort,
    SendMessageRequest,
    SendMessageResponse,
    DeliveryStatus,
    CapabilityCheckResult,
)


logger = logging.getLogger(__name__)


class MockAdapter(AggregatorPort):
    """
    Mock aggregator for testing without real API calls
    
    Features:
    - Simulates message sending
    - Configurable success rate
    - Tracks all operations for inspection
    - No external dependencies
    
    Args:
        success_rate: Probability of successful sends (0.0 to 1.0)
        delay: Simulated network delay in seconds
        rcs_capable_rate: Probability that phone is RCS capable
    """
    
    def __init__(
        self,
        success_rate: float = 0.95,
        delay: float = 0.01,
        rcs_capable_rate: float = 0.8,
    ):
        self.success_rate = success_rate
        self.delay = delay
        self.rcs_capable_rate = rcs_capable_rate
        self.sent_messages: List[Dict] = []
        self.webhooks_received: List[Dict] = []
        
        logger.info(
            f"[MOCK] Mock Adapter initialized "
            f"(success_rate={success_rate}, delay={delay}s)"
        )
    
    async def send_rcs_message(
        self,
        request: SendMessageRequest,
    ) -> SendMessageResponse:
        """Simulate RCS message sending"""
        await asyncio.sleep(self.delay)
        
        success = random.random() < self.success_rate
        external_id = f"mock_rcs_{uuid4().hex[:12]}"
        
        self.sent_messages.append({
            "type": "rcs",
            "request": request,
            "external_id": external_id,
            "success": success,
            "timestamp": datetime.utcnow(),
        })
        
        logger.info(
            f"[MOCK]: {'Success' if success else 'Failed'} RCS to {request.recipient_phone} "
            f"(id={external_id})"
        )
        
        if success:
            return SendMessageResponse(
                success=True,
                external_id=external_id,
            )
        else:
            error_codes = [
                "NETWORK_ERROR",
                "RATE_LIMIT",
                "INVALID_NUMBER",
                "SERVICE_UNAVAILABLE",
            ]
            error_code = random.choice(error_codes)
            
            return SendMessageResponse(
                success=False,
                error_code=error_code,
                error_message=f"Mock error: {error_code}",
            )
    
    async def send_sms_message(
        self,
        request: SendMessageRequest,
    ) -> SendMessageResponse:
        """Simulate SMS message sending (higher success rate)"""
        await asyncio.sleep(self.delay)
        
        # SMS has 99% success rate (more reliable than RCS)
        success = random.random() < 0.99
        external_id = f"mock_sms_{uuid4().hex[:12]}"
        
        self.sent_messages.append({
            "type": "sms",
            "request": request,
            "external_id": external_id,
            "success": success,
            "timestamp": datetime.utcnow(),
        })
        
        logger.info(
            f"[MOCK]: {'Success' if success else 'Failed'} SMS to {request.recipient_phone} "
            f"(id={external_id})"
        )
        
        return SendMessageResponse(
            success=True,
            external_id=external_id,
        )
    
    async def check_rcs_capability(
        self,
        phone_numbers: List[str],
    ) -> List[CapabilityCheckResult]:
        """Simulate RCS capability check"""
        await asyncio.sleep(self.delay)
        
        results = []
        for phone in phone_numbers:
            # Deterministic but pseudo-random based on phone number
            rcs_enabled = (hash(phone) % 100) < (self.rcs_capable_rate * 100)
            
            result = CapabilityCheckResult(
                phone_number=phone,
                rcs_enabled=rcs_enabled,
                last_checked=datetime.utcnow(),
                features=["typing_indicator", "read_receipts"] if rcs_enabled else [],
            )
            results.append(result)
            
            logger.debug(f"✅ {phone}: RCS {'enabled' if rcs_enabled else 'disabled'}")
        
        return results
    
    async def get_delivery_status(
        self,
        external_id: str,
    ) -> Optional[DeliveryStatus]:
        """Simulate delivery status check"""
        await asyncio.sleep(self.delay)
        
        # Simulate progressive delivery states
        statuses = ["sent", "delivered", "read"]
        status = random.choice(statuses)
        
        return DeliveryStatus(
            message_id=external_id,
            external_id=external_id,
            status=status,
            timestamp=datetime.utcnow(),
        )
    
    async def handle_webhook(
        self,
        payload: Dict[str, Any],
        headers: Dict[str, str],
    ) -> Optional[DeliveryStatus]:
        """Simulate webhook handling"""
        self.webhooks_received.append({
            "payload": payload,
            "headers": headers,
            "timestamp": datetime.utcnow(),
        })
        
        logger.info(f"[MOCK]: Webhook received - {payload.get('eventType')}")
        
        return DeliveryStatus(
            message_id=payload.get("messageId"),
            external_id=payload.get("externalId", payload.get("messageId")),
            status=payload.get("eventType", "delivered"),
            timestamp=datetime.utcnow(),
        )
    
    def validate_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> bool:
        """Mock signature validation - always succeeds"""
        return True
    
    async def get_account_balance(self) -> Dict[str, Any]:
        """Simulate getting account balance"""
        return {
            "balance": 1000.0,
            "currency": "INR",
            "credits": 5000,
            "aggregator": "mock"
        }
    
    def get_name(self) -> str:
        """Get adapter name"""
        return "mock"
    
    async def close(self):
        """Cleanup resources"""
        logger.info("[MOCK] Mock Adapter closed")
    
    # Testing helper methods
    
    def get_sent_messages(self) -> List[Dict]:
        """Get all sent messages for inspection"""
        return self.sent_messages
    
    def get_success_count(self) -> int:
        """Get number of successful sends"""
        return sum(1 for msg in self.sent_messages if msg["success"])
    
    def get_failure_count(self) -> int:
        """Get number of failed sends"""
        return sum(1 for msg in self.sent_messages if not msg["success"])
    
    def get_webhooks(self) -> List[Dict]:
        """Get all received webhooks"""
        return self.webhooks_received
    
    def clear_history(self):
        """Clear all history"""
        self.sent_messages = []
        self.webhooks_received = []
        logger.info("[MOCK] Mock Adapter history cleared")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive statistics"""
        rcs_count = sum(1 for msg in self.sent_messages if msg["type"] == "rcs")
        sms_count = sum(1 for msg in self.sent_messages if msg["type"] == "sms")
        
        return {
            "total_sent": len(self.sent_messages),
            "successful": self.get_success_count(),
            "failed": self.get_failure_count(),
            "rcs_sent": rcs_count,
            "sms_sent": sms_count,
            "webhooks_received": len(self.webhooks_received),
            "success_rate": (
                self.get_success_count() / len(self.sent_messages)
                if self.sent_messages else 0
            ),
        }
    
    def print_stats(self):
        """Print statistics to console"""
        stats = self.get_stats()
        print("\n" + "="*60)
        print("MOCK ADAPTER STATISTICS")
        print("="*60)
        print(f"Total Messages Sent: {stats['total_sent']}")
        print(f"  Successful: {stats['successful']}")
        print(f"  Failed: {stats['failed']}")
        print(f"  RCS: {stats['rcs_sent']}")
        print(f"  SMS: {stats['sms_sent']}")
        print(f"Success Rate: {stats['success_rate']*100:.1f}%")
        print(f"Webhooks Received: {stats['webhooks_received']}")
        print("="*60 + "\n")
