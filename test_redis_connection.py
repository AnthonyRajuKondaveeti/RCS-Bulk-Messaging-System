"""Quick test to check Redis connection"""
import asyncio
import time
import redis.asyncio as aioredis

async def test_redis():
    try:
        r = await aioredis.from_url(
            "redis://localhost:6379/0",
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        
        # Test connection
        await r.ping()
        print("SUCCESS: Connected to Redis\n")
        
        # Set the circuit breaker state using Python
        await r.set("circuit_breaker:rcssms:state", "open")
        ts = time.time() - 30  # 30 seconds ago
        await r.set("circuit_breaker:rcssms:open_ts", f"{ts}")
        print("Set circuit breaker to OPEN with timestamp 30s ago\n")
        
        # List all keys matching circuit_breaker pattern
        keys = await r.keys("circuit_breaker:*")
        print(f"All circuit_breaker keys: {keys}\n")
        
        # Get the circuit breaker state
        state = await r.get("circuit_breaker:rcssms:state")
        open_ts = await r.get("circuit_breaker:rcssms:open_ts")
        print(f"Circuit breaker state: '{state}'")
        print(f"Open timestamp: '{open_ts}'")
        print(f"State == 'open': {state == 'open'}\n")
        
        await r.aclose()
        
        print("="*70)
        print("Now test the circuit breaker with the RCS adapter...")
        print("="*70)
        
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_redis())
