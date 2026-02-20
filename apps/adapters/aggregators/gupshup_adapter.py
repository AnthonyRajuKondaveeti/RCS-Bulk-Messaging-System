"""
Gupshup Aggregator Adapter

Concrete implementation of AggregatorPort for Gupshup RCS/SMS API.
Handles message sending, webhook processing, and capability checks.

API Documentation: https://docs.gupshup.io/

Features:
    - RCS messaging with rich cards
    - SMS fallback
    - Webhook signature verification
    - Rate limiting handling
    - Error mapping
"""

import hmac
import hashlib
import json
from typing import Optional, List, Dict, Any
from datetime import datetime
import httpx
import logging

from apps.core.ports.aggregator import (
    AggregatorPort,
    SendMessageRequest,
    SendMessageResponse,
    DeliveryStatus,
    CapabilityCheckResult,
    AggregatorException,
    ValidationException,
    WebhookValidationException,
    RateLimitException,
)
from apps.core.domain.message import MessageChannel, RichCard, SuggestedAction


logger = logging.getLogger(__name__)


class GupshupAdapter(AggregatorPort):
    """
    Gupshup API adapter
    
    Implements RCS and SMS messaging through Gupshup's API.
    
    Configuration:
        - api_key: Gupshup API key
        - app_name: Application name
        - base_url: API base URL
        - webhook_secret: Secret for webhook verification
        
    Example:
        >>> adapter = GupshupAdapter(
        ...     api_key="your-api-key",
        ...     app_name="your-app",
        ...     webhook_secret="your-secret",
        ... )
        >>> response = await adapter.send_rcs_message(request)
    """
    
    def __init__(
        self,
        api_key: str,
        app_name: str,
        webhook_secret: str,
        base_url: str = "https://api.gupshup.io/wa/api/v1",
        timeout: int = 30,
        max_retries: int = 3,
    ):
        """
        Initialize Gupshup adapter
        
        Args:
            api_key: Gupshup API key
            app_name: Application name
            webhook_secret: Webhook verification secret
            base_url: API base URL
            timeout: Request timeout in seconds
            max_retries: Max retry attempts
        """
        self.api_key = api_key
        self.app_name = app_name
        self.webhook_secret = webhook_secret
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.max_retries = max_retries
        
        # HTTP client
        self.client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "apikey": api_key,
                "Content-Type": "application/json",
            }
        )
    
    async def send_rcs_message(
        self,
        request: SendMessageRequest,
    ) -> SendMessageResponse:
        """
        Send RCS message via Gupshup
        
        Args:
            request: Message send request
            
        Returns:
            Send response with external ID
        """
        try:
            # Build RCS payload
            payload = self._build_rcs_payload(request)
            
            # Send request
            url = f"{self.base_url}/msg"
            response = await self.client.post(url, json=payload)
            
            # Handle response
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "60"))
                raise RateLimitException(
                    "Rate limit exceeded",
                    retry_after=retry_after,
                )
            
            response.raise_for_status()
            result = response.json()
            
            # Parse response
            if result.get("status") == "submitted":
                return SendMessageResponse(
                    success=True,
                    external_id=result.get("messageId"),
                )
            else:
                return SendMessageResponse(
                    success=False,
                    error_code=result.get("errorCode"),
                    error_message=result.get("message"),
                )
                
        except httpx.HTTPStatusError as e:
            logger.error(f"Gupshup API error: {e}")
            error_data = e.response.json() if e.response.text else {}
            return SendMessageResponse(
                success=False,
                error_code=error_data.get("errorCode", str(e.response.status_code)),
                error_message=error_data.get("message", str(e)),
            )
            
        except Exception as e:
            logger.exception("Failed to send RCS message")
            raise AggregatorException(f"Failed to send RCS message: {e}")
    
    async def send_sms_message(
        self,
        request: SendMessageRequest,
    ) -> SendMessageResponse:
        """
        Send SMS message via Gupshup
        
        Args:
            request: Message send request
            
        Returns:
            Send response with external ID
        """
        try:
            # Build SMS payload
            payload = {
                "channel": "sms",
                "source": self.app_name,
                "destination": request.recipient_phone,
                "message": {
                    "type": "text",
                    "text": request.content_text,
                }
            }
            
            # Send request
            url = f"{self.base_url}/msg"
            response = await self.client.post(url, json=payload)
            
            # Handle rate limiting
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "60"))
                raise RateLimitException(
                    "Rate limit exceeded",
                    retry_after=retry_after,
                )
            
            response.raise_for_status()
            result = response.json()
            
            # Parse response
            if result.get("status") == "submitted":
                return SendMessageResponse(
                    success=True,
                    external_id=result.get("messageId"),
                )
            else:
                return SendMessageResponse(
                    success=False,
                    error_code=result.get("errorCode"),
                    error_message=result.get("message"),
                )
                
        except httpx.HTTPStatusError as e:
            logger.error(f"Gupshup SMS error: {e}")
            error_data = e.response.json() if e.response.text else {}
            return SendMessageResponse(
                success=False,
                error_code=error_data.get("errorCode", str(e.response.status_code)),
                error_message=error_data.get("message", str(e)),
            )
            
        except Exception as e:
            logger.exception("Failed to send SMS message")
            raise AggregatorException(f"Failed to send SMS message: {e}")
    
    async def check_rcs_capability(
        self,
        phone_numbers: List[str],
    ) -> List[CapabilityCheckResult]:
        """
        Check RCS capability for phone numbers
        
        Args:
            phone_numbers: List of phone numbers
            
        Returns:
            Capability results for each number
        """
        results = []
        
        try:
            # Gupshup capability check endpoint
            url = f"{self.base_url}/capability"
            
            for phone in phone_numbers:
                payload = {
                    "phone": phone,
                    "channel": "rcs",
                }
                
                response = await self.client.post(url, json=payload)
                response.raise_for_status()
                
                data = response.json()
                
                results.append(
                    CapabilityCheckResult(
                        phone_number=phone,
                        rcs_enabled=data.get("rcsEnabled", False),
                        last_checked=datetime.utcnow(),
                        features=data.get("features", []),
                    )
                )
                
        except Exception as e:
            logger.warning(f"RCS capability check failed: {e}")
            # Return all as not capable on error
            for phone in phone_numbers:
                results.append(
                    CapabilityCheckResult(
                        phone_number=phone,
                        rcs_enabled=False,
                        last_checked=datetime.utcnow(),
                    )
                )
        
        return results
    
    async def get_delivery_status(
        self,
        external_id: str,
    ) -> Optional[DeliveryStatus]:
        """
        Query delivery status from Gupshup
        
        Args:
            external_id: Gupshup message ID
            
        Returns:
            Delivery status or None
        """
        try:
            url = f"{self.base_url}/msg/status/{external_id}"
            response = await self.client.get(url)
            response.raise_for_status()
            
            data = response.json()
            
            return self._parse_delivery_status(data)
            
        except Exception as e:
            logger.error(f"Failed to get delivery status: {e}")
            return None
    
    async def handle_webhook(
        self,
        payload: Dict[str, Any],
        headers: Dict[str, str],
    ) -> Optional[DeliveryStatus]:
        """
        Parse webhook callback from Gupshup
        
        Args:
            payload: Webhook request body
            headers: HTTP headers
            
        Returns:
            Parsed delivery status
            
        Raises:
            WebhookValidationException: If signature invalid
        """
        # Verify signature
        signature = headers.get("x-gupshup-signature", "")
        if not self.validate_webhook_signature(
            json.dumps(payload).encode(),
            signature,
        ):
            raise WebhookValidationException("Invalid webhook signature")
        
        # Parse webhook data
        return self._parse_delivery_status(payload)
    
    def validate_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> bool:
        """
        Validate Gupshup webhook signature
        
        Args:
            payload: Raw request body
            signature: Signature from header
            
        Returns:
            True if signature is valid
        """
        # Calculate expected signature
        expected = hmac.new(
            self.webhook_secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()
        
        # Constant-time comparison
        return hmac.compare_digest(expected, signature)
    
    async def get_account_balance(self) -> Dict[str, Any]:
        """
        Get account balance from Gupshup
        
        Returns:
            Balance information
        """
        try:
            url = f"{self.base_url}/account/balance"
            response = await self.client.get(url)
            response.raise_for_status()
            
            data = response.json()
            
            return {
                "balance": data.get("balance"),
                "currency": data.get("currency", "INR"),
                "credits": data.get("credits"),
            }
            
        except Exception as e:
            logger.error(f"Failed to get account balance: {e}")
            return {"balance": None, "currency": "INR", "credits": None}
    
    def get_name(self) -> str:
        """Get adapter name"""
        return "gupshup"
    
    def _build_rcs_payload(
        self,
        request: SendMessageRequest,
    ) -> Dict[str, Any]:
        """
        Build Gupshup RCS API payload
        
        Args:
            request: Message send request
            
        Returns:
            API payload dictionary
        """
        payload = {
            "channel": "rcs",
            "source": self.app_name,
            "destination": request.recipient_phone,
        }
        
        # Build message content
        if request.rich_card:
            # Rich card message
            payload["message"] = self._build_rich_card_message(
                request.content_text,
                request.rich_card,
                request.suggestions,
            )
        elif request.suggestions:
            # Text with suggestions
            payload["message"] = {
                "type": "text",
                "text": request.content_text,
                "suggestions": [
                    self._build_suggestion(s) for s in request.suggestions
                ],
            }
        else:
            # Plain text
            payload["message"] = {
                "type": "text",
                "text": request.content_text,
            }
        
        return payload
    
    def _build_rich_card_message(
        self,
        text: str,
        rich_card: RichCard,
        suggestions: List[SuggestedAction],
    ) -> Dict[str, Any]:
        """Build rich card message payload"""
        card_payload = {
            "type": "card",
            "payload": {
                "title": rich_card.title or "",
                "description": rich_card.description or text,
            }
        }
        
        # Add media if present
        if rich_card.media_url:
            card_payload["payload"]["media"] = {
                "url": rich_card.media_url,
                "contentType": rich_card.media_type or "image/jpeg",
                "height": rich_card.media_height or "MEDIUM",
            }
        
        # Add suggestions
        if suggestions:
            card_payload["payload"]["suggestions"] = [
                self._build_suggestion(s) for s in suggestions
            ]
        
        return card_payload
    
    def _build_suggestion(
        self,
        suggestion: SuggestedAction,
    ) -> Dict[str, Any]:
        """Build suggestion payload"""
        if suggestion.type == "reply":
            return {
                "type": "reply",
                "text": suggestion.text,
                "postbackData": suggestion.postback_data or suggestion.text,
            }
        elif suggestion.type == "url":
            return {
                "type": "action",
                "text": suggestion.text,
                "action": {
                    "type": "url",
                    "url": suggestion.url,
                }
            }
        elif suggestion.type == "dial":
            return {
                "type": "action",
                "text": suggestion.text,
                "action": {
                    "type": "dial",
                    "phoneNumber": suggestion.phone_number,
                }
            }
        else:
            return {
                "type": "reply",
                "text": suggestion.text,
            }
    
    def _parse_delivery_status(
        self,
        data: Dict[str, Any],
    ) -> Optional[DeliveryStatus]:
        """Parse Gupshup webhook/status response"""
        try:
            # Map Gupshup status to our status
            status_mapping = {
                "sent": "sent",
                "delivered": "delivered",
                "read": "read",
                "failed": "failed",
                "error": "failed",
            }
            
            gupshup_status = data.get("eventType") or data.get("status")
            status = status_mapping.get(gupshup_status, "unknown")
            
            return DeliveryStatus(
                message_id=data.get("messageId"),
                external_id=data.get("externalId") or data.get("messageId"),
                status=status,
                timestamp=datetime.utcnow(),
                error_code=data.get("errorCode"),
                error_message=data.get("errorMessage"),
                metadata=data,
            )
            
        except Exception as e:
            logger.error(f"Failed to parse delivery status: {e}")
            return None
    
    async def close(self):
        """Close HTTP client"""
        await self.client.aclose()
