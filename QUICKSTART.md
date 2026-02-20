# 🚀 Quick Start Guide - RCS Platform

## What You Have Now

✅ **Core Domain Models** - Campaign and Message with full business logic
✅ **Port Interfaces** - Clean contracts for all external dependencies  
✅ **Configuration System** - Environment-based config management
✅ **Campaign Service** - Complete orchestration logic
✅ **Documentation** - Architecture and implementation guides
✅ **Development Environment** - Docker Compose setup

## 📁 File Structure

```
rcs-platform/
├── apps/
│   └── core/
│       ├── domain/
│       │   ├── campaign.py      ✅ Campaign aggregate with state machine
│       │   └── message.py       ✅ Message entity with RCS/SMS support
│       ├── ports/
│       │   ├── aggregator.py    ✅ RCS/SMS provider interface
│       │   ├── queue.py         ✅ Message queue interface
│       │   └── repository.py    ✅ Data persistence interface
│       ├── services/
│       │   └── campaign_service.py  ✅ Campaign orchestration
│       └── config.py            ✅ Configuration management
│
├── infra/
│   └── config/
│       └── dev.yaml             ✅ Development config
│
├── docs/
│   └── IMPLEMENTATION_GUIDE.md  ✅ Next steps & architecture
│
├── README.md                    ✅ Project documentation
├── requirements.txt             ✅ Python dependencies
├── docker-compose.yml           ✅ Local infrastructure
├── .env.example                 ✅ Environment template
└── .gitignore                   ✅ Git ignore rules
```

## 🏃 Getting Started (5 minutes)

### 1. Setup Infrastructure

```bash
# Start PostgreSQL, RabbitMQ, Redis, Jaeger
docker-compose up -d

# Verify services are running
docker-compose ps

# You should see:
# - postgres (port 5432)
# - rabbitmq (ports 5672, 15672)
# - redis (port 6379)
# - jaeger (port 16686)
```

### 2. Setup Python Environment

```bash
# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Setup environment variables
cp .env.example .env
# Edit .env with your Gupshup credentials
```

### 3. Test the Domain Models

```python
# Create a test script: test_domain.py
from apps.core.domain.campaign import Campaign, CampaignType, Priority
from apps.core.domain.message import Message, MessageContent, SuggestedAction
from datetime import datetime
from uuid import uuid4

# Test Campaign creation
tenant_id = uuid4()
template_id = uuid4()

campaign = Campaign.create(
    name="Test Campaign",
    tenant_id=tenant_id,
    template_id=template_id,
    campaign_type=CampaignType.PROMOTIONAL,
    priority=Priority.HIGH,
)

print(f"✅ Campaign created: {campaign.id}")
print(f"Status: {campaign.status}")

# Test scheduling
campaign.schedule(scheduled_for=datetime(2024, 12, 31, 9, 0))
print(f"✅ Campaign scheduled for: {campaign.scheduled_for}")

# Test Message creation
content = MessageContent(
    text="Your order has shipped!",
    suggestions=[
        SuggestedAction(
            type="url",
            text="Track Order",
            url="https://example.com/track"
        )
    ]
)

message = Message.create(
    campaign_id=campaign.id,
    tenant_id=tenant_id,
    recipient_phone="+919876543210",
    content=content,
)

print(f"✅ Message created: {message.id}")
print(f"Recipient: {message.recipient_phone}")
print(f"Channel: {message.channel}")

# Test SMS fallback conversion
sms_text = content.to_sms_text()
print(f"✅ SMS fallback text: {sms_text}")

print("\n🎉 All domain models working perfectly!")
```

Run it:
```bash
python test_domain.py
```

### 4. Explore the Services

```bash
# RabbitMQ Management UI
open http://localhost:15672
# Login: guest/guest

# Jaeger Tracing UI
open http://localhost:16686
```

## 📚 Understanding the Code

### Domain Model Example - Campaign State Machine

```python
# campaigns can only transition through valid states
campaign = Campaign.create(...)  # Status: DRAFT

campaign.schedule(datetime(2024, 12, 25))  # Status: SCHEDULED
campaign.activate()  # Status: ACTIVE
campaign.pause()     # Status: PAUSED
campaign.resume()    # Status: ACTIVE
campaign.complete()  # Status: COMPLETED

# Invalid transitions raise exceptions
try:
    campaign.activate()  # Already completed!
except InvalidStateTransition as e:
    print(f"Error: {e}")
```

### Port Interface Example - Clean Architecture

```python
# Business logic depends on interface, not implementation
from apps.core.ports.aggregator import AggregatorPort

class DeliveryService:
    def __init__(self, aggregator: AggregatorPort):
        self.aggregator = aggregator  # Could be Gupshup, Route, or mock
    
    async def send_message(self, message: Message):
        # Same code works with any aggregator implementation
        response = await self.aggregator.send_rcs_message(request)
        return response

# In tests, inject mock
service = DeliveryService(aggregator=MockAggregator())

# In production, inject real implementation
service = DeliveryService(aggregator=GupshupAdapter())
```

## 🎯 What to Build Next

### Priority 1: Core Services (Week 1)
1. **Delivery Service** - Message sending logic
2. **Template Model** - Message templates
3. **Database Repositories** - PostgreSQL implementation
4. **Gupshup Adapter** - Real aggregator integration

### Priority 2: API & Workers (Week 2)
1. **FastAPI Application** - REST API endpoints
2. **Campaign Orchestrator** - Background worker
3. **Message Dispatcher** - Sending worker
4. **Webhook Processor** - Status update handler

### Priority 3: Testing & Observability (Week 3)
1. **Unit Tests** - Test all domain logic
2. **Integration Tests** - End-to-end flows
3. **Metrics & Tracing** - Prometheus + Jaeger
4. **Load Tests** - Performance testing

## 💡 Pro Tips

### 1. Use Type Hints Everywhere
```python
# Good ✅
async def create_campaign(
    tenant_id: UUID,
    name: str,
    template_id: UUID,
) -> Campaign:
    ...

# Bad ❌
async def create_campaign(tenant_id, name, template_id):
    ...
```

### 2. Keep Domain Pure
```python
# Domain models should have ZERO dependencies
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
# ❌ No FastAPI, SQLAlchemy, or external libs!

class Campaign:
    def activate(self) -> None:
        # Pure business logic
        if self.status != CampaignStatus.SCHEDULED:
            raise InvalidStateTransition(...)
```

### 3. Test Business Logic First
```python
# Test domain models without database or API
def test_campaign_lifecycle():
    campaign = Campaign.create(...)
    assert campaign.status == CampaignStatus.DRAFT
    
    campaign.schedule(future_date)
    assert campaign.status == CampaignStatus.SCHEDULED
    
    campaign.activate()
    assert campaign.status == CampaignStatus.ACTIVE
```

### 4. Use Dependency Injection
```python
# Services receive dependencies, don't create them
class CampaignService:
    def __init__(
        self,
        uow: UnitOfWork,
        queue: QueuePort,
    ):
        self.uow = uow
        self.queue = queue

# Makes testing easy - inject mocks
service = CampaignService(
    uow=MockUnitOfWork(),
    queue=MockQueue(),
)
```

## 🐛 Common Issues

### Database Connection Failed
```bash
# Check if PostgreSQL is running
docker-compose ps postgres

# View logs
docker-compose logs postgres

# Restart
docker-compose restart postgres
```

### Import Errors
```bash
# Make sure you're in the virtual environment
source venv/bin/activate

# Make sure all __init__.py files exist
find apps -type d -exec touch {}/__init__.py \;

# Install in development mode
pip install -e .
```

### Port Already in Use
```bash
# Find process using port
lsof -i :5432  # PostgreSQL
lsof -i :5672  # RabbitMQ

# Kill process or change port in docker-compose.yml
```

## 📖 Learning Resources

- **Clean Architecture**: "Clean Architecture" by Robert C. Martin
- **DDD**: "Domain-Driven Design" by Eric Evans
- **FastAPI**: https://fastapi.tiangolo.com/
- **SQLAlchemy**: https://docs.sqlalchemy.org/
- **RabbitMQ**: https://www.rabbitmq.com/tutorials/

## 🤝 Need Help?

Check these files:
1. `README.md` - Project overview
2. `docs/IMPLEMENTATION_GUIDE.md` - Next steps & architecture
3. `apps/core/domain/campaign.py` - Domain model examples
4. `apps/core/services/campaign_service.py` - Service layer patterns

## ✅ Verification Checklist

- [ ] Docker containers running (postgres, rabbitmq, redis, jaeger)
- [ ] Virtual environment activated
- [ ] Dependencies installed (`pip list` shows fastapi, sqlalchemy, etc.)
- [ ] Environment variables configured (.env file)
- [ ] Domain models importable (`python -c "from apps.core.domain.campaign import Campaign"`)
- [ ] Test script runs successfully

**Ready to build the rest? Let's go! 🚀**
