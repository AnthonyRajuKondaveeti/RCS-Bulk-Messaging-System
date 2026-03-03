import asyncio
import os
import sys
import json
from uuid import uuid4
from sqlalchemy import text

# Add project root to sys.path
sys.path.append(os.getcwd())

from apps.adapters.db.postgres import init_database, close_database, get_database

async def debug_insert():
    await init_database()
    db = get_database()
    
    tenant_id = uuid4()
    audience_id = uuid4()
    
    try:
        async with db.session() as session:
            # Create a test tenant/audience first if needed, 
            # but let's just try to insert into audience_contacts with a random audience_id
            # (Note: This might fail FK if not careful, but let's see the SERIALIZATION error first)
            
            print("Testing JSON insertion...")
            data = ["Test Variable"]
            
            # Method 1: Bare list (usually fails on JSONB if not cast, but let's see)
            try:
                print("Method 1: Bare list")
                await session.execute(
                    text("INSERT INTO audience_contacts (id, audience_id, phone_number, variables, created_at) VALUES (:id, :aid, :p, :v, now())"),
                    {"id": uuid4(), "aid": audience_id, "p": "+1234567890", "v": data}
                )
                print("Method 1 SUCCESS")
            except Exception as e:
                print(f"Method 1 FAILED: {e}")

            # Method 2: json.dumps string
            try:
                print("\nMethod 2: json.dumps string")
                await session.execute(
                    text("INSERT INTO audience_contacts (id, audience_id, phone_number, variables, created_at) VALUES (:id, :aid, :p, :v, now())"),
                    {"id": uuid4(), "aid": audience_id, "p": "+1234567891", "v": json.dumps(data)}
                )
                print("Method 2 SUCCESS")
            except Exception as e:
                print(f"Method 2 FAILED: {e}")

    finally:
        await close_database()

if __name__ == "__main__":
    asyncio.run(debug_insert())
