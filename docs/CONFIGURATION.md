# Configuration Reference

All config is driven by environment variables. Copy `.env.example` → `.env` for dev. Use `.env.prod.example` → `.env.prod` for production.

---

## Environment

| Variable      | Default | Description                   |
| ------------- | ------- | ----------------------------- |
| `ENVIRONMENT` | `dev`   | `dev`, `staging`, or `prod`   |
| `DEBUG`       | `true`  | Must be `false` in production |

---

## Database

| Variable      | Default            | Description                                  |
| ------------- | ------------------ | -------------------------------------------- |
| `DB_HOST`     | `127.0.0.1`        | PostgreSQL hostname                          |
| `DB_PORT`     | `5433`             | PostgreSQL port                              |
| `DB_NAME`     | `rcs_platform_dev` | Database name                                |
| `DB_USERNAME` | `postgres`         | Username — note: `DB_USERNAME` not `DB_USER` |
| `DB_PASSWORD` | `rcs_dev_pass`     | Password                                     |

---

## RabbitMQ

| Variable            | Required | Description                     |
| ------------------- | -------- | ------------------------------- |
| `RABBITMQ_HOST`     | —        | Hostname (Docker: service name) |
| `RABBITMQ_PORT`     | —        | Default `5672`                  |
| `RABBITMQ_USERNAME` | ✓        | No default — must be set        |
| `RABBITMQ_PASSWORD` | ✓        | No default — must be set        |

---

## Redis

| Variable         | Default     | Description            |
| ---------------- | ----------- | ---------------------- |
| `REDIS_HOST`     | `localhost` | Redis hostname         |
| `REDIS_PORT`     | `6379`      | Redis port             |
| `REDIS_PASSWORD` | _(empty)_   | Leave empty if no auth |

---

## rcssms.in Aggregator

| Variable              | Required | Description                                        |
| --------------------- | -------- | -------------------------------------------------- |
| `RCS_USERNAME`        | ✓        | rcssms.in account username                         |
| `RCS_PASSWORD`        | ✓        | rcssms.in account password                         |
| `RCS_ID`              | ✓        | Bot / RCS sender ID                                |
| `RCS_CLIENT_SECRET`   | —        | Client secret for bearer token auth                |
| `RCS_USE_BEARER`      | `false`  | Set `true` to use bearer token instead of password |
| `USE_MOCK_AGGREGATOR` | `false`  | Set `true` to use mock adapter (no real API calls) |

---

## SMS Fallback (smsidea.co.in)

| Variable        | Required | Description                                              |
| --------------- | -------- | -------------------------------------------------------- |
| `SMS_USERNAME`  | ✓        | smsidea.co.in portal login username (e.g., phone number) |
| `SMS_PASSWORD`  | ✓        | smsidea.co.in API key                                    |
| `SMS_SENDER_ID` | ✓        | 6-character DLT-approved sender ID (e.g., VRMGMT)        |
| `SMS_PEID`      | —        | Principal Entity ID from DLT portal (optional)           |
| `SMS_TIMEOUT`   | `30`     | API request timeout in seconds                           |

**Example Configuration:**

```bash
SMS_USERNAME=9895495477
SMS_PASSWORD=d7f03e0447c54bc8962974b76e6e8f64
SMS_SENDER_ID=VRMGMT
```

---

## Security

| Variable                      | Required  | Description                                                                                   |
| ----------------------------- | --------- | --------------------------------------------------------------------------------------------- |
| `SECRET_KEY`                  | ✓         | Min 32 chars. Generate: `python -c "import secrets; print(secrets.token_hex(32))"`            |
| `JWT_ALGORITHM`               | `HS256`   | JWT signing algorithm                                                                         |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30`      | JWT expiry                                                                                    |
| `CORS_ORIGINS`                | ✓ in prod | Comma-separated allowed origins. Example: `https://app.example.com,https://admin.example.com` |
| `METRICS_TOKEN`               | —         | Token required to scrape `/metrics`. Unset = open (dev only).                                 |

**CORS_ORIGINS**

Cross-Origin Resource Sharing (CORS) controls which web origins can make requests to the API.

- **Default:** Empty list (blocks all browser requests)
- **Development:** Set to your local frontend URL (e.g., `http://localhost:3000`)
- **Production:** Comma-separated list of allowed origins, no wildcards
- **Format:** `https://app.example.com,https://admin.example.com`
- **Important:** If not set, the API will start normally but all browser-based requests will fail with CORS errors

Example `.env` configuration:

```bash
# Development
CORS_ORIGINS=http://localhost:3000,http://localhost:3001

# Production
CORS_ORIGINS=https://app.example.com,https://admin.example.com
```

If `CORS_ORIGINS` is not set, you'll see this warning in the logs:

```
CORS_ORIGINS not set — all browser requests will be blocked
```

---

## Rate Limiting

| Variable             | Default | Description                      |
| -------------------- | ------- | -------------------------------- |
| `RATE_LIMIT_ENABLED` | `true`  | Enable Redis-based rate limiting |
| `DEFAULT_RATE_LIMIT` | `100`   | Requests per minute per API key  |
| `RCSSMS_RATE_LIMIT`  | `500`   | Max messages/second to rcssms.in |

---

## Retry & Fallback

| Variable          | Default | Description                        |
| ----------------- | ------- | ---------------------------------- |
| `MAX_RETRIES`     | `3`     | Max send attempts per message      |
| `RETRY_BACKOFF`   | `60`    | Seconds between retry attempts     |
| `ENABLE_FALLBACK` | `true`  | Enable fallback on RCS failure     |
| `FALLBACK_DELAY`  | `300`   | Seconds before triggering fallback |

---

## Observability

| Variable          | Default   | Description                         |
| ----------------- | --------- | ----------------------------------- |
| `LOG_LEVEL`       | `INFO`    | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT`      | `json`    | `json` or `console`                 |
| `METRICS_PORT`    | `9090`    | Prometheus metrics port             |
| `JAEGER_ENDPOINT` | _(empty)_ | Jaeger collector URL for tracing    |

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
