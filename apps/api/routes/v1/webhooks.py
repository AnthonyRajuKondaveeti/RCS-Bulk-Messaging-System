from apps.api.middleware.auth import get_current_tenant, validate_rcssms_webhook_signature
"""
Webhook API Routes

Handles DLR (delivery status) callbacks from rcssms.in.

Endpoints:
    POST /webhooks/rcssms   - rcssms.in DLR callback
    POST /webhooks/generic  - Generic handler for testing
    GET  /webhooks/test     - Health check

rcssms.in DLR Payload:
    {
        "msgid": "example-20231230133500309785919999999999",
        "msisdn": "+919999999999",
        "status": "DELIVERED" | "FAILED" | "SENT" | "READ"
    }

Template approval DLR:
    {
        "templateid": "7U5QvSVi5e",
        "status": "APPROVED" | "REJECTED"
    }
"""

import logging
from typing import Dict, Any

from fastapi import APIRouter, Request, HTTPException, status, BackgroundTasks
from pydantic import BaseModel

from apps.adapters.queue.rabbitmq import RabbitMQAdapter
from apps.core.ports.queue import QueueMessage, QueuePriority
from apps.core.config import get_settings


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


class WebhookResponse(BaseModel):
    """Webhook acknowledgment response"""
    status: str = "received"
    webhook_id: str


@router.post("/rcssms", response_model=WebhookResponse)
async def rcssms_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    rcssms.in DLR callback endpoint.

    Receives delivery status updates (DLR) and template approval
    notifications from rcssms.in and queues them for async processing.

    Register this URL in your rcssms.in account as the DLR endpoint.

    Expected payloads:
        Delivery update: { "msgid": "...", "msisdn": "...", "status": "DELIVERED" }
        Template approval: { "templateid": "...", "status": "APPROVED" }
    """
    try:
        # FIX (GAP 24): Validate HMAC-SHA256 signature before processing.
        # rcssms.in signs every callback with X-RcsSms-Signature header.
        # We read the raw body first (before parsing JSON) so the bytes
        # match exactly what rcssms.in signed.
        raw_body = await request.body()
        signature = request.headers.get("X-RcsSms-Signature", "")

        from apps.core.config import get_settings
        settings = get_settings()
        client_secret = settings.rcssms.client_secret if settings.rcssms else None

        if not validate_rcssms_webhook_signature(raw_body, signature, client_secret or ""):
            logger.warning(
                "Invalid webhook signature from %s",
                request.client.host if request.client else "unknown",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature",
            )

        import json as _json
        payload = _json.loads(raw_body)
        headers = dict(request.headers)

        # Support both DLR types
        webhook_id = (
            payload.get("msgid")
            or payload.get("templateid")
            or "unknown"
        )
        event_status = payload.get("status", "unknown")

        logger.info(
            "Received rcssms.in webhook: id=%s status=%s",
            webhook_id, event_status,
        )

        background_tasks.add_task(
            queue_webhook_for_processing,
            aggregator="rcssms",
            payload=payload,
            headers=headers,
        )

        return WebhookResponse(
            status="received",
            webhook_id=webhook_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error processing rcssms.in webhook")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Webhook processing error: {str(e)}",
        )


@router.post("/generic", response_model=WebhookResponse)
async def generic_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    aggregator: str = "unknown",
):
    """
    Generic webhook handler for testing and development.

    Query params:
        aggregator: Name of aggregator (rcssms, mock, etc.)
    """
    try:
        payload = await request.json()
        headers = dict(request.headers)

        logger.info(f"Received generic webhook from {aggregator}")

        background_tasks.add_task(
            queue_webhook_for_processing,
            aggregator=aggregator,
            payload=payload,
            headers=headers,
        )

        return WebhookResponse(
            status="received",
            webhook_id=payload.get("msgid") or payload.get("id", "unknown"),
        )

    except Exception as e:
        logger.exception("Error processing generic webhook")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Webhook processing error: {str(e)}",
        )


@router.get("/test")
async def test_webhook():
    """Test endpoint to verify webhooks are configured correctly."""
    settings = get_settings()

    return {
        "status": "webhooks_active",
        "endpoints": {
            "rcssms_dlr": "/api/v1/webhooks/rcssms",
            "generic": "/api/v1/webhooks/generic",
        },
        "queue": settings.queue_names.get("webhook_processor"),
        "note": "Register /api/v1/webhooks/rcssms as your DLR URL in rcssms.in portal",
    }


async def queue_webhook_for_processing(
    aggregator: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
) -> None:
    """
    Queue webhook for asynchronous processing by webhook_processor worker.

    Args:
        aggregator: Aggregator name (rcssms, etc.)
        payload:    Webhook POST body
        headers:    HTTP headers
    """
    try:
        settings = get_settings()

        queue = RabbitMQAdapter(url=settings.rabbitmq.url)
        await queue.connect()

        from uuid import uuid4
        webhook_id = str(uuid4())

        message = QueueMessage(
            id=webhook_id,
            queue_name=settings.queue_names["webhook_processor"],
            payload={
                "webhook_id": webhook_id,
                "aggregator": aggregator,
                "payload": payload,
                "headers": headers,
            },
            priority=QueuePriority.HIGH,
        )

        await queue.enqueue(message)

        logger.debug(f"Webhook {webhook_id} queued for processing")

        await queue.close()

    except Exception as e:
        logger.exception(f"Failed to queue webhook: {e}")
        # Don't raise — webhook was received, processing will be retried
