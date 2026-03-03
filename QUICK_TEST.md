# 🚀 Quick Start - End-to-End Test Commands

Copy and paste these commands to run a complete test with 25 phone numbers.

## Prerequisites (One-time setup)

```powershell
# 1. Start Docker services
docker-compose up -d postgres redis rabbitmq

# 2. Run database migrations
.\venv\Scripts\alembic.exe upgrade head

# 3. Seed test data (API keys, template, audience)
.\venv\Scripts\python.exe scripts\seed_dev.py
# ⚠️ SAVE THE API KEY PRINTED - You'll need it!
```

## Run End-to-End Test (3 Terminals)

### Terminal 1 - API Server
```powershell
.\venv\Scripts\uvicorn.exe apps.api.main:app --host 0.0.0.0 --port 8000 --reload
```

### Terminal 2 - Workers
```powershell
.\venv\Scripts\python.exe -m apps.workers.manager
```

### Terminal 3 - Test Script
```powershell
.\venv\Scripts\python.exe tests\test_e2e_20_numbers.py
```

## Expected Result

```
═══════════════════════════════════════════════════════════════════════
        🧪 RCS PLATFORM - END-TO-END TEST (20+ NUMBERS)
═══════════════════════════════════════════════════════════════════════

✅ Using MOCK AGGREGATOR (no real messages sent)

▶ STEP 1: Connecting to Infrastructure
   ✅ Database connected
   ✅ RabbitMQ connected
   ✅ Mock aggregator initialized

▶ STEP 2: Creating Template
   ✅ Template created

▶ STEP 3: Creating Audience with 25 Test Contacts
   ✅ Audience created: 25 contacts

▶ STEP 4: Creating Campaign
   ✅ Campaign created

▶ STEP 5: Activating Campaign
   ✅ Campaign activated!

▶ STEP 6: Monitoring Message Processing
   ✅ All 25 messages processed!

▶ STEP 7: Final Results
   📊 Messages delivered: 24/25 (96%)
   
✅ END-TO-END TEST COMPLETE
```

## Troubleshooting

**If messages stay PENDING:**
- Make sure Terminal 2 (workers) is running
- Check: http://localhost:15672 (RabbitMQ - guest/guest)

**If "connection refused":**
- Verify services: `docker ps`
- Should see: rcs-postgres, rcs-redis, rcs-rabbitmq (all healthy)

**If "database not migrated":**
- Run: `.\venv\Scripts\alembic.exe upgrade head`

## Check Results in Database

```powershell
.\venv\Scripts\python.exe -c "
import asyncio
from apps.adapters.db.postgres import get_database
from sqlalchemy import text

async def show_results():
    db = get_database()
    await db.connect()
    try:
        async with db.session() as session:
            # Count messages by status
            result = await session.execute(
                text('SELECT status, COUNT(*) FROM messages GROUP BY status')
            )
            print('\nMessage Status:')
            for row in result.fetchall():
                status, count = row
                icon = {'PENDING':'⏸️','SENT':'✅','DELIVERED':'✅','FAILED':'❌'}.get(status, ' ')
                print(f'  {icon} {status}: {count}')
            
            # Show campaigns
            result = await session.execute(
                text('SELECT id, name, status, recipient_count FROM campaigns ORDER BY created_at DESC LIMIT 5')
            )
            print('\nRecent Campaigns:')
            for row in result.fetchall():
                print(f'  {row[1]}: {row[2]} ({row[3]} recipients)')
    finally:
        await db.disconnect()

asyncio.run(show_results())
"
```

## Switch to Real RCS (When Ready)

1. Edit `.env`:
```env
USE_MOCK_AGGREGATOR=false
RCS_USERNAME=your_username
RCS_PASSWORD=your_password
RCS_ID=your_id
```

2. Restart API and Workers (Ctrl+C then restart commands above)

3. Run test again - it will ask for confirmation before sending real messages

---

## All-in-One Test Command

```powershell
# This runs everything in sequence (for testing only, not production)
$ErrorActionPreference = "Stop"

Write-Host "Starting services..." -ForegroundColor Cyan
docker-compose up -d postgres redis rabbitmq
Start-Sleep -Seconds 10

Write-Host "Running migrations..." -ForegroundColor Cyan
.\venv\Scripts\alembic.exe upgrade head

Write-Host "Seeding data..." -ForegroundColor Cyan
.\venv\Scripts\python.exe scripts\seed_dev.py

Write-Host "Starting API server..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PWD'; .\venv\Scripts\uvicorn.exe apps.api.main:app --host 0.0.0.0 --port 8000 --reload"

Write-Host "Waiting for API to start..." -ForegroundColor Cyan
Start-Sleep -Seconds 5

Write-Host "Starting workers..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PWD'; .\venv\Scripts\python.exe -m apps.workers.manager"

Write-Host "Waiting for workers to initialize..." -ForegroundColor Cyan
Start-Sleep -Seconds 5

Write-Host "`nReady to test! Run this in a new terminal:" -ForegroundColor Green
Write-Host "  .\venv\Scripts\python.exe tests\test_e2e_20_numbers.py" -ForegroundColor Yellow
Write-Host "`nOr test API access:" -ForegroundColor Green
Write-Host '  $apiKey = "rcs_dev_f5a75afb11a29e8fc379d880249b8fb5"  # Use key from seed output' -ForegroundColor Yellow
Write-Host '  Invoke-WebRequest -Uri "http://localhost:8000/ready" -Headers @{"X-API-Key"=$apiKey} -UseBasicParsing' -ForegroundColor Yellow
```

---

For detailed information, see **TESTING_GUIDE.md**
