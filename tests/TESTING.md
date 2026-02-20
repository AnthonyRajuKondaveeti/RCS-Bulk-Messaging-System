# 🧪 RCS Platform - Testing Guide

This guide covers how to test the RCS platform locally using our verified test scripts and Postman.

## 🚀 1. Quick Local Test (Verified)

The easiest way to verify the system is using the self-contained local test script. This runs **Domain Models**, **Mock Services**, and an **End-to-End Flow**.

**Command:**
```powershell
# Ensure venv is active and Docker (postgres, rabbitmq) is running
python tests/local/test_local.py
```

**What it tests:**
*   ✅ **Domain Logic**: Campaign state changes, Template rendering.
*   ✅ **Services**: Campaign creation, Message dispatching (using Mock Adapter).
*   ✅ **End-to-End**: Full flow from creation to delivery logging in PostgreSQL.

---

## 📮 2. Postman Testing

For API testing, use the following Postman configuration.

### Collection JSON
Save this as `RCS_Platform.postman_collection.json` and import into Postman.

```json
{
  "info": {
    "name": "RCS Platform API",
    "description": "Complete API testing collection",
    "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
  },
  "variable": [
    {
      "key": "base_url",
      "value": "http://localhost:8000",
      "type": "string"
    },
    {
      "key": "api_key",
      "value": "your-api-key-here",
      "type": "string"
    }
  ],
  "item": [
    {
      "name": "Health Check",
      "request": {
        "method": "GET",
        "header": [],
        "url": {
          "raw": "{{base_url}}/health",
          "host": ["{{base_url}}"],
          "path": ["health"]
        }
      }
    },
    {
      "name": "Create Campaign",
      "request": {
        "method": "POST",
        "header": [
          {
            "key": "X-API-Key",
            "value": "{{api_key}}",
            "type": "text"
          },
          {
            "key": "Content-Type",
            "value": "application/json",
            "type": "text"
          }
        ],
        "body": {
          "mode": "raw",
          "raw": "{\n  \"name\": \"Test Campaign\",\n  \"template_id\": \"550e8400-e29b-41d4-a716-446655440000\",\n  \"campaign_type\": \"promotional\",\n  \"priority\": \"high\"\n}"
        },
        "url": {
          "raw": "{{base_url}}/api/v1/campaigns",
          "host": ["{{base_url}}"],
          "path": ["api", "v1", "campaigns"]
        }
      }
    }
  ]
}
```

### Environment Setup
1.  Set `base_url` to `http://localhost:8000`.
2.  Set `api_key` to the key in your `.env` file (e.g., `test_api_key_...`).

---

## 🏗️ 3. Running Integration Tests

For more advanced testing involving workers:

1.  Start the full stack: `docker-compose up -d`
2.  Start API: `python -m apps.api.main`
3.  Start Workers: `python -m apps.workers.manager`
4.  Trigger campaigns via Postman and watch worker logs.
