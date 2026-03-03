"""
Test Circuit Breaker Functionality

Tests that the RedisCircuitBreaker properly blocks requests when OPEN.
"""
import asyncio
import logging
from uuid import uuid4
from apps.adapters.aggregators.rcssms_adapter import RcsSmsAdapter
from apps.core.ports.aggregator import SendMessageRequest
from apps.core.domain.message import MessageChannel
from apps.core.config import get_settings

# Enable debug logging for circuit breaker
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("apps.core.resilience.redis_circuit_breaker")
logger.setLevel(logging.DEBUG)

async def test_circuit_breaker():
    """Test that circuit breaker blocks requests when OPEN"""
    settings = get_settings()
    
    print(f"Redis URL: {settings.redis.url}")
    
    # Create adapter (will use Redis circuit breaker)
    adapter = RcsSmsAdapter(
        username=settings.rcssms.username if settings.rcssms else "test",
        password=settings.rcssms.password if settings.rcssms else "test",
        rcs_id=settings.rcssms.rcs_id if settings.rcssms else "test",
    )
    
    # Check circuit breaker state before test
    breaker_stats = await adapter._breaker.get_stats()
    print(f"\nCircuit Breaker Stats BEFORE test:")
    print(f"  State: {breaker_stats['state']}")
    print(f"  Failure count: {breaker_stats['failure_count']}")
    print(f"  Last opened: {breaker_stats['last_open_ago_seconds']}s ago")
    
    # Create a test request
    request = SendMessageRequest(
        message_id=uuid4(),
        recipient_phone="+919876543210",
        channel=MessageChannel.RCS,
        content_text="Test message",
    )
    
    print("=" * 70)
    print("TEST: Attempting to send message with circuit breaker OPEN")
    print("=" * 70)
    
    try:
        response = await adapter.send_rcs_message(request)
        print(f"✓ Response received: success={response.success}")
        print(f"  Error code: {response.error_code}")
        print(f"  Error message: {response.error_message}")
        
        if response.error_code == "CIRCUIT_OPEN":
            print("\n✅ SUCCESS: Circuit breaker blocked the request as expected!")
            print("   Log should show 'rcssms_circuit_open' without 'sending_rcs_message'")
        else:
            print("\n❌ UNEXPECTED: Circuit breaker did not block (wrong error code)")
            
    except Exception as e:
        print(f"❌ EXCEPTION: {type(e).__name__}: {e}")
    
    finally:
        await adapter.client.aclose()
        if adapter._breaker:
            await adapter._breaker.close()
    
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(test_circuit_breaker())
