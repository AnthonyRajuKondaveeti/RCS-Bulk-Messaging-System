# System Runbook & Operations Guide

This document provides the step-by-step flow to run, test, and debug the RCS Messaging System "manually". Use this to verify the system works from end-to-end.

---

## 🚀 1. Quick Local Verification (Run This First!)

Before running the full system, run this self-contained test script. It verifies the database, RabbitMQ, and Domain Logic **without** needing the API server or Workers running.

**Prerequisites:**
1. Docker containers running (`postgres`, `rabbitmq`).
2. Virtual environment active.

```powershell
# 1. Start Infrastructure (if not running)
docker-compose up -d postgres rabbitmq

### 2. Running Local Tests
We have a safe, local test script that mocks the RCS provider (no cost).

**New Architecture:** this test now submits messages to RabbitMQ and **waits** for the background `worker` container to process them. This ensures the entire flow (DB -> Queue -> Worker) is working.

```powershell
# 1. Ensure Docker stack (including worker) is running
docker-compose up -d

# 2. Run the test
python tests/local/test_local.py
```

**What this does:**
*   Creates a generic Campaign & Template.
*   **Populates Tables**: Creates sample data in `templates`, `audiences`, and `opt_ins` tables.
*   Sends test messages using a **Mock Adapter** (no real SMS sent).
*   Verifies that messages are queued in RabbitMQ and logged in PostgreSQL.
*   **Success Output:** `✅ ALL TESTS PASSED!`

---

## 🛠️ 2. Full System Execution (API + Workers)

To### 1. Starting the Stack
The system runs on Docker Compose. This starts:
*   **PostgreSQL** (Database)
*   **RabbitMQ** (Message Queue)
*   **Redis** (Cache)
*   **Jaeger** (Tracing)
*   **Worker** (Background Processing)

To start the full stack:
```powershell
docker-compose up -d
```
*Check status:* `docker-compose ps`

### Step B: Start API Server (Terminal 1)
Handles REST API requests (Campaign creation, sending).

```powershell
.\venv\Scripts\activate
python -m apps.api.main
```
*   **URL:** `http://localhost:8000`
*   **Docs:** `http://localhost:8000/docs`

### Step C: Start Background Workers (Terminal 2)
Processes the queue and handles message delivery.

```powershell
.\venv\Scripts\activate
python -m apps.workers.manager
```
*   **Logs:** Watch this terminal for "Processing message..." logs.

---

### 3. Monitoring Logs
To view logs from **both** the local test script and the Docker containers simultaneously, use the provided PowerShell script:

```powershell
.\scripts\monitor_logs.ps1
```

This script will:
1.  Open a **new window** streaming `docker-compose logs -f`.
2.  Tail the `logs/test_local.log` file in the **current window**.

Alternatively, you can view the local test logs manually:
```powershell
Get-Content logs/test_local.log -Wait
```

### 4. Component-Specific Logs (Infrastructure)
The Docker log stream includes output from all services. To view logs for specific infrastructure components, use:
*   **Worker**: `docker-compose logs -f worker`
*   **Redis**: `docker-compose logs -f redis`
*   **Jaeger**: `docker-compose logs -f jaeger`
*   **Prometheus**: `docker-compose logs -f prometheus`
*   **RabbitMQ**: `docker-compose logs -f rabbitmq`

---

## 📮 5. API Testing with Postman

You can test the full flow using Postman.

### Setup
1.  **Import Collection**: Create a new collection in Postman or import the JSON provided in `tests/TESTING.md` (if available).
2.  **Environment Variables**:
    *   `base_url`: `http://localhost:8000`
    *   `api_key`: `test_api_key_...` (See `.env` or use `test_api_key_12345678901234567890123456789012`)

### Test Flow
1.  **Health Check**:
    *   `GET {{base_url}}/health`
    *   **Expect**: `200 OK` `{"status": "healthy"}`

2.  **Create Campaign**:
    *   `POST {{base_url}}/api/v1/campaigns`
    *   **Headers**: `X-API-Key: {{api_key}}`
    *   **Body**:
        ```json
        {
          "name": "Postman Test Campaign",
          "template_id": "550e8400-e29b-41d4-a716-446655440000",
          "campaign_type": "promotional",
          "priority": "high"
        }
        ```
    *   **Expect**: `201 Created` with `"id": "..."`

3.  **Check Logs**:
    *   Look at **Terminal 2 (Workers)**. You should see:
        *   `Received task: campaign.orchestrator`
        *   `Processing campaign ...`
        *   `Message sent (Mock) ...`

---

## 🐘 4. Database Access (pgAdmin)

To inspect tables and data manually using verified credentials:

| Parameter | Value |
| :--- | :--- |
| **Host** | `localhost` (or `127.0.0.1`) |
| **Port** | `5433` (mapped from Docker 5432) |
| **Database** | `rcs_platform_dev` |
| **User** | `postgres` |
| **Password** | `rcs_dev_pass` |

**Connection Steps:**
1.  Open pgAdmin / DBeaver.
2.  Create new Server Connection.
3.  Enter the details above.
4.  Save and connect.

---

## 🔍 5. Accessing & Debugging Logs

If something fails, verify the logs using these commands:

### A. Infrastructure Logs (Docker)
Connection refusals usually mean a container is down.

```powershell
# PostgreSQL (Database connection errors)
docker logs rcs-postgres

# RabbitMQ (Queue connection errors)
docker logs rcs-rabbitmq
```

### B. Application Logs
*   **API Errors**: Check **Terminal 1** output.
*   **Delivery Failures**: Check **Terminal 2** output.
*   **Log Files**: Check the `logs/` directory (if configured in `.env`).

---

## 🧹 6. Clean Start (Reset)

To wipe everything and start fresh:

```powershell
# Stop everything and delete data volumes
docker-compose down -v

# Re-create DB schema
docker-compose up -d postgres rabbitmq
.\venv\Scripts\activate
alembic upgrade head
```
