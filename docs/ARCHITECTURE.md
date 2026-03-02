# Architecture

## Pattern

Hexagonal architecture (ports & adapters). Business logic in `apps/core` has zero infrastructure imports. All infrastructure lives in `apps/adapters` and implements abstract port interfaces.

```
apps/core/ports/aggregator.py   ← AggregatorPort (abstract)
apps/core/ports/queue.py        ← QueuePort (abstract)
apps/adapters/aggregators/      ← RcsSmsAdapter, MockAdapter
apps/adapters/queue/            ← RabbitMQAdapter
apps/adapters/db/               ← PostgreSQL repos
```

Swap the aggregator or queue broker without touching domain logic. Run all tests against MockAdapter with no real API calls.

---

## Components

```
Client
  └─ API (FastAPI :8000)
       ├─ PostgreSQL   — source of truth
       ├─ Redis        — rate limiting + circuit breaker state
       └─ RabbitMQ
            ├─ campaign.orchestrator ──► Orchestrator Worker
            │                                └─ message.dispatch ──► Dispatcher Worker
            │                                                              └─ rcssms.in API
            └─ webhook.process  ◄── POST /webhooks/rcssms ◄── rcssms.in DLR
```

---

## Async Dispatch Pipeline

The API never calls rcssms.in directly. Every campaign activation goes through the queue pipeline:

| Step | Actor | Action |
|---|---|---|
| 1 | API | Validates campaign + template → publishes to `campaign.orchestrator` |
| 2 | Orchestrator | Streams audience contacts in batches of 500 → creates `Message` rows (PENDING) → publishes to `message.dispatch` |
| 3 | Dispatcher | Dequeues → idempotency check → calls `RcsSmsAdapter` → updates status to SENT |
| 4 | rcssms.in | POSTs DLR to `/api/v1/webhooks/rcssms` |
| 5 | API | Webhook processor updates status to DELIVERED / FAILED |

**Why queue-based:** campaigns target 100k+ recipients. Synchronous dispatch would time out and lose messages on any failure. Queue gives at-least-once delivery, backpressure, and horizontal scaling.

---

## Key Design Decisions

### RabbitMQ over Celery
Direct aio-pika gives full control over queue topology, DLQ binding, prefetch, and publisher confirms. Celery adds abstraction with no benefit at this scale.

### Dead Letter Queue per queue
Every queue declares `x-dead-letter-exchange`. Messages that exhaust retries route to `queue_name.dlq` automatically — nothing is silently dropped.

### Publisher confirms on batch enqueue
The orchestrator uses a dedicated channel with `publisher_confirms=True`. Each publish awaits broker ACK before continuing. Prevents silent partial-batch failures on crash mid-publish.

### Redis circuit breaker (shared state)
Multiple dispatcher replicas share circuit breaker state in Redis. When the breaker trips on one instance, all instances immediately stop calling rcssms.in — no independent failure storms.

```
States: CLOSED → (5 failures in 60s) → OPEN → (60s recovery) → HALF-OPEN → CLOSED
```

### Keyset pagination for audience streaming
`stream_contacts()` uses `WHERE id > last_seen_id LIMIT 500` — O(1) per page via index. OFFSET degrades to O(n) full scan; unusable at 100k+ contacts.

### Multi-tenancy: shared schema
All tables include `tenant_id`. Auth middleware resolves tenant from API key on every request. All repo methods filter by `tenant_id` — no cross-tenant query is possible.

---

## Message Status Lifecycle

```
PENDING → QUEUED → SENT → DELIVERED   (happy path)
                       └─ FAILED → (retry) → FALLBACK_SENT → FALLBACK_DELIVERED
PENDING → EXPIRED  (created_at + 24h passed without send)
```

---

## Middleware Order

FastAPI applies middleware LIFO. Declaration order in `main.py` → execution order on request:

```
RequestSizeLimit → RequestID → RateLimit → Auth → Tenancy
```
