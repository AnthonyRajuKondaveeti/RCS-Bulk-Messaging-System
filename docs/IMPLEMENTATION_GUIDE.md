# RCS Platform - Implementation Guide

## 📋 What We've Built (Phase 1)

### ✅ Completed Files

1. **Core Domain Models** (Pure Business Logic)
   - ✅ `apps/core/domain/campaign.py` - Campaign aggregate with state machine
   - ✅ `apps/core/domain/message.py` - Message entity with RCS support
   
2. **Port Interfaces** (Clean Architecture Contracts)
   - ✅ `apps/core/ports/aggregator.py` - RCS/SMS provider interface
   - ✅ `apps/core/ports/queue.py` - Message queue interface
   - ✅ `apps/core/ports/repository.py` - Data persistence interface

3. **Configuration**
   - ✅ `apps/core/config.py` - Centralized config management
   - ✅ `infra/config/dev.yaml` - Development configuration

4. **Service Layer**
   - ✅ `apps/core/services/campaign_service.py` - Campaign orchestration

5. **Documentation**
   - ✅ `README.md` - Comprehensive project documentation

## 🎯 Next Steps - Remaining Files

### Phase 2: Complete Domain & Services (Priority 1)

```bash
# Domain Models
apps/core/domain/template.py          # Template value object
apps/core/domain/opt_in.py            # Opt-in/consent management

# Services
apps/core/services/delivery_service.py     # Message delivery orchestration
apps/core/services/fallback_service.py     # SMS fallback logic
apps/core/services/compliance_service.py   # Opt-out checking
apps/core/services/billing_service.py      # Usage tracking
```

### Phase 3: Infrastructure Adapters (Priority 1)

```bash
# Aggregator Implementations
apps/adapters/aggregators/gupshup_adapter.py   # Gupshup integration
apps/adapters/aggregators/route_adapter.py     # Route Mobile (stub)
apps/adapters/aggregators/infobip_adapter.py   # Infobip (stub)

# Queue Implementation
apps/adapters/queue/rabbitmq.py               # RabbitMQ adapter
apps/adapters/queue/bullmq.py                 # BullMQ (optional)
apps/adapters/queue/dlq_handler.py            # Dead Letter Queue

# Database Layer
apps/adapters/db/postgres.py                  # DB connection
apps/adapters/db/repositories/campaign_repo.py
apps/adapters/db/repositories/message_repo.py
apps/adapters/db/repositories/event_repo.py
apps/adapters/db/repositories/opt_out_repo.py
```

### Phase 4: API Layer (Priority 1)

```bash
# Main Application
apps/api/main.py                      # FastAPI app setup

# Middleware
apps/api/middleware/auth.py           # Authentication
apps/api/middleware/tenancy.py        # Multi-tenancy
apps/api/middleware/rate_limit.py     # Rate limiting
apps/api/middleware/request_id.py     # Correlation IDs

# API Routes v1
apps/api/routes/v1/campaigns.py       # Campaign CRUD
apps/api/routes/v1/templates.py       # Template management
apps/api/routes/v1/audiences.py       # Audience management
apps/api/routes/v1/health.py          # Health checks
apps/api/routes/v1/webhooks.py        # Webhook endpoints
apps/api/routes/v1/analytics.py       # Campaign analytics
```

### Phase 5: Workers (Priority 2)

```bash
# Background Workers
apps/workers/orchestrator/campaign_orchestrator.py   # Campaign execution
apps/workers/dispatcher/message_dispatcher.py        # Message sending
apps/workers/fallback/sms_fallback_worker.py        # Fallback handler
apps/workers/events/webhook_processor.py            # Webhook callbacks
```

### Phase 6: Observability (Priority 2)

```bash
# Monitoring
apps/core/observability/metrics.py    # Prometheus metrics
apps/core/observability/tracing.py    # OpenTelemetry
apps/core/observability/logging.py    # Structured logging
```

### Phase 7: Database & Infrastructure (Priority 2)

```bash
# Migrations
infra/migrations/env.py                          # Alembic config
infra/migrations/versions/001_initial_schema.py  # Initial tables

# Docker
infra/docker/api.Dockerfile            # API container
infra/docker/worker.Dockerfile         # Worker container
infra/docker/docker-compose.yml        # Local development

# Config
infra/config/staging.yaml              # Staging environment
infra/config/prod.yaml                 # Production environment
```

### Phase 8: Testing (Priority 3)

```bash
# Unit Tests
tests/unit/test_campaign.py
tests/unit/test_message.py
tests/unit/test_state_machine.py
tests/unit/test_fallback.py
tests/unit/test_rate_limit.py

# Integration Tests
tests/integration/test_campaign_flow.py
tests/integration/test_webhooks.py
tests/integration/test_dlq.py
tests/integration/test_api.py

# Load Tests
tests/load/send_10k_messages.py
tests/load/concurrent_campaigns.py
```

### Phase 9: Documentation (Priority 3)

```bash
docs/architecture.md           # Architecture deep dive
docs/api_versioning.md        # API versioning strategy
docs/message_lifecycle.md     # Message flow diagram
docs/retry_and_dlq.md        # Retry logic & DLQ
docs/observability.md        # Monitoring guide
docs/scaling_plan.md         # Horizontal scaling
docs/deployment.md           # Deployment guide
```

## 🚀 Recommended Build Order

### Week 1: Core Foundation
1. ✅ Domain models (Done!)
2. ✅ Port interfaces (Done!)
3. Template & Opt-in models
4. Delivery & Fallback services
5. Database repositories

### Week 2: Infrastructure
1. PostgreSQL adapter & repositories
2. RabbitMQ adapter
3. Gupshup adapter (primary)
4. Route adapter (stub for future)
5. Database migrations

### Week 3: API & Workers
1. FastAPI main app
2. Authentication middleware
3. Campaign routes
4. Campaign orchestrator worker
5. Message dispatcher worker
6. Webhook processor

### Week 4: Testing & Polish
1. Unit tests
2. Integration tests
3. Observability (metrics, tracing, logs)
4. Docker setup
5. Documentation

## 💡 Implementation Tips

### 1. Domain Models (Priority: HIGH)
```python
# Template value object
@dataclass
class Template:
    """RCS message template with variables"""
    id: UUID
    name: str
    content: str
    variables: List[str]  # e.g., ["customer_name", "order_id"]
    rich_card_template: Optional[RichCard]
    
    def render(self, variables: Dict[str, str]) -> MessageContent:
        """Render template with actual values"""
        text = self.content
        for key, value in variables.items():
            text = text.replace(f"{{{key}}}", value)
        return MessageContent(text=text, rich_card=self.rich_card_template)
```

### 2. Database Schema Design

```sql
-- campaigns table
CREATE TABLE campaigns (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL,
    campaign_type VARCHAR(50) NOT NULL,
    template_id UUID NOT NULL,
    scheduled_for TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    -- Stats columns
    messages_sent INTEGER DEFAULT 0,
    messages_delivered INTEGER DEFAULT 0,
    messages_failed INTEGER DEFAULT 0,
    -- Indexes
    INDEX idx_tenant_status (tenant_id, status),
    INDEX idx_scheduled (scheduled_for)
);

-- messages table (high volume - consider partitioning)
CREATE TABLE messages (
    id UUID PRIMARY KEY,
    campaign_id UUID NOT NULL REFERENCES campaigns(id),
    tenant_id UUID NOT NULL,
    recipient_phone VARCHAR(20) NOT NULL,
    status VARCHAR(50) NOT NULL,
    channel VARCHAR(20) NOT NULL,
    external_id VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW(),
    sent_at TIMESTAMP,
    delivered_at TIMESTAMP,
    -- Indexes
    INDEX idx_campaign (campaign_id),
    INDEX idx_external_id (external_id),
    INDEX idx_status_created (status, created_at)
) PARTITION BY RANGE (created_at);  -- Monthly partitions
```

### 3. Queue Message Format

```python
# Campaign orchestrator job
{
    "campaign_id": "550e8400-e29b-41d4-a716-446655440000",
    "batch_size": 100,
    "rate_limit": 1000  # messages per second
}

# Message dispatcher job
{
    "message_id": "550e8400-e29b-41d4-a716-446655440001",
    "retry_count": 0,
    "priority": "high"
}

# Fallback handler job
{
    "message_id": "550e8400-e29b-41d4-a716-446655440001",
    "original_channel": "rcs",
    "fallback_channel": "sms"
}
```

### 4. Gupshup Integration

```python
# RCS message payload
POST https://api.gupshup.io/wa/api/v1/msg
{
    "channel": "rcs",
    "source": "919876543210",
    "destination": "919876543211",
    "message": {
        "type": "card",
        "payload": {
            "title": "Your Order",
            "description": "Order #1234 shipped",
            "media": {
                "url": "https://cdn.example.com/image.jpg",
                "contentType": "image/jpeg"
            },
            "suggestions": [
                {
                    "type": "action",
                    "text": "Track Order",
                    "action": {
                        "type": "url",
                        "url": "https://track.example.com/1234"
                    }
                }
            ]
        }
    }
}
```

### 5. Worker Pattern

```python
# Worker skeleton
class CampaignOrchestratorWorker:
    async def process_campaign(self, job: QueueJob):
        campaign_id = UUID(job.payload["campaign_id"])
        
        # Load campaign
        campaign = await self.campaign_repo.get_by_id(campaign_id)
        
        # Get recipients (paginated)
        recipients = await self.get_recipients(campaign_id, batch_size=100)
        
        # Create messages
        messages = []
        for recipient in recipients:
            message = Message.create(
                campaign_id=campaign_id,
                tenant_id=campaign.tenant_id,
                recipient_phone=recipient.phone,
                content=template.render(recipient.variables),
            )
            messages.append(message)
        
        # Save messages
        await self.message_repo.save_batch(messages)
        
        # Enqueue dispatch jobs
        for message in messages:
            await self.queue.enqueue(QueueMessage(
                id=str(message.id),
                queue_name="message.dispatch",
                payload={"message_id": str(message.id)},
            ))
```

## 🎓 Architecture Principles

### Clean Architecture Layers

1. **Domain Layer** (No dependencies)
   - Pure business logic
   - Domain models with behavior
   - No framework dependencies
   - No database, no HTTP, no external libs

2. **Service Layer** (Depends on Domain + Ports)
   - Orchestrates use cases
   - Uses domain models
   - Depends on port interfaces (not implementations)

3. **Port Layer** (Interfaces only)
   - Defines contracts
   - Abstract interfaces
   - No implementations

4. **Adapter Layer** (Implements Ports)
   - Database adapters
   - Queue adapters
   - HTTP clients
   - All infrastructure code

5. **API Layer** (Entry point)
   - FastAPI routes
   - Depends on services
   - Minimal logic (validation, serialization)

### Dependency Rule
**Dependencies point inward:**
API → Services → Domain
Adapters → Ports ← Services

**Never:**
Domain → Services
Domain → Adapters
Ports → Adapters

## 📦 Dependencies to Install

```bash
# Core
fastapi==0.104.1
uvicorn[standard]==0.24.0
pydantic==2.5.0
python-multipart==0.0.6

# Database
sqlalchemy==2.0.23
asyncpg==0.29.0
alembic==1.12.1
psycopg2-binary==2.9.9

# Queue
aio-pika==9.3.1  # RabbitMQ
redis==5.0.1

# HTTP
httpx==0.25.2
aiohttp==3.9.1

# Auth
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4

# Monitoring
prometheus-client==0.19.0
opentelemetry-api==1.21.0
opentelemetry-sdk==1.21.0

# Utils
pyyaml==6.0.1
python-dotenv==1.0.0
```

## 🎯 Success Criteria

### MVP (Minimum Viable Product)
- ✅ Create campaign
- ✅ Send RCS messages via Gupshup
- ✅ Automatic SMS fallback
- ✅ Webhook status updates
- ✅ Basic analytics
- ✅ Opt-out management

### Production Ready
- All unit tests passing (>80% coverage)
- All integration tests passing
- Load tested (10K msg/sec)
- Monitoring dashboards
- API documentation complete
- Deployment automation
- Disaster recovery plan

## 📞 Next Steps

Would you like me to generate:
1. **Delivery Service** - Core message sending logic
2. **Gupshup Adapter** - Real aggregator integration
3. **Database Repositories** - PostgreSQL implementation
4. **API Routes** - REST endpoints
5. **Workers** - Background job processors

Just let me know which component to build next!
