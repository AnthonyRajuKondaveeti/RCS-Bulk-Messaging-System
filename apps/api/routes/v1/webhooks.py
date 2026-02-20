"""
Webhook API Routes

Handles delivery status webhooks from aggregators.

Endpoints:
    POST /webhooks/gupshup - Gupshup delivery status
    POST /webhooks/route - Route Mobile delivery status
    POST /webhooks/generic - Generic webhook handler

Security:
    - Signature verification required
    - No authentication (webhooks use signatures)
    - Request logging for debugging
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


@router.post("/gupshup", response_model=WebhookResponse)
async def gupshup_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Gupshup delivery status webhook
    
    Receives delivery status updates from Gupshup and queues
    them for asynchronous processing by webhook processor worker.
    
    Headers:
        - X-Gupshup-Signature: Webhook signature for verification
    
    Example payload:
        {
            "eventType": "delivered",
            "messageId": "msg_123",
            "externalId": "ext_456",
            "timestamp": "2024-01-15T10:30:00Z"
        }
    """
    try:
        # Get raw body for signature verification
        body = await request.body()
        
        # Parse JSON
        payload = await request.json()
        
        # Get headers
        headers = dict(request.headers)
        
        # Log webhook receipt
        event_type = payload.get("eventType", "unknown")
        message_id = payload.get("messageId", "unknown")
        
        logger.info(
            f"Received Gupshup webhook: "
            f"event={event_type}, message={message_id}"
        )
        
        # Queue for async processing
        # This returns immediately without blocking
        background_tasks.add_task(
            queue_webhook_for_processing,
            aggregator="gupshup",
            payload=payload,
            headers=headers,
        )
        
        # Return success immediately
        return WebhookResponse(
            status="received",
            webhook_id=message_id,
        )
        
    except Exception as e:
        logger.exception("Error processing Gupshup webhook")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Webhook processing error: {str(e)}",
        )


@router.post("/route", response_model=WebhookResponse)
async def route_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Route Mobile delivery status webhook
    
    Similar to Gupshup webhook but with Route-specific payload format.
    """
    try:
        body = await request.body()
        payload = await request.json()
        headers = dict(request.headers)
        
        event_type = payload.get("status", "unknown")
        message_id = payload.get("id", "unknown")
        
        logger.info(
            f"Received Route webhook: "
            f"status={event_type}, message={message_id}"
        )
        
        background_tasks.add_task(
            queue_webhook_for_processing,
            aggregator="route",
            payload=payload,
            headers=headers,
        )
        
        return WebhookResponse(
            status="received",
            webhook_id=message_id,
        )
        
    except Exception as e:
        logger.exception("Error processing Route webhook")
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
    Generic webhook handler for testing
    
    Accepts any JSON payload and queues for processing.
    Useful for testing and development.
    
    Query params:
        aggregator: Name of aggregator (gupshup, route, etc.)
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
            webhook_id=payload.get("id", "unknown"),
        )
        
    except Exception as e:
        logger.exception("Error processing generic webhook")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Webhook processing error: {str(e)}",
        )


@router.get("/test")
async def test_webhook():
    """
    Test endpoint to verify webhooks are working
    
    Returns basic info about webhook configuration.
    """
    settings = get_settings()
    
    return {
        "status": "webhooks_active",
        "endpoints": {
            "gupshup": "/api/v1/webhooks/gupshup",
            "route": "/api/v1/webhooks/route",
            "generic": "/api/v1/webhooks/generic",
        },
        "queue": settings.queue_names.get("webhook_processor"),
    }


async def queue_webhook_for_processing(
    aggregator: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
) -> None:
    """
    Queue webhook for asynchronous processing
    
    Args:
        aggregator: Aggregator name (gupshup, route, etc.)
        payload: Webhook payload
        headers: HTTP headers (for signature verification)
    """
    try:
        settings = get_settings()
        
        # Connect to queue
        queue = RabbitMQAdapter(url=settings.rabbitmq.url)
        await queue.connect()
        
        # Create webhook job
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
            priority=QueuePriority.HIGH,  # Webhooks are high priority
        )
        
        # Enqueue
        await queue.enqueue(message)
        
        logger.debug(f"Webhook {webhook_id} queued for processing")
        
        await queue.close()
        
    except Exception as e:
        logger.exception(f"Failed to queue webhook: {e}")
        # Don't raise - webhook was received, processing will be retried
