# RCS Platform — Operations Runbook
## Docker, DB Verification, Load Testing & Scaling

---

## PART 1 — Docker Commands

### Start infrastructure only (postgres on port 5433, not 5432)
```bash
docker compose up -d postgres rabbitmq redis
```

### Start everything including the worker container
```bash
docker compose up -d
```

### Check all containers are healthy
```bash
docker compose ps
```

### View live worker logs
```bash
docker logs -f rcs-worker
```

### Stop everything
```bash
docker compose down
```

### Nuclear reset — wipe ALL data volumes (containers + data)
```bash
docker compose down -v
```

---

## PART 2 — Clear All Databases

### Step 1: Connect to Postgres (note port 5433, not default 5432)
```bash
docker exec -it rcs-postgres psql -U postgres -d rcs_platform_dev
```

### Step 2: Wipe all tables in correct FK order (run inside psql)
```sql
TRUNCATE TABLE 
  messages,
  events,
  audience_contacts,
  audiences,
  campaigns,
  templates,
  opt_ins
RESTART IDENTITY CASCADE;
```

### Step 3: Flush Redis (circuit breaker state + rate limits)
```bash
docker exec -it rcs-redis redis-cli FLUSHALL
```

### Step 4: Purge all RabbitMQ queues
```bash
docker exec -it rcs-rabbitmq rabbitmqctl purge_queue campaign.orchestrator
docker exec -it rcs-rabbitmq rabbitmqctl purge_queue message.dispatch
docker exec -it rcs-rabbitmq rabbitmqctl purge_queue webhook.process
docker exec -it rcs-rabbitmq rabbitmqctl purge_queue message.fallback
```

---

## PART 3 — Query All DBs to Verify RCS Flow

Run these after activating a campaign. Each checks a different layer.

### PostgreSQL — full flow verification
```bash
docker exec -it rcs-postgres psql -U postgres -d rcs_platform_dev
```

```sql
-- 1. Templates: check status and external_template_id
SELECT id, name, status, external_template_id, created_at
FROM templates
ORDER BY created_at DESC LIMIT 5;

-- 2. Audiences: check contact counts are real
SELECT a.id, a.name, a.total_contacts,
       COUNT(ac.id) AS actual_contacts_in_table
FROM audiences a
LEFT JOIN audience_contacts ac ON ac.audience_id = a.id
GROUP BY a.id, a.name, a.total_contacts
ORDER BY a.created_at DESC LIMIT 5;

-- 3. Campaigns: status + stats
SELECT id, name, status, recipient_count,
       messages_sent, messages_delivered, messages_failed,
       ROUND(messages_delivered::numeric / NULLIF(recipient_count,0) * 100, 1) AS delivery_pct
FROM campaigns
ORDER BY created_at DESC LIMIT 5;

-- 4. Messages: status breakdown for latest campaign
SELECT m.status, COUNT(*) AS count
FROM messages m
JOIN campaigns c ON c.id = m.campaign_id
WHERE c.id = (SELECT id FROM campaigns ORDER BY created_at DESC LIMIT 1)
GROUP BY m.status
ORDER BY count DESC;

-- 5. Message pipeline health — are any stuck in PENDING?
SELECT status, COUNT(*) FROM messages GROUP BY status;

-- 6. Fallback check — child messages created for failed RCS
SELECT 
  parent.status AS rcs_status,
  child.status AS fallback_status,
  child.channel,
  COUNT(*) AS count
FROM messages child
JOIN messages parent ON parent.id = child.parent_message_id
GROUP BY parent.status, child.status, child.channel;

-- 7. Events audit trail for a campaign
SELECT event_type, created_at, data->>'status' AS status
FROM events
WHERE aggregate_id = (SELECT id FROM campaigns ORDER BY created_at DESC LIMIT 1)
ORDER BY created_at;
```

### RabbitMQ — queue depths (should drain to 0 after processing)
```bash
docker exec -it rcs-rabbitmq rabbitmqctl list_queues name messages consumers
```

### Redis — circuit breaker state
```bash
docker exec -it rcs-redis redis-cli KEYS "circuit_breaker:*"
docker exec -it rcs-redis redis-cli KEYS "rate_limit:*"
# If circuit is OPEN you'll see a key like: circuit_breaker:rcssms:state
```

---

## PART 4 — Run the 500-Number Load Test

### Prerequisites
```bash
# DB must be on port 5433 — set this in your .env or export before running
export DB_PORT=5433
export DB_HOST=localhost
export USE_MOCK_AGGREGATOR=true
```

### Run migrations first (only needed after a fresh wipe)
```bash
alembic upgrade head
```

### Start workers locally (3 terminals or use manager)
```bash
# Terminal 1 — Orchestrator
python -m apps.workers.entrypoints.orchestrator

# Terminal 2 — Dispatcher
python -m apps.workers.entrypoints.dispatcher

# Terminal 3 — Webhook processor
python -m apps.workers.entrypoints.webhook

# OR — all in one (dev only)
python -m apps.workers.manager
```

### Run the 500-number test
```bash
USE_MOCK_AGGREGATOR=true python tests/test_e2e_500_numbers.py
```

### Watch it live in another terminal
```bash
# Message pipeline status every 3 seconds
watch -n3 'docker exec rcs-postgres psql -U postgres -d rcs_platform_dev -c \
  "SELECT status, COUNT(*) FROM messages GROUP BY status ORDER BY status;"'

# RabbitMQ queue drain
watch -n2 'docker exec rcs-rabbitmq rabbitmqctl list_queues name messages'
```

---

## PART 5 — Check Logs (What to Look For)

### Worker logs
```bash
# All worker output
docker logs -f rcs-worker

# Filter to just orchestrator steps
docker logs rcs-worker 2>&1 | grep "STEP"

# Filter errors only
docker logs rcs-worker 2>&1 | grep -E "ERROR|CRITICAL|failed|exception"

# Filter circuit breaker events
docker logs rcs-worker 2>&1 | grep "circuit_breaker"
```

### What healthy logs look like
```
STEP 1/7 | Campaign job received — starting orchestration
STEP 2/7 | Campaign loaded
STEP 3/7 | Template loaded and validated
STEP 4-5/7 | Creating message batch from streamed contacts  batch_number=1  batch_size=500
STEP 6/7 | Queuing batch for dispatch  messages_queued=500
STEP 7/7 | Campaign orchestration complete  total_messages_queued=500
```

### What broken looks like
```
STEP 4/7 | No recipients found       ← audience_ids missing in campaign metadata
STEP 3/7 | Template not approved     ← template never approved
ERROR Campaign orchestration failed  ← check error= field for detail
```

### API logs (if running the API separately)
```bash
# Tail API logs if running with uvicorn directly
uvicorn apps.api.main:app --host 0.0.0.0 --port 8000 --reload 2>&1 | grep -v "200 OK"
```

---

## PART 6 — Scaling: Timeouts and Worker Counts

### Should you change timeouts per scale?

**Short answer: change `RCS_TIMEOUT` for real API calls, not for mock.**

| Env Var | Default | What It Controls | When to Change |
|---|---|---|---|
| `RCS_TIMEOUT` | 30s | HTTP timeout for each rcssms.in API call | Increase to 60s in prod if network is slow |
| `SMS_TIMEOUT` | 30s | HTTP timeout for SMS fallback calls | Same as above |
| `ORCHESTRATOR_BATCH_SIZE` | 1000 | Contacts processed per DB query | Lower if DB is under memory pressure |
| `DISPATCHER_CONCURRENCY` | 10 | Parallel sends per dispatcher instance | Increase for higher throughput |
| `WEBHOOK_CONCURRENCY` | 20 | Parallel webhook jobs | Fine as-is for most loads |

**These are NOT in .env by default** — add them to your `.env` file:
```bash
RCS_TIMEOUT=45
DISPATCHER_CONCURRENCY=20
ORCHESTRATOR_BATCH_SIZE=500
```

The mock adapter has a hardcoded 100ms delay per send. For 500 messages at concurrency=10, expect ~5–6 seconds of dispatch time total. No timeout changes needed for mock testing.

---

### Can you increase workers? YES — and your code is ready for it.

The codebase is built for horizontal scaling. Here's what's already in place:

**1. Dispatcher: scale by running multiple instances**
Each dispatcher instance independently pulls from the same `message.dispatch` RabbitMQ queue. RabbitMQ handles load distribution automatically. To run 3 dispatchers:
```bash
# docker-compose.prod.yml already has separate services — just scale them
docker compose -f docker-compose.prod.yml up -d --scale worker-dispatcher=3
```

Or locally, just open 3 terminals running:
```bash
DISPATCHER_CONCURRENCY=20 python -m apps.workers.entrypoints.dispatcher
```

**2. Orchestrator: do NOT scale beyond 1 instance**
The orchestrator processes one campaign job at a time (prefetch=1). Running 2 orchestrators against the same campaign queue is safe but won't help because each campaign job is single-threaded by design. Scale the dispatcher instead.

**3. Webhook processor: safe to scale**
```bash
docker compose -f docker-compose.prod.yml up -d --scale worker-webhook=2
```

**4. The circuit breaker is Redis-backed and shared**
All dispatcher replicas share one circuit breaker state in Redis. If rcssms.in starts failing, ALL dispatchers trip simultaneously — no independent failure storms. This is already implemented correctly.

**5. Concurrency vs. instances math**
For 500 messages with 1 dispatcher at concurrency=10 and 100ms mock latency:
- 500 messages / 10 concurrent = 50 rounds × 100ms ≈ 5 seconds

For 10,000 messages, 2 dispatcher instances at concurrency=20:
- 10,000 / 40 concurrent = 250 rounds × 100ms ≈ 25 seconds

Real rcssms.in will be slower (network latency). Set `DISPATCHER_CONCURRENCY=20` and run 2–3 instances as your starting point for production.

---

## PART 7 — Known Issue: Test Contacts vs. audience_contacts Table

**Critical — read before running the test.**

The test file (`test_e2e_500_numbers.py`) creates contacts using:
```python
Contact(phone_number=phone, metadata=variables)
audience.add_contacts(test_contacts)
```

The `metadata` field is used for variable data here, but the orchestrator reads from the `variables` field of `AudienceContactModel` in the `audience_contacts` table. This means **template personalization (name, test_id) will be empty in the messages** — contacts will send but variables won't be populated.

This is a test data issue, not a production issue. The real API's CSV upload and `POST /audiences/{id}/contacts` correctly populate the `variables` field.

To fix in the test, change the contact creation to:
```python
contact = Contact(phone_number=phone, metadata={})
contact.variables = [f"User {i+1}", str(i+1)]   # matches template {{name}}, {{test_id}}
test_contacts.append(contact)
```

---

## PART 8 — VS Code Agent Prompt (Send to Claude Sonnet in VS Code)

Use this if you want Claude to inspect your running system and report back:

```
I need you to investigate the current state of the RCS messaging platform.
Do NOT modify any code. Read only. Report findings at the end.

TASK 1 — Read these files completely and report any issues:
  - apps/workers/manager.py          (worker startup and fault isolation)
  - apps/workers/orchestrator/campaign_orchestrator.py  (steps 4-7, batch loop)
  - apps/adapters/db/repositories/audience_repo.py      (stream_contacts and bulk_add_contacts)
  - tests/test_e2e_500_numbers.py   (check Contact creation — does it set .variables?)

TASK 2 — Identify these specific things:
  1. In test_e2e_500_numbers.py: are contacts created with a .variables list 
     matching the template placeholders, or is variable data stuffed into .metadata?
  2. In campaign_orchestrator.py: what is the default batch_size in __init__?
     Is it the same as what manager.py passes via ORCHESTRATOR_BATCH_SIZE env var?
  3. In audience_repo.py: does bulk_add_contacts() write to the audience_contacts 
     table or to the audiences.contacts JSON column?
  4. In manager.py: does the dispatcher use DISPATCHER_CONCURRENCY env var?
     What is the fallback default?

TASK 3 — Check if these env vars are documented anywhere in the codebase:
  ORCHESTRATOR_BATCH_SIZE, DISPATCHER_CONCURRENCY, WEBHOOK_CONCURRENCY,
  RCS_TIMEOUT, DB_PORT
  Report which file documents each one (or "undocumented").

TASK 4 — Report any mismatches between:
  - What the test file uploads as contact data
  - What the orchestrator reads when building message variables

Give me a numbered findings list with file:line references for each issue found.
No code changes. Findings report only.
```
