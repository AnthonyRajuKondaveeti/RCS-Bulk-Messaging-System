"""
RCSSMS Aggregator Adapter

Concrete implementation of AggregatorPort for rcssms.in RCS API.
Handles message sending, template creation, webhook (DLR) processing.

API Documentation: https://web.rcssms.in
Endpoints:
    - Send RCS:      https://web.rcssms.in/rcsapi/jsonapi.jsp?apitype=1
    - Access Token:  https://web.rcssms.in/api/rcs/accesstoken
    - Template:      https://web.rcssms.in/rcsapi/rcscreatetemplate.jsp
"""

import base64
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
from apps.core.resilience.redis_circuit_breaker import (
    RedisCircuitBreaker,
    CircuitBreakerOpenError,
)

from apps.core.domain.message import MessageChannel, RichCard, SuggestedAction
from apps.core.ports.aggregator import (
    AggregatorException,
    AggregatorPort,
    CapabilityCheckResult,
    DeliveryStatus,
    RateLimitException,
    SendMessageRequest,
    SendMessageResponse,
    WebhookValidationException,
)

import structlog
from apps.core.config import get_settings

logger = structlog.get_logger(__name__)

# Error code mapping from rcssms.in API docs
RCSSMS_ERROR_CODES = {
    "13": "Invalid JSON packet",
    "10": "Missing required fields (username/password/type/msisdn)",
    "15": "Account validity expired",
    "21": "Username does not exist or is invalid",
    "30": "Token validity expired",
    "22": "Incorrect username/password or token",
    "23": "Insufficient credit",
    "24": "Incorrect number list",
    "18": "Template ID not found",
}


class RcsSmsAdapter(AggregatorPort):
    """
    rcssms.in API adapter

    Implements RCS messaging through rcssms.in's JSON API.
    Supports bearer token authentication and password-based authentication.

    Configuration (via .env):
        RCS_USERNAME      - Account username
        RCS_PASSWORD      - Account password (used if not using bearer token)
        RCS_ID            - Bot/account RCS ID
        RCS_CLIENT_SECRET - Client secret for bearer token generation (optional)
        RCS_USE_BEARER    - Set to "true" to use bearer token auth

    Example:
        >>> adapter = RcsSmsAdapter(
        ...     username="myuser",
        ...     password="mypass",
        ...     rcs_id="mybotid",
        ... )
        >>> response = await adapter.send_rcs_message(request)
    """

    SEND_URL = "https://web.rcssms.in/rcsapi/jsonapi.jsp?apitype=1"
    TOKEN_URL = "https://web.rcssms.in/api/rcs/accesstoken"
    TEMPLATE_URL = "https://web.rcssms.in/rcsapi/rcscreatetemplate.jsp"

    def __init__(
        self,
        username: str,
        password: str,
        rcs_id: str,
        client_secret: Optional[str] = None,
        use_bearer: bool = False,
        timeout: int = 30,
    ):
        """
        Initialize rcssms.in adapter

        Args:
            username:      Account username
            password:      Account password
            rcs_id:        Bot/RCS ID assigned during account creation
            client_secret: Client secret for bearer token generation
            use_bearer:    If True, authenticate via bearer token instead of password
            timeout:       HTTP request timeout in seconds
        """
        self.username = username
        self.password = password
        self.rcs_id = rcs_id
        self.client_secret = client_secret
        self.use_bearer = use_bearer
        self.timeout = timeout

        # Bearer token state
        self._bearer_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

        self.client = httpx.AsyncClient(timeout=timeout)

        # Redis-backed circuit breaker — shared state across all worker processes.
        # Redis URL resolved lazily from settings so tests can override with a mock.
        _settings = get_settings()
        self._breaker = RedisCircuitBreaker(
            name="rcssms",
            redis_url=_settings.redis.url,
            failure_threshold=5,
            failure_window_seconds=60,
            recovery_timeout=60,
            success_threshold=2,
        )
    
    async def connect(self) -> None:
        """Connect to aggregator (opens circuit breaker connection)"""
        # Circuit breaker connects lazily on first use, but we can pre-initialize if needed
        logger.info("RcsSmsAdapter: connect called", extra={"username": self.username})

    # ------------------------------------------------------------------
    # AggregatorPort interface
    # ------------------------------------------------------------------

    async def send_rcs_message(
        self,
        request: SendMessageRequest,
    ) -> SendMessageResponse:
        """
        Send RCS message via rcssms.in (METHOD 1 - preferred).

        Supports BASIC, RICH, and RICHCASOUREL template types.
        Up to 500 recipients can be batched in a single request.

        Args:
            request: Message send request

        Returns:
            SendMessageResponse with external message IDs
        """
        try:
            # All N worker replicas share the same Redis-backed circuit state.
            # If rcssms.in is down, the breaker trips once and protects
            # all workers without each having to experience the failure.
            async with self._breaker():
                payload = await self._build_rcs_payload(request)
                headers = await self._get_headers()

                logger.info(
                    "sending_rcs_message",
                    message_id=str(request.message_id),
                    recipient=request.recipient_phone,
                    template_id=request.metadata.get("template_id") if request.metadata else None,
                    rcs_id=self.rcs_id,
                )

                response = await self.client.post(
                    self.SEND_URL,
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                result = response.json()

            send_response = self._parse_send_response(result)

            if send_response.success:
                logger.info(
                    "rcssms_accepted",
                    message_id=str(request.message_id),
                    external_id=send_response.external_id,
                )
            else:
                logger.error(
                    "rcssms_rejected",
                    message_id=str(request.message_id),
                    error_code=send_response.error_code,
                    error_message=send_response.error_message,
                )

            return send_response

        except CircuitBreakerOpenError as e:
            logger.warning("rcssms_circuit_open", detail=str(e))
            return SendMessageResponse(
                success=False,
                error_code="CIRCUIT_OPEN",
                error_message=str(e),
            )
        except httpx.HTTPStatusError as e:
            logger.error("rcssms_http_error", status_code=e.response.status_code, detail=str(e))
            return SendMessageResponse(
                success=False,
                error_code=str(e.response.status_code),
                error_message=str(e),
            )
        except Exception as e:
            logger.exception("rcssms_send_failed")
            raise AggregatorException(f"Failed to send RCS message: {e}")

    async def send_sms_message(
        self,
        request: SendMessageRequest,
    ) -> SendMessageResponse:
        """
        rcssms.in has no SMS endpoint — this method must never be called.

        SMS fallback is handled exclusively by SmsIdeaAdapter (smsidea.co.in).
        SMSFallbackWorker obtains that adapter via AggregatorFactory.create_sms_adapter().

        Raising here (rather than silently re-sending as RCS) ensures that a
        misconfiguration — e.g. the fallback worker accidentally using the RCS
        adapter — is caught immediately instead of silently ignoring the fallback.
        """
        raise NotImplementedError(
            "RcsSmsAdapter does not support SMS. "
            "Use SmsIdeaAdapter (smsidea.co.in) for SMS fallback. "
            "Obtain it via AggregatorFactory.create_sms_adapter()."
        )

    async def check_rcs_capability(
        self,
        phone_numbers: List[str],
    ) -> List[CapabilityCheckResult]:
        """
        rcssms.in does not expose a capability check endpoint.
        Assume all numbers are RCS-capable; the API will handle fallback.
        """
        return [
            CapabilityCheckResult(
                phone_number=phone,
                rcs_enabled=True,  # Optimistic — API handles delivery
                last_checked=datetime.utcnow(),
                features=["rich_cards", "suggestions"],
            )
            for phone in phone_numbers
        ]

    async def get_delivery_status(
        self,
        external_id: str,
    ) -> Optional[DeliveryStatus]:
        """
        rcssms.in uses DLR push callbacks (webhooks) for delivery status.
        Pull-based status query is not supported — return None.
        Configure your DLR endpoint in your rcssms.in account settings.
        """
        logger.info(
            "rcssms.in uses push DLR callbacks. "
            "Pull-based status query is not supported."
        )
        return None

    async def handle_webhook(
        self,
        payload: Dict[str, Any],
        headers: Dict[str, str],
    ) -> Optional[DeliveryStatus]:
        """
        Parse DLR push callback from rcssms.in.

        rcssms.in posts DLR updates to your registered webhook URL.
        No signature verification is documented — accept all callbacks
        from the known rcssms.in IP range or validate at network level.

        Expected payload:
            {
                "templateid": "...",
                "status": "APPROVED" | "REJECTED"
            }
        Or DLR delivery update:
            {
                "msgid": "...",
                "msisdn": "+91...",
                "status": "DELIVERED" | "FAILED" | ...
            }

        Args:
            payload: Webhook POST body
            headers: HTTP headers

        Returns:
            Parsed DeliveryStatus or None
        """
        return self._parse_dlr_payload(payload)

    def validate_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> bool:
        """
        rcssms.in does not document webhook signature verification.
        Always returns True — secure your webhook endpoint at network level.
        """
        return True

    async def get_account_balance(self) -> Dict[str, Any]:
        """
        rcssms.in does not expose a balance API endpoint.
        Returns a placeholder — check balance via the web portal.
        """
        return {
            "balance": None,
            "currency": "INR",
            "credits": None,
            "note": "Balance check not supported via API. Use web portal.",
        }

    def get_name(self) -> str:
        return "rcssms"

    # ------------------------------------------------------------------
    # Template management (bonus — not in Gupshup adapter)
    # ------------------------------------------------------------------

    async def create_template(
        self,
        rcs_type: str,
        campaign_name: str,
        description: Optional[str] = None,
        media_url: Optional[str] = None,
        media_height: Optional[str] = None,
        card_orientation: Optional[str] = None,
        title: Optional[str] = None,
        reply: Optional[List[Dict]] = None,
        action: Optional[List[Dict]] = None,
        cards: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """
        Submit a template for operator approval via rcssms.in.

        Args:
            rcs_type:         "BASIC", "RICH", or "RICHCASOUREL"
            campaign_name:    Reference name / project ID
            description:      Template description (BASIC/RICH)
            media_url:        Publicly hosted image/video URL (RICH)
            media_height:     "Low", "Medium", "High" (RICH)
            card_orientation: "Vertical", "Horizontal" (RICH)
            title:            Card title (RICH)
            reply:            List of reply suggestions [{"text": "..."}]
            action:           List of action buttons
            cards:            List of carousel cards (RICHCASOUREL only)

        Returns:
            API response with templateid and status (PENDING)
        """
        payload: Dict[str, Any] = {
            "username": self.username,
            "password": self.password,
            "rcstype": rcs_type.upper(),
            "rcsid": self.rcs_id,
            "cname": campaign_name,
        }

        if rcs_type.upper() == "BASIC":
            payload["description"] = description or ""

        elif rcs_type.upper() == "RICH":
            payload.update({
                "media_url": media_url or "",
                "mediaheight": media_height or "Medium",
                "cardorientation": card_orientation or "Vertical",
                "title": title or "",
                "description": description or "",
            })
            if reply:
                payload["reply"] = reply
            if action:
                payload["action"] = action

        elif rcs_type.upper() == "RICHCASOUREL":
            payload["cards"] = cards or []

        try:
            response = await self.client.post(
                self.TEMPLATE_URL,
                json=payload,
            )
            response.raise_for_status()
            return response.json()

        except Exception as e:
            logger.exception("Failed to create template")
            raise AggregatorException(f"Template creation failed: {e}")

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    async def _get_headers(self) -> Dict[str, str]:
        """Build HTTP headers — bearer token if configured, else plain."""
        headers = {"Content-Type": "application/json"}

        if self.use_bearer:
            token = await self._get_bearer_token()
            headers["Authorization"] = f"Bearer {token}"

        return headers

    async def _get_bearer_token(self) -> str:
        """
        Fetch or return cached bearer token.
        Token validity is 24 hours per API docs.
        """
        if self._bearer_token and self._token_expires_at:
            if datetime.utcnow() < self._token_expires_at:
                return self._bearer_token

        if not self.client_secret:
            raise AggregatorException(
                "client_secret is required for bearer token authentication"
            )

        # Build Basic auth header: base64(username:client_secret)
        credentials = f"{self.username}:{self.client_secret}"
        encoded = base64.b64encode(credentials.encode()).decode()

        response = await self.client.post(
            self.TOKEN_URL,
            headers={
                "Authorization": f"Basic {encoded}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        data = response.json()

        self._bearer_token = data.get("access_token") or data.get("token")
        # Cache for 23 hours (1 hour buffer before 24h expiry)
        self._token_expires_at = datetime.utcnow() + timedelta(hours=23)

        if not self._bearer_token:
            raise AggregatorException("Bearer token not found in response")

        return self._bearer_token

    # ------------------------------------------------------------------
    # Payload builders
    # ------------------------------------------------------------------

    async def _build_rcs_payload(
        self,
        request: SendMessageRequest,
    ) -> Dict[str, Any]:
        """
        Build rcssms.in JSON payload (METHOD 1 / apitype=1 format).

        rcstype resolution (priority order):
          1. metadata["rcs_type"]  — explicitly set by orchestrator from template.rcs_type
          2. rich_card present     — RICH
          3. default               — BASIC
          RICHCASOUREL is only set via metadata["rcs_type"]; it cannot be inferred
          from rich_card alone because the domain RichCard model is single-card.

        Variables format (METHOD 1 spec):
          The API expects an array of objects, one object per recipient, where
          keys are var1, var2, var3... in template placeholder order:
              "variables": [{"var1": "John", "var2": "ORD-123"}]
          The raw metadata["variables"] is an ordered flat list of values
          (e.g. ["John", "ORD-123"]) so we convert it here.
          For a single recipient (the normal case) this produces one object.

        Phone format:
          The API accepts numbers with or without the 91 country code prefix.
          The domain normalises to E.164 (+91XXXXXXXXXX) which is accepted as-is.
        """
        meta = request.metadata or {}

        # --- rcstype ---
        # Prefer the explicit value stored by the orchestrator (from template.rcs_type).
        # Fall back to inference from rich_card for backward compatibility.
        rcs_type = meta.get("rcs_type") or (
            "RICH" if request.rich_card else "BASIC"
        )

        payload: Dict[str, Any] = {
            "rcstype": rcs_type.upper(),
            "rcsid": self.rcs_id,
            "msisdn": request.recipient_phone,
            "templateid": meta.get("template_id", ""),
        }

        # Auth — per docs, password tag is optional when using bearer token
        payload["username"] = self.username
        if not self.use_bearer:
            payload["password"] = self.password

        # --- variables ---
        # Convert flat ordered list ["val1", "val2"] to Method 1 format:
        # [{"var1": "val1", "var2": "val2"}] — one object per recipient.
        raw_vars = meta.get("variables")
        if raw_vars:
            if isinstance(raw_vars, list) and len(raw_vars) > 0:
                if isinstance(raw_vars[0], dict):
                    # Already in Method 1 object format — pass through
                    payload["variables"] = raw_vars
                else:
                    # Flat list of values — convert to Method 1 keyed object
                    var_obj = {f"var{i + 1}": str(v) for i, v in enumerate(raw_vars)}
                    payload["variables"] = [var_obj]

        return payload

    # ------------------------------------------------------------------
    # Response parsers
    # ------------------------------------------------------------------

    def _parse_send_response(
        self,
        result: Dict[str, Any],
    ) -> SendMessageResponse:
        """Parse send API response."""
        # Check for error codes
        error_code = str(result.get("error") or result.get("errorcode") or "")
        if error_code and error_code in RCSSMS_ERROR_CODES:
            return SendMessageResponse(
                success=False,
                error_code=error_code,
                error_message=RCSSMS_ERROR_CODES[error_code],
            )

        # Success: response contains list of {msgid, msisdn}
        data = result.get("data", [])
        if data:
            # Use first msgid as the primary external_id
            external_id = data[0].get("msgid") if data else None
            return SendMessageResponse(
                success=True,
                external_id=external_id,
                metadata={"all_msgids": data} if len(data) > 1 else None,
            )

        return SendMessageResponse(
            success=False,
            error_message="Unexpected response format from rcssms.in",
        )

    def _parse_dlr_payload(
        self,
        payload: Dict[str, Any],
    ) -> Optional[DeliveryStatus]:
        """Parse DLR push callback from rcssms.in."""
        try:
            status_mapping = {
                "DELIVERED": "delivered",
                "SENT": "sent",
                "FAILED": "failed",
                "UNDELIVERED": "failed",
                "READ": "read",
                "APPROVED": "delivered",
                "REJECTED": "failed",
            }

            raw_status = (payload.get("status") or "").upper()
            status = status_mapping.get(raw_status, "unknown")
            msg_id = payload.get("msgid") or payload.get("templateid")

            logger.info("DLR payload parsed",
                        extra={
                            "external_id": msg_id,
                            "raw_status": raw_status,
                            "mapped_status": status,
                            "msisdn": payload.get("msisdn"),
                            "step": "dlr_parsed",
                        })

            return DeliveryStatus(
                message_id=msg_id,
                external_id=msg_id,
                status=status,
                timestamp=datetime.utcnow(),
                error_code=None,
                error_message=None,
                metadata=payload,
            )

        except Exception as e:
            logger.error("Failed to parse DLR payload",
                         extra={"error": str(e), "payload": str(payload), "step": "dlr_parse_error"})
            return None

    async def close(self):
        """Close HTTP client and Redis circuit breaker connection."""
        await self.client.aclose()
        await self._breaker.close()
