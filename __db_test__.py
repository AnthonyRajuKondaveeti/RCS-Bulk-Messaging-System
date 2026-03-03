import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from apps.core.config import get_settings

async def test():
    try:
        s = get_settings()
        url = s.database.url
        print(f"Testing URL: {url.replace(s.database.password, '***')}")
        eng = create_async_engine(url)
        async with eng.connect() as conn:
            print("Connection Success!")
    except Exception as e:
        print(f"Connection Failed: {e}")

if __name__ == "__main__":
    asyncio.run(test())
