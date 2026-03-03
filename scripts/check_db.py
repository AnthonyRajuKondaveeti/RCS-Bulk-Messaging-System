import asyncio
import os
import sys

# Add project root to sys.path
sys.path.append(os.getcwd())

from apps.adapters.db.postgres import get_database, init_database, close_database
from sqlalchemy import text

async def check():
    await init_database()
    db = get_database()
    async with db.session() as session:
        res = await session.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"))
        print('Tables:', [r[0] for r in res.fetchall()])
    await close_database()

if __name__ == "__main__":
    asyncio.run(check())
