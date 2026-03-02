# RCS Messaging Platform

Enterprise bulk RCS messaging platform for the Indian market, built on rcssms.in.

## Stack

| Layer | Technology |
|---|---|
| API | FastAPI (Python 3.11+) |
| Database | PostgreSQL 15 + SQLAlchemy (asyncpg) |
| Queue | RabbitMQ (aio-pika) |
| Cache / Circuit Breaker | Redis |
| RCS Aggregator | rcssms.in JSON API |
| Migrations | Alembic |
| Containers | Docker + Docker Compose |

## Quick Start

```bash
# 1. Configure environment
cp .env.example .env
# Fill in RCS_USERNAME, RCS_PASSWORD, RCS_ID, SECRET_KEY

# 2. Start infrastructure
docker-compose up -d postgres redis rabbitmq

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run migrations
alembic upgrade head

# 5. Start API
uvicorn apps.api.main:app --host 0.0.0.0 --port 8000 --reload

# 6. Start workers (two terminals)
python -m apps.workers.entrypoints.orchestrator
python -m apps.workers.entrypoints.dispatcher
```

API docs available at `http://localhost:8000/docs` (dev mode only).

## Testing Without Real Credentials

Set `USE_MOCK_AGGREGATOR=true` in `.env`. The mock adapter simulates 95% delivery success with 100ms latency.

## How It Works

1. Client creates a **Template** → submits for rcssms.in approval → gets `external_template_id`
2. Client creates an **Audience** with contacts (phone + template variables)
3. Client creates a **Campaign** linking template + audience → activates it
4. **Orchestrator worker** streams contacts in batches → creates `Message` rows → publishes to dispatch queue
5. **Dispatcher worker** sends each message via rcssms.in → updates status
6. rcssms.in POSTs **DLR webhooks** → system updates delivery status

## Repository Structure

```
apps/
  api/            FastAPI routes, middleware
  adapters/       DB, queue, and rcssms.in implementations
  core/           Domain models, ports, services (no infra dependencies)
  workers/        Orchestrator and dispatcher entrypoints
infra/
  config/         YAML config per environment
  migrations/     Alembic migration scripts
```

## Docs

| File | Contents |
|---|---|
| `docs/SETUP.md` | Full setup and first-run guide |
| `docs/CONFIGURATION.md` | All environment variables |
| `docs/ARCHITECTURE.md` | Design decisions and component overview |
| `CHANGELOG.md` | Version history |
