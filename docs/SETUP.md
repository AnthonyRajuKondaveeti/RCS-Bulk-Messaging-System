# Setup & Installation

## Prerequisites

- Python 3.11+
- Docker + Docker Compose v2
- Active rcssms.in account with an approved bot/RCS ID

---

## 1. Environment

```bash
cp .env.example .env
```

Open `.env` and set at minimum:

```env
RCS_USERNAME=your_username
RCS_PASSWORD=your_password
RCS_ID=your_bot_rcs_id
SECRET_KEY=<output of: python -c "import secrets; print(secrets.token_hex(32))">
```

See `docs/CONFIGURATION.md` for all variables.

---

## 2. Start Infrastructure

```bash
docker-compose up -d postgres redis rabbitmq
docker-compose ps   # all three should show healthy
```

RabbitMQ management UI: `http://localhost:15672` (guest / guest)

---

## 3. Install Dependencies

```bash
pip install -r requirements.txt
```

---

## 4. Run Migrations

```bash
alembic upgrade head
```

Verify tables exist:
```bash
docker exec -it <postgres_container> psql -U postgres -d rcs_platform_dev -c '\dt'
```

Expected tables: `campaigns`, `messages`, `templates`, `audiences`, `audience_contacts`, `opt_ins`, `events`, `api_keys`

---

## 5. Start the API

```bash
uvicorn apps.api.main:app --host 0.0.0.0 --port 8000 --reload
```

Check it's up:
```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready   # probes DB + RabbitMQ
```

API docs: `http://localhost:8000/docs`

---

## 6. Start Workers

Open two terminals:

```bash
# Terminal 1
python -m apps.workers.entrypoints.orchestrator

# Terminal 2
python -m apps.workers.entrypoints.dispatcher
```

Both should log `connected` and `waiting for messages`.

---

## First Send (Mock Mode)

Test the full pipeline without real rcssms.in credentials:

```bash
# In .env
USE_MOCK_AGGREGATOR=true
```

Then restart API + workers and run:

```bash
# 1. Create a template
curl -X POST http://localhost:8000/api/v1/templates \
  -H "X-API-Key: your_key" -H "Content-Type: application/json" \
  -d '{"name":"Test","content":"Hello {{1}}!","rcs_type":"BASIC","variables":[{"name":"name","required":true}]}'

# 2. Approve it manually (mock mode — no rcssms.in needed)
curl -X POST http://localhost:8000/api/v1/templates/{id}/approve \
  -H "X-API-Key: your_key" -H "Content-Type: application/json" \
  -d '{"external_template_id":"mock-001"}'

# 3. Create audience + add a contact
curl -X POST http://localhost:8000/api/v1/audiences \
  -H "X-API-Key: your_key" -H "Content-Type: application/json" \
  -d '{"name":"Test Audience"}'

curl -X POST http://localhost:8000/api/v1/audiences/{id}/contacts \
  -H "X-API-Key: your_key" -H "Content-Type: application/json" \
  -d '{"contacts":[{"phone_number":"+919876543210","variables":["John"]}]}'

# 4. Create + activate campaign
curl -X POST http://localhost:8000/api/v1/campaigns \
  -H "X-API-Key: your_key" -H "Content-Type: application/json" \
  -d '{"name":"Test Campaign","template_id":"<template_id>","campaign_type":"BROADCAST","audience_ids":["<audience_id>"]}'

curl -X POST http://localhost:8000/api/v1/campaigns/{id}/activate \
  -H "X-API-Key: your_key"
```

Check campaign stats — messages should reach `DELIVERED` within ~1 second.

---

## Production

Copy and fill in production values:

```bash
cp .env.prod.example .env.prod
```

See production checklist in `docs/CONFIGURATION.md`.
