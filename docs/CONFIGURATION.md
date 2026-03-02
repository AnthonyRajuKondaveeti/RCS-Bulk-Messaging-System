# Configuration Reference

All config is driven by environment variables. Copy `.env.example` → `.env` for dev. Use `.env.prod.example` → `.env.prod` for production.

---

## Environment

| Variable | Default | Description |
|---|---|---|
| `ENVIRONMENT` | `dev` | `dev`, `staging`, or `prod` |
| `DEBUG` | `true` | Must be `false` in production |

---

## Database

| Variable | Default | Description |
|---|---|---|
| `DB_HOST` | `127.0.0.1` | PostgreSQL hostname |
| `DB_PORT` | `5433` | PostgreSQL port |
| `DB_NAME` | `rcs_platform_dev` | Database name |
| `DB_USERNAME` | `postgres` | Username — note: `DB_USERNAME` not `DB_USER` |
| `DB_PASSWORD` | `rcs_dev_pass` | Password |

---

## RabbitMQ

| Variable | Required | Description |
|---|---|---|
| `RABBITMQ_HOST` | — | Hostname (Docker: service name) |
| `RABBITMQ_PORT` | — | Default `5672` |
| `RABBITMQ_USERNAME` | ✓ | No default — must be set |
| `RABBITMQ_PASSWORD` | ✓ | No default — must be set |

---

## Redis

| Variable | Default | Description |
|---|---|---|
| `REDIS_HOST` | `localhost` | Redis hostname |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_PASSWORD` | _(empty)_ | Leave empty if no auth |

---

## rcssms.in Aggregator

| Variable | Required | Description |
|---|---|---|
| `RCS_USERNAME` | ✓ | rcssms.in account username |
| `RCS_PASSWORD` | ✓ | rcssms.in account password |
| `RCS_ID` | ✓ | Bot / RCS sender ID |
| `RCS_CLIENT_SECRET` | — | Client secret for bearer token auth |
| `RCS_USE_BEARER` | `false` | Set `true` to use bearer token instead of password |
| `USE_MOCK_AGGREGATOR` | `false` | Set `true` to use mock adapter (no real API calls) |

---

## Security

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | ✓ | Min 32 chars. Generate: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | JWT expiry |
| `CORS_ORIGINS` | ✓ in prod | Comma-separated allowed origins. No wildcards in production. |
| `METRICS_TOKEN` | — | Token required to scrape `/metrics`. Unset = open (dev only). |

---

## Rate Limiting

| Variable | Default | Description |
|---|---|---|
| `RATE_LIMIT_ENABLED` | `true` | Enable Redis-based rate limiting |
| `DEFAULT_RATE_LIMIT` | `100` | Requests per minute per API key |
| `RCSSMS_RATE_LIMIT` | `500` | Max messages/second to rcssms.in |

---

## Retry & Fallback

| Variable | Default | Description |
|---|---|---|
| `MAX_RETRIES` | `3` | Max send attempts per message |
| `RETRY_BACKOFF` | `60` | Seconds between retry attempts |
| `ENABLE_FALLBACK` | `true` | Enable fallback on RCS failure |
| `FALLBACK_DELAY` | `300` | Seconds before triggering fallback |

---

## Observability

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | `json` | `json` or `console` |
| `METRICS_PORT` | `9090` | Prometheus metrics port |
| `JAEGER_ENDPOINT` | _(empty)_ | Jaeger collector URL for tracing |

---

## Production Checklist

- [ ] `SECRET_KEY` ≥ 32 chars, not the placeholder value
- [ ] `DEBUG=false`
- [ ] `ENVIRONMENT=prod`
- [ ] `CORS_ORIGINS` lists explicit origins (no `*`)
- [ ] `METRICS_TOKEN` set to protect `/metrics`
- [ ] `RABBITMQ_USERNAME` / `RABBITMQ_PASSWORD` not default `guest/guest`
- [ ] `REDIS_PASSWORD` set
- [ ] `RCS_CLIENT_SECRET` set (required for webhook HMAC validation)
