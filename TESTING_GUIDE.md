# 🧪 RCS Platform Testing Guide

Complete guide for testing the RCS messaging platform end-to-end.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start - Mock Testing](#quick-start---mock-testing)
3. [End-to-End Test with 20+ Numbers](#end-to-end-test-with-20-numbers)
4. [Testing with Real RCS](#testing-with-real-rcs)
5. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### 1. Services Running

```powershell
# Start Docker containers
docker-compose up -d postgres redis rabbitmq

# Verify all services are healthy
docker ps --format "table {{.Names}}\t{{.Status}}"

# Expected output:
# rcs-postgres   Up 2 minutes (healthy)
# rcs-redis      Up 2 minutes (healthy)
# rcs-rabbitmq   Up 2 minutes (healthy)
```

### 2. Database Migrated

```powershell
# Run migrations to latest
.\venv\Scripts\alembic.exe upgrade head

# Verify current version
.\venv\Scripts\alembic.exe current

# Expected output:
# 006_add_api_key_rate_limit (head)
```

### 3. Seed Data (Optional but Recommended)

```powershell
# Seed development data (API keys, template, audience)
.\venv\Scripts\python.exe scripts\seed_dev.py

# This creates:
# - 1 API key (prints to console - save this!)
# - 1 approved template (mock-template-001)
# - 1 audience with 2 contacts
```

---

## Quick Start - Mock Testing

Test without sending real messages. Perfect for development!

### Step 1: Configure Mock Mode

```powershell
# Verify .env has mock enabled
Get-Content .env | Select-String "USE_MOCK_AGGREGATOR"

# Should show:
# USE_MOCK_AGGREGATOR=true

# If not, add it:
Add-Content .env "USE_MOCK_AGGREGATOR=true"
```

### Step 2: Start API Server

```powershell
# Terminal 1 - API Server
.\venv\Scripts\uvicorn.exe apps.api.main:app --host 0.0.0.0 --port 8000 --reload

# Wait for startup message:
# INFO:     Started server process
# INFO:     Waiting for application startup.
# INFO:     Application startup complete.
```

### Step 3: Start Workers

```powershell
# Terminal 2 - All Workers (simplest option)
.\venv\Scripts\python.exe -m apps.workers.manager

# OR run individually for better control:

# Terminal 2 - Orchestrator Worker
.\venv\Scripts\python.exe -m apps.workers.entrypoints.orchestrator

# Terminal 3 - Dispatcher Worker  
.\venv\Scripts\python.exe -m apps.workers.entrypoints.dispatcher

# Terminal 4 - Webhook Worker
.\venv\Scripts\python.exe -m apps.workers.entrypoints.webhook

# Terminal 5 - Fallback Worker
.\venv\Scripts\python.exe -m apps.workers.entrypoints.fallback
```

### Step 4: Run Simple Test

```powershell
# Terminal 4 (or new terminal) - Test Script
.\venv\Scripts\python.exe tests\local\test_local.py

# Expected output:
# ╔════════════════════════════════════════════════════════════════════╗
# ║                  🧪 RCS PLATFORM LOCAL TESTS                      ║
# ╚════════════════════════════════════════════════════════════════════╝
# 
# TEST 1: Domain Models
# ✅ Campaign created: ...
# ✅ Domain model tests PASSED!
# 
# TEST 2: Services with Mock Adapter
# ✅ Database connected
# ✅ Queue connected
# ✅ Mock adapter initialized (90% success rate)
# ...
```

---

## End-to-End Test with 20+ Numbers

Complete flow test with 25 phone numbers.

### Run the Test

```powershell
# With mock (safe, no real messages)
.\venv\Scripts\python.exe tests\test_e2e_20_numbers.py

# The script will:
# 1. Create a template
# 2. Create audience with 25 contacts
# 3. Create and activate campaign
# 4. Wait for workers to process all 25 messages
# 5. Show detailed results
```

### Expected Output

```
═══════════════════════════════════════════════════════════════════════
        🧪 RCS PLATFORM - END-TO-END TEST (20+ NUMBERS)
═══════════════════════════════════════════════════════════════════════

✅ Using MOCK AGGREGATOR (no real messages sent)
📝 Logging to: logs/test_e2e_20_numbers.log

────────────────────────────────────────────────────────────────────────
▶ STEP 1: Connecting to Infrastructure
────────────────────────────────────────────────────────────────────────
   ✅ Database connected
   ✅ RabbitMQ connected
   ✅ Mock aggregator initialized

────────────────────────────────────────────────────────────────────────
▶ STEP 2: Creating Template
────────────────────────────────────────────────────────────────────────
   ✅ Template created: <uuid>
      Name: E2E Test Template - 20 Numbers
      Status: approved
      External ID: mock-template-e2e-20

────────────────────────────────────────────────────────────────────────
▶ STEP 3: Creating Audience with 25 Test Contacts
────────────────────────────────────────────────────────────────────────
   ✅ Audience created: <uuid>
      Name: E2E Test Audience - 25 Numbers
      Contacts: 25
      Status: ready

   📋 Sample contacts:
      1. +919876540000 - {'name': 'Test User 1', 'test_id': '1'}
      2. +919876540001 - {'name': 'Test User 2', 'test_id': '2'}
      3. +919876540002 - {'name': 'Test User 3', 'test_id': '3'}
      4. +919876540003 - {'name': 'Test User 4', 'test_id': '4'}
      5. +919876540004 - {'name': 'Test User 5', 'test_id': '5'}
      ... and 20 more

────────────────────────────────────────────────────────────────────────
▶ STEP 4: Creating Campaign
────────────────────────────────────────────────────────────────────────
   ✅ Campaign created: <uuid>
      Name: E2E Test Campaign - 25 Numbers
      Status: draft
      Recipients: 25

────────────────────────────────────────────────────────────────────────
▶ STEP 5: Activating Campaign
────────────────────────────────────────────────────────────────────────
   ⚡ Activating campaign...
   This will queue messages for sending by workers...
   ✅ Campaign activated!
      Status: active

────────────────────────────────────────────────────────────────────────
▶ STEP 6: Monitoring Message Processing
────────────────────────────────────────────────────────────────────────

   💡 NOTE: This requires workers to be running!
      Terminal 1: python -m apps.workers.entrypoints.orchestrator
      Terminal 2: python -m apps.workers.entrypoints.dispatcher
      Or: python -m apps.workers.manager (runs all workers)

⏳ Waiting for 25 messages to be processed...
   Timeout: 180s

   Status update:
      ⏸️  PENDING: 25

   Status update:
      ⏸️  PENDING: 20
      ✅ SENT: 5

   Status update:
      ⏸️  PENDING: 10
      ✅ SENT: 15

   Status update:
      ✅ SENT: 25

✅ All 25 messages processed!

────────────────────────────────────────────────────────────────────────
▶ STEP 7: Final Results
────────────────────────────────────────────────────────────────────────

📊 Campaign Statistics:
   Campaign ID: <uuid>
   Status: active
   Recipients: 25
   Messages sent: 25
   Messages delivered: 24
   Messages failed: 1
   Delivery rate: 96.0%

📈 Message Status Breakdown:
   ✅ DELIVERED    :  24 ( 96.0%) ████████████████████
   ❌ FAILED       :   1 (  4.0%) █

📱 Sample Messages:
    1. +919876540000 - ✅ DELIVERED
    2. +919876540001 - ✅ DELIVERED
    3. +919876540002 - ✅ DELIVERED
    4. +919876540003 - ✅ DELIVERED
    5. +919876540004 - ✅ DELIVERED
    6. +919876540005 - ✅ DELIVERED
    7. +919876540006 - ✅ DELIVERED
    8. +919876540007 - ✅ DELIVERED
    9. +919876540008 - ✅ DELIVERED
   10. +919876540009 - ❌ FAILED (Simulated delivery failure)

═══════════════════════════════════════════════════════════════════════
                    ✅ END-TO-END TEST COMPLETE
═══════════════════════════════════════════════════════════════════════

✅ All messages processed successfully!

📝 Next Steps:
   1. Test with real RCS credentials:
      - Remove USE_MOCK_AGGREGATOR from .env
      - Add RCS_USERNAME, RCS_PASSWORD, RCS_ID
      - Run this script again

   Campaign ID: <uuid>
   View logs: logs/test_e2e_20_numbers.log
```

---

## Testing with Real RCS

⚠️ **WARNING**: This will send REAL messages to REAL phone numbers!

### Step 1: Configure RCS Credentials

```powershell
# Edit .env file
notepad .env

# Set these values:
USE_MOCK_AGGREGATOR=false
RCS_USERNAME=your_rcssms_username
RCS_PASSWORD=your_rcssms_password
RCS_ID=your_rcs_id
RCS_BASE_URL=https://api.rcssms.in
```

### Step 2: Verify RCS Connection

```powershell
# Test connection only
.\venv\Scripts\python.exe -c "
from apps.core.config import get_settings
settings = get_settings()
print(f'RCS Config:')
print(f'  Username: {settings.aggregator.username}')
print(f'  Base URL: {settings.aggregator.base_url}')
print(f'  Mock enabled: {settings.aggregator.use_mock}')
"
```

### Step 3: Test with Real Numbers

```powershell
# Restart API and workers with new config
# Stop all terminals (Ctrl+C) and restart

# Terminal 1 - API
.\venv\Scripts\uvicorn.exe apps.api.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 - Workers
.\venv\Scripts\python.exe -m apps.workers.manager

# Terminal 3 - Run test
.\venv\Scripts\python.exe tests\test_e2e_20_numbers.py

# It will ask for confirmation:
# ⚠️  Using REAL RCS AGGREGATOR (messages will be sent!)
#    RCS Username: your_username
# 
#    Continue? (yes/no): yes
```

### Step 4: Use Your Own Phone Numbers

Edit the test script to use real numbers:

```python
# Edit: tests/test_e2e_20_numbers.py
# Line ~115-125

# Replace generated numbers with real ones:
test_contacts = [
    Contact(
        phone_number="+919988776655",  # Your actual test number
        metadata={"name": "Real User 1", "test_id": "1"}
    ),
    Contact(
        phone_number="+919988776656",  # Another real number
        metadata={"name": "Real User 2", "test_id": "2"}
    ),
    # Add more real numbers...
]
```

---

## Testing via API (Postman/curl)

### Get API Key from Seed

```powershell
# The seed script prints the API key
# OR get it from database:
.\venv\Scripts\python.exe -c "
import asyncio
from apps.adapters.db.postgres import get_database
from sqlalchemy import text

async def get_key():
    db = get_database()
    await db.connect()
    try:
        async with db.session() as session:
            result = await session.execute(
                text('SELECT key_hash FROM api_keys LIMIT 1')
            )
            row = result.fetchone()
            if row:
                # Note: This is the HASH, not the raw key!
                # Use the key printed by seed_dev.py
                print('API Key hash found in DB')
                print('Use the raw key from seed_dev.py output')
    finally:
        await db.disconnect()

asyncio.run(get_key())
"
```

### Test Endpoints

```powershell
# 1. Health Check
Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing

# 2. Ready Check (requires API key from seed)
$apiKey = "rcs_dev_f5a75afb11a29e8fc379d880249b8fb5"  # From seed output
Invoke-WebRequest `
    -Uri "http://localhost:8000/ready" `
    -Headers @{"X-API-Key"=$apiKey} `
    -UseBasicParsing

# 3. List Templates
Invoke-WebRequest `
    -Uri "http://localhost:8000/api/v1/templates" `
    -Headers @{"X-API-Key"=$apiKey} `
    -UseBasicParsing

# 4. List Campaigns
Invoke-WebRequest `
    -Uri "http://localhost:8000/api/v1/campaigns" `
    -Headers @{"X-API-Key"=$apiKey} `
    -UseBasicParsing
```

---

## Troubleshooting

### Workers Not Processing Messages

```powershell
# Check RabbitMQ queues
Start-Process "http://localhost:15672"
# Login: guest/guest
# Check queues:
#   - campaign.orchestrate
#   - message.dispatch
#   - message.webhook
#   - message.fallback

# Check worker logs
Get-Content logs\worker.log -Tail 50

# Restart workers
# Ctrl+C in worker terminal, then:
.\venv\Scripts\python.exe -m apps.workers.manager
```

### Database Connection Issues

```powershell
# Check PostgreSQL is running
docker ps | Select-String "rcs-postgres"

# Test connection
.\venv\Scripts\python.exe -c "
import asyncio
from apps.adapters.db.postgres import get_database

async def test():
    db = get_database()
    await db.connect()
    print('✅ Database connected')
    await db.disconnect()

asyncio.run(test())
"
```

### Redis Connection Issues

```powershell
# Check Redis is running
docker ps | Select-String "rcs-redis"

# Test connection
.\venv\Scripts\python.exe -c "
import asyncio
import redis.asyncio as redis

async def test():
    r = await redis.from_url('redis://localhost:6379')
    await r.ping()
    print('✅ Redis connected')
    await r.aclose()

asyncio.run(test())
"
```

### Mock Adapter Not Working

```powershell
# Verify .env setting
Get-Content .env | Select-String "USE_MOCK_AGGREGATOR"

# Should be:
# USE_MOCK_AGGREGATOR=true

# Check logs
Get-Content logs\test_e2e_20_numbers.log -Tail 100
```

### Messages Stuck in PENDING

**Possible causes:**

1. **Workers not running** - Start: `python -m apps.workers.manager`
2. **RabbitMQ issue** - Check: http://localhost:15672
3. **Database lock** - Check logs for deadlock errors
4. **Template not approved** - Template must have status='approved'

```powershell
# Check message status directly
.\venv\Scripts\python.exe -c "
import asyncio
from apps.adapters.db.postgres import get_database
from sqlalchemy import text

async def check():
    db = get_database()
    await db.connect()
    try:
        async with db.session() as session:
            result = await session.execute(
                text('SELECT status, COUNT(*) FROM messages GROUP BY status')
            )
            for row in result.fetchall():
                print(f'{row[0]}: {row[1]}')
    finally:
        await db.disconnect()

asyncio.run(check())
"
```

---

## Performance Testing

### Load Test with 100+ Numbers

Modify `test_e2e_20_numbers.py`:

```python
# Change line ~115:
for i in range(100):  # Instead of 25
    phone = f"+91987654{i:05d}"  # 5 digits for 100+ numbers
```

### Monitor Performance

```powershell
# Watch message processing rate
while ($true) {
    Clear-Host
    Write-Host "Message Processing Status - $(Get-Date -Format 'HH:mm:ss')"
    Write-Host "="*60
    
    .\venv\Scripts\python.exe -c "
import asyncio
from apps.adapters.db.postgres import get_database
from sqlalchemy import text

async def show():
    db = get_database()
    await db.connect()
    try:
        async with db.session() as session:
            result = await session.execute(
                text('SELECT status, COUNT(*) FROM messages GROUP BY status ORDER BY status')
            )
            for row in result.fetchall():
                status, count = row
                icon = {'PENDING':'⏸️','SENT':'✅','DELIVERED':'✅','FAILED':'❌'}.get(status, ' ')
                print(f'{icon} {status:12s}: {count:4d}')
    finally:
        await db.disconnect()

asyncio.run(show())
"
    
    Start-Sleep -Seconds 2
}
```

---

## Quick Commands Summary

```powershell
# Start services
docker-compose up -d postgres redis rabbitmq

# Migrate database
.\venv\Scripts\alembic.exe upgrade head

# Seed data (optional)
.\venv\Scripts\python.exe scripts\seed_dev.py

# Start API
.\venv\Scripts\uvicorn.exe apps.api.main:app --host 0.0.0.0 --port 8000 --reload

# Start workers
.\venv\Scripts\python.exe -m apps.workers.manager

# Run tests
.\venv\Scripts\python.exe tests\local\test_local.py                 # Simple test
.\venv\Scripts\python.exe tests\test_e2e_20_numbers.py              # Full E2E test

# Check status
docker ps
.\venv\Scripts\alembic.exe current
```

---

## Next Steps After Testing

1. ✅ **Mock tests pass** → Configure real RCS credentials
2. ✅ **Real RCS works** → Test with production numbers
3. ✅ **Production ready** → Deploy using `docker-compose.prod.yml`

See `DEPLOYMENT.md` for production deployment guide.
