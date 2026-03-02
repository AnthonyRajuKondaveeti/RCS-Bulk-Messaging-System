# Changelog

## v1.0.0 — March 2025

### Added
- Campaign management API (create, schedule, activate, pause, cancel)
- Template lifecycle with rcssms.in approval workflow (BASIC, RICH, RICHCASOUREL)
- Audience management with normalised `audience_contacts` table and keyset-paginated streaming
- Per-contact template variables via `ContactRequest.variables`
- Orchestrator worker — expands campaigns into messages in memory-safe batches of 500
- Dispatcher worker — sends via rcssms.in with configurable concurrency
- RcsSmsAdapter — full rcssms.in JSON API integration (password + bearer token auth)
- MockAdapter — 95% success / 100ms latency simulation for testing
- Redis-backed distributed circuit breaker shared across all dispatcher replicas
- DLR webhook handler — delivery status and template approval callbacks
- Multi-tenant architecture with API key authentication (SHA-256 hashed keys)
- Sliding window rate limiting via Redis sorted sets
- RabbitMQ Dead Letter Queue per queue via x-dead-letter-exchange
- Publisher confirms on batch enqueue — prevents silent partial-batch failures
- Real readiness probe — actually connects to DB and RabbitMQ (was hardcoded `"ok"`)
- Startup security gate — crashes on insecure config in production
- Structured JSON logging with `request_id` and `tenant_id` context on every line
- Prometheus metrics at `/metrics` with optional `X-Metrics-Token` protection

### Fixed
- `DB_USERNAME` env var mapping (was incorrectly mapped as `DB_USER`)
- `expires_at` now `created_at + 24h` — was broken for late-night messages
- Auth middleware no longer generates random `uuid4()` per request as tenant ID
- Webhook endpoint no longer bypasses authentication entirely
- `HMAC` webhook validation raises `WebhookConfigurationError` if secret is unset — no silent bypass
- Migration revision conflict — two files with `001_` prefix (needs rename to `005_`)
- `apps/config.py` deprecated — all config from `apps/core/config.py`

### Known Issues
- `001_add_rcssms_template_columns.py` needs rename to `005_` to fix Alembic revision chain
- No true SMS fallback — rcssms.in has no SMS endpoint; fallback sends BASIC RCS
- No scheduled campaign polling — campaigns with `scheduled_for` require external trigger
