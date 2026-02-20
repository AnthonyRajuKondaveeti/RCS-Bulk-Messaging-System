# RCS Messaging Platform

Enterprise-grade Rich Communication Services (RCS) platform with SMS fallback, built using Clean Architecture and Domain-Driven Design principles.

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        API Layer                             │
│  FastAPI REST API │ WebSocket │ Webhooks │ Health Checks    │
└─────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────┐
│                      Service Layer                           │
│  Campaign Service │ Delivery Service │ Fallback Service     │
└─────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────┐
│                      Domain Layer                            │
│  Campaign │ Message │ Template │ Opt-In (Pure Business)    │
└─────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────┐
│                    Infrastructure Layer                      │
│  PostgreSQL │ RabbitMQ │ Redis │ Gupshup │ Route Mobile    │
└─────────────────────────────────────────────────────────────┘
```

### Key Design Patterns

- **Hexagonal Architecture**: Core business logic isolated from infrastructure
- **Domain-Driven Design**: Rich domain models with behavior
- **CQRS**: Separate read/write models for scalability
- **Event Sourcing**: Domain events for audit trail
- **Repository Pattern**: Abstract data access
- **Unit of Work**: Transaction management

## 🚀 Features

### Core Capabilities
- ✅ **Multi-tenant architecture** with tenant isolation
- ✅ **RCS messaging** with rich cards, suggestions, and media
- ✅ **Automatic SMS fallback** when RCS unavailable
- ✅ **Campaign management** with scheduling and orchestration
- ✅ **Message templates** with variable substitution
- ✅ **Delivery tracking** with webhooks and status updates
- ✅ **Rate limiting** per tenant and aggregator
- ✅ **Retry logic** with exponential backoff
- ✅ **Dead Letter Queue** for failed messages
- ✅ **Opt-out management** and compliance

### Advanced Features
- 📊 **Real-time analytics** with Prometheus metrics
- 🔍 **Distributed tracing** with OpenTelemetry
- 🎯 **Audience segmentation** and targeting
- 📅 **Campaign scheduling** with cron support
- 🔄 **Webhook callbacks** for delivery events
- 🔐 **API key authentication** and JWT tokens
- 📈 **Horizontal scaling** with stateless workers

## 📦 Technology Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Language** | Python 3.11+ | Core platform |
| **Framework** | FastAPI | REST API |
| **Database** | PostgreSQL 15+ | Primary data store |
| **Cache** | Redis | Caching & rate limiting |
| **Queue** | RabbitMQ | Async job processing |
| **ORM** | SQLAlchemy | Database access |
| **Migration** | Alembic | Schema migrations |
| **Metrics** | Prometheus | Observability |
| **Tracing** | Jaeger | Distributed tracing |
| **Aggregators** | Gupshup, Route Mobile | RCS/SMS delivery |

## 📁 Project Structure

```
rcs-platform/
├── apps/
│   ├── api/                    # FastAPI REST API
│   │   ├── main.py            # Application entry point
│   │   ├── middleware/        # Auth, tenancy, rate limiting
│   │   └── routes/v1/         # API endpoints (versioned)
│   │
│   ├── workers/               # Stateless background workers
│   │   ├── orchestrator/      # Campaign orchestration
│   │   ├── dispatcher/        # Message dispatching
│   │   ├── fallback/          # SMS fallback handler
│   │   └── events/            # Webhook processing
│   │
│   ├── core/
│   │   ├── domain/            # 🎯 PURE BUSINESS LOGIC
│   │   │   ├── campaign.py    # Campaign aggregate
│   │   │   ├── message.py     # Message entity
│   │   │   └── template.py    # Template value object
│   │   │
│   │   ├── services/          # 🔧 ORCHESTRATION LAYER
│   │   │   ├── campaign_service.py
│   │   │   ├── delivery_service.py
│   │   │   └── fallback_service.py
│   │   │
│   │   ├── ports/             # 🔌 INTERFACES (Critical!)
│   │   │   ├── aggregator.py  # RCS/SMS abstraction
│   │   │   ├── queue.py       # Message queue interface
│   │   │   └── repository.py  # Data persistence interface
│   │   │
│   │   └── config.py          # Configuration management
│   │
│   └── adapters/              # 🔨 INFRASTRUCTURE IMPLEMENTATIONS
│       ├── aggregators/       # Vendor integrations
│       │   ├── gupshup_adapter.py
│       │   └── route_adapter.py
│       │
│       ├── queue/
│       │   ├── rabbitmq.py
│       │   └── dlq_handler.py
│       │
│       └── db/
│           ├── postgres.py
│           └── repositories/
│
├── infra/
│   ├── docker/                # Docker configurations
│   ├── config/                # Environment configs
│   │   ├── dev.yaml
│   │   ├── staging.yaml
│   │   └── prod.yaml
│   └── migrations/            # Database migrations
│
├── tests/
│   ├── unit/                  # Unit tests
│   ├── integration/           # Integration tests
│   └── load/                  # Load tests
│
└── docs/                      # Documentation
```

## 🎯 Domain Model

### Campaign Lifecycle

```
DRAFT ──schedule()──> SCHEDULED ──activate()──> ACTIVE ──complete()──> COMPLETED
  │                                               │
  │                                               ├──pause()──> PAUSED
  └──────────────cancel()────────────────────────┴──cancel()──> CANCELLED
```

### Message Lifecycle

```
PENDING ──queue()──> QUEUED ──send()──> SENT ──delivered()──> DELIVERED ──read()──> READ
                        │                 │
                        │                 └──failed()──> FAILED ──fallback()──> FALLBACK_SENT
                        │
                        └──failed()──> FAILED (max retries) ──> DLQ
```

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Docker & Docker Compose
- PostgreSQL 15+
- RabbitMQ 3.12+
- Redis 7+

### Installation

```bash
# Clone repository
git clone https://github.com/yourorg/rcs-platform.git
cd rcs-platform

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set environment variables
cp .env.example .env
# Edit .env with your credentials

# Start infrastructure (PostgreSQL, RabbitMQ, Redis)
docker-compose up -d

# Run migrations
alembic upgrade head

# Start API server
python -m apps.api.main

# Start workers (in separate terminals)
python -m apps.workers.orchestrator.campaign_orchestrator
python -m apps.workers.dispatcher.message_dispatcher
python -m apps.workers.fallback.sms_fallback_worker
```

## 📊 Configuration

Configuration is loaded from:
1. YAML files (`infra/config/{env}.yaml`)
2. Environment variables (override YAML)
3. Secrets manager (production)

### Environment Variables

```bash
# Required
ENVIRONMENT=dev
DB_PASSWORD=your-db-password
RABBITMQ_PASSWORD=your-queue-password
SECRET_KEY=your-secret-key

# Gupshup Credentials
GUPSHUP_API_KEY=your-api-key
GUPSHUP_APP_NAME=your-app-name
GUPSHUP_WEBHOOK_SECRET=your-webhook-secret

# Optional
DEBUG=true
LOG_LEVEL=INFO
```

## 🔧 Usage Examples

### Create Campaign

```python
from apps.core.services.campaign_service import CampaignService
from apps.core.domain.campaign import CampaignType, Priority

# Create campaign
campaign = await campaign_service.create_campaign(
    tenant_id=tenant_id,
    name="Black Friday Sale",
    template_id=template_id,
    campaign_type=CampaignType.PROMOTIONAL,
    priority=Priority.HIGH,
)

# Add audience
await campaign_service.add_audience(
    campaign_id=campaign.id,
    audience_id=audience_id,
    recipient_phones=["+919876543210", "+919876543211"],
)

# Schedule campaign
await campaign_service.schedule_campaign(
    campaign_id=campaign.id,
    scheduled_for=datetime(2024, 11, 29, 9, 0),
)
```

### Send RCS Message

```python
from apps.core.domain.message import MessageContent, RichCard, SuggestedAction

# Create rich content
content = MessageContent(
    text="Your order #1234 has shipped! 🚚",
    rich_card=RichCard(
        title="Track Your Order",
        description="Estimated delivery: Nov 30",
        media_url="https://cdn.example.com/package.jpg",
        suggestions=[
            SuggestedAction(
                type="url",
                text="Track Package",
                url="https://track.example.com/1234"
            )
        ]
    )
)

# Send message
message = await delivery_service.send_message(
    campaign_id=campaign_id,
    recipient_phone="+919876543210",
    content=content,
)
```

## 🧪 Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=apps --cov-report=html

# Run specific test suite
pytest tests/unit/
pytest tests/integration/

# Run load tests
python tests/load/send_10k_messages.py
```

## 📈 Monitoring

### Metrics (Prometheus)
- `http://localhost:9090/metrics` - API metrics
- Campaign delivery rates
- Message queue depths
- Aggregator response times
- Error rates by type

### Tracing (Jaeger)
- `http://localhost:16686` - Jaeger UI
- End-to-end request tracing
- Service dependency graph

### Logs
- JSON structured logging
- Correlation IDs for request tracking
- Log levels: DEBUG, INFO, WARNING, ERROR, CRITICAL

## 🔒 Security

- ✅ API key authentication
- ✅ JWT token-based auth
- ✅ Webhook signature verification
- ✅ Rate limiting per tenant
- ✅ SQL injection prevention (ORM)
- ✅ Input validation (Pydantic)
- ✅ CORS configuration

## 📖 API Documentation

Once the server is running, visit:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

## 🤝 Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

### Code Style
- Follow PEP 8
- Use type hints
- Write docstrings (Google style)
- Maintain test coverage >80%

## 📄 License

This project is licensed under the MIT License - see LICENSE file for details.

## 🙋 Support

- 📧 Email: support@example.com
- 💬 Slack: #rcs-platform
- 🐛 Issues: https://github.com/yourorg/rcs-platform/issues

## 🗺️ Roadmap

- [ ] WhatsApp integration
- [ ] Advanced analytics dashboard
- [ ] A/B testing for campaigns
- [ ] Template marketplace
- [ ] Multi-language support
- [ ] GraphQL API
- [ ] Kubernetes deployment configs
- [ ] AI-powered send time optimization

---

Built with ❤️ using Clean Architecture principles
