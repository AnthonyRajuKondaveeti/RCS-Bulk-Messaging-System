"""
SMSIdea SMS Adapter

Concrete SMS implementation using smsidea.co.in's HTTP API.
Used exclusively for RCS fallback — when RCS delivery fails or the
recipient's device is not RCS-capable, the fallback worker calls
send_sms_message() on this adapter.

API Documentation: https://smsidea.co.in
Send endpoint:     https://smsidea.co.in/smsstatuswithid.aspx  (primary)
Fallback endpoint: https://smsidea.co.in/sendbulksms.aspx       (bulk/custom)

Auth:
    The API uses `mobile` (login username) + `pass` (password or API key).
    The API key shown in the portal can be used in place of the password.

DLT compliance (India TRAI):
    - senderid: 6-character approved alphanumeric sender ID
    - peid:     Principal Entity ID registered on DLT portal
    - templateid: Pre-registered DLT template ID

Fallback content strategy:
    1. If message.content has a template_id AND the SMS template is configured,
       send with templateid + peid + variables rendered into `msg`.
    2. Otherwise, fall back to plain text from MessageContent.to_sms_text().
       This covers edge cases (no DLT template, testing, etc.) at the cost
       of potential delivery filtering for non-transactional routes.

Error codes (from smsidea.co.in docs):
    error API01 - Invalid username or password
    error API03 - Invalid senderid format
    error API04 - Insufficient balance
    error API06 - Invalid 10-digit mobile number
    error API07 - Error while sending SMS
    error API08 - Missing required parameters
    error API10 - Message length exceeded (Normal: 469 chars, Unicode: 800)
    error API11 - Invalid schedule date format
"""

import httpx
import structlog
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

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

logger = structlog.get_logger(__name__)

# smsidea.co.in string error code → human description
SMSIDEA_ERROR_CODES: Dict[str, str] = {
    "error API01": "Invalid username or password",
    "error API03": "Invalid senderid format",
    "error API04": "Insufficient balance",
    "error API06": "Invalid 10-digit mobile number",
    "error API07": "Error while sending SMS",
    "error API08": "Missing required parameters",
    "error API10": "Message length exceeded (Normal: 469 chars, Unicode: 800 chars)",
    "error API11": "Invalid schedule date format",
}

# Max SMS body length per the API docs
SMS_MAX_LENGTH_NORMAL = 469
SMS_MAX_LENGTH_UNICODE = 800

SEND_URL = "https://smsidea.co.in/smsstatuswithid.aspx"


class SmsIdeaAdapter(AggregatorPort):
    """
    smsidea.co.in SMS adapter — SMS fallback only.

    This adapter handles only the send_sms_message() path.
    All RCS methods raise NotImplementedError because RCS is handled
    exclusively by RcsSmsAdapter (rcssms.in).

    Configuration (via .env):
        SMS_USERNAME  - Login username (same as portal login)
        SMS_PASSWORD  - Password or API key from portal
        SMS_SENDER_ID - 6-character DLT-approved sender ID
        SMS_PEID      - Principal Entity ID (DLT registration)

    Example:
        >>> adapter = SmsIdeaAdapter(
        ...     username="myuser",
        ...     password="d7f03e...",   # API key from portal
        ...     sender_id="MYBRND",
        ...     peid="1234567890",
        ... )
        >>> response = await adapter.send_sms_message(request)
    """

    def __init__(
        self,
        username: str,
        password: str,
        sender_id: str,
        peid: Optional[str] = None,
        timeout: int = 30,
    ) -> None:
        """
        Initialise the smsidea.co.in adapter.

        Args:
            username:  Portal login username (maps to `mobile` param in API)
            password:  Portal password or API key (maps to `pass` param)
            sender_id: 6-character DLT-approved sender ID (e.g. "MYBRND")
            peid:      Principal Entity ID from DLT portal (optional but
                       strongly recommended for transactional routes)
            timeout:   HTTP request timeout in seconds
        """
        self.username = username
        self.password = password
        self.sender_id = sender_id
        self.peid = peid
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout)

    # ------------------------------------------------------------------
    # AggregatorPort — only send_sms_message is implemented here
    # ------------------------------------------------------------------

    async def send_sms_message(
        self,
        request: SendMessageRequest,
    ) -> SendMessageResponse:
        """
        Send an SMS via smsidea.co.in.

        Content strategy:
          - If request.metadata contains 'sms_template_id', send with that
            templateid and the rendered msg text (DLT compliant).
          - Otherwise send the plain text from content_text (best-effort).

        The recipient number is sent as-is; smsidea accepts 10-digit Indian
        numbers with or without the country code prefix.

        Args:
            request: Standard SendMessageRequest (channel should be SMS)

        Returns:
            SendMessageResponse with external_id on success
        """
        recipient = _normalise_phone(request.recipient_phone)
        msg_text = _truncate_sms(request.content_text or "")

        # Build query params — the primary smsidea API is GET/POST with params
        params: Dict[str, str] = {
            "mobile": self.username,
            "pass": self.password,
            "senderid": self.sender_id,
            "to": recipient,
            "msg": msg_text,
            "restype": "json",
        }

        # DLT fields — include when available
        if self.peid:
            params["peid"] = self.peid

        meta = request.metadata or {}

        # Prefer a dedicated SMS DLT template ID over the RCS one
        sms_template_id = meta.get("sms_template_id") or meta.get("template_id")
        if sms_template_id:
            params["templateid"] = sms_template_id

        logger.info(
            "smsidea_sending",
            message_id=str(request.message_id),
            recipient=recipient,
            sender_id=self.sender_id,
            has_template=bool(sms_template_id),
            msg_length=len(msg_text),
        )

        try:
            response = await self.client.get(SEND_URL, params=params)
            response.raise_for_status()
            return self._parse_response(response.text, str(request.message_id))

        except httpx.HTTPStatusError as exc:
            logger.error(
                "smsidea_http_error",
                message_id=str(request.message_id),
                status_code=exc.response.status_code,
                detail=str(exc),
            )
            return SendMessageResponse(
                success=False,
                error_code=str(exc.response.status_code),
                error_message=str(exc),
            )
        except httpx.RequestError as exc:
            logger.error(
                "smsidea_request_error",
                message_id=str(request.message_id),
                detail=str(exc),
            )
            raise AggregatorException(f"smsidea.co.in request failed: {exc}")

    # ------------------------------------------------------------------
    # Stubs — RCS operations are not handled by this adapter
    # ------------------------------------------------------------------

    async def send_rcs_message(self, request: SendMessageRequest) -> SendMessageResponse:
        raise NotImplementedError(
            "SmsIdeaAdapter handles SMS only. Use RcsSmsAdapter for RCS."
        )

    async def check_rcs_capability(
        self, phone_numbers: List[str]
    ) -> List[CapabilityCheckResult]:
        raise NotImplementedError("SmsIdeaAdapter does not support RCS capability checks.")

    async def get_delivery_status(self, external_id: str) -> Optional[DeliveryStatus]:
        """
        smsidea.co.in does not expose a pull-based delivery status endpoint.
        Configure a DLR push URL in the portal for push-based updates.
        """
        logger.info("smsidea_pull_dlr_not_supported", external_id=external_id)
        return None

    async def handle_webhook(
        self,
        payload: Dict[str, Any],
        headers: Dict[str, str],
    ) -> Optional[DeliveryStatus]:
        """
        Parse DLR push from smsidea.co.in.

        smsidea pushes a status update to your configured DLR URL.
        Expected fields: msgid, msisdn, status (DELIVERED/FAILED/etc.)
        """
        return self._parse_dlr(payload)

    def validate_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """smsidea.co.in does not document webhook signatures — always True."""
        return True

    async def get_account_balance(self) -> Dict[str, Any]:
        """
        Query SMS credit balance from smsidea.co.in.

        Uses the Get Balance API:
            https://smsidea.co.in/sms/api/getbalance.aspx
        """
        try:
            response = await self.client.get(
                "https://smsidea.co.in/sms/api/getbalance.aspx",
                params={
                    "mobile": self.username,
                    "pass": self.password,
                    "restype": "json",
                },
            )
            response.raise_for_status()
            data = response.json()
            return {
                "balance": data.get("balance"),
                "currency": "INR",
                "credits": data.get("balance"),
                "status": data.get("status"),
            }
        except Exception as exc:
            logger.warning("smsidea_balance_check_failed", error=str(exc))
            return {"balance": None, "currency": "INR", "credits": None, "error": str(exc)}

    def get_name(self) -> str:
        return "smsidea"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_response(self, raw: str, message_id: str) -> SendMessageResponse:
        """
        Parse smsidea.co.in API response.

        Success (JSON restype):
            {"status":"#status#","statusdesc":"#statusdesc#","messageid":"<id>"}

        Error (string prefix):
            "error API01", "error API04", etc.

        The API may also return plain text like "MessageId:12345" if restype
        is omitted — we always request restype=json so that branch is a guard.
        """
        text = (raw or "").strip()

        # Check for known error code prefix
        for error_key, error_desc in SMSIDEA_ERROR_CODES.items():
            if text.lower().startswith(error_key.lower()):
                logger.error(
                    "smsidea_api_error",
                    message_id=message_id,
                    error_code=error_key,
                    raw_response=text,
                )
                return SendMessageResponse(
                    success=False,
                    error_code=error_key,
                    error_message=error_desc,
                )

        # Try JSON parse (restype=json)
        try:
            import json
            data = json.loads(text)
            status = str(data.get("status", "")).strip()
            msg_id = data.get("messageid") or data.get("msgid")

            if status == "0" or status.lower() == "success":
                logger.info(
                    "smsidea_accepted",
                    message_id=message_id,
                    external_id=str(msg_id),
                )
                return SendMessageResponse(
                    success=True,
                    external_id=str(msg_id) if msg_id else None,
                )
            else:
                logger.error(
                    "smsidea_rejected",
                    message_id=message_id,
                    status=status,
                    raw=text,
                )
                return SendMessageResponse(
                    success=False,
                    error_code=status,
                    error_message=data.get("statusdesc", text),
                )
        except (ValueError, KeyError):
            pass

        # Plain-text fallback: "MessageId:12345" or bare numeric ID
        if text.isdigit() or text.lower().startswith("messageid:"):
            external_id = text.replace("MessageId:", "").replace("messageid:", "").strip()
            logger.info("smsidea_accepted_plain", message_id=message_id, external_id=external_id)
            return SendMessageResponse(success=True, external_id=external_id)

        # Unknown response format — treat as failure
        logger.error(
            "smsidea_unexpected_response",
            message_id=message_id,
            raw=text[:200],
        )
        return SendMessageResponse(
            success=False,
            error_message=f"Unexpected smsidea response: {text[:100]}",
        )

    def _parse_dlr(self, payload: Dict[str, Any]) -> Optional[DeliveryStatus]:
        """Parse DLR push callback from smsidea.co.in."""
        try:
            status_map = {
                "DELIVERED": "delivered",
                "SENT": "sent",
                "FAILED": "failed",
                "UNDELIVERED": "failed",
                "READ": "read",
            }
            raw_status = str(payload.get("status", "")).upper()
            status = status_map.get(raw_status, "unknown")
            msg_id = payload.get("msgid") or payload.get("messageid")

            logger.info(
                "smsidea_dlr_parsed",
                external_id=msg_id,
                raw_status=raw_status,
                mapped_status=status,
                msisdn=payload.get("msisdn"),
            )

            return DeliveryStatus(
                message_id=msg_id,
                external_id=str(msg_id) if msg_id else "",
                status=status,
                timestamp=datetime.utcnow(),
                metadata=payload,
            )
        except Exception as exc:
            logger.error("smsidea_dlr_parse_error", error=str(exc), payload=str(payload))
            return None

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self.client.aclose()


# ------------------------------------------------------------------
# Module-level utilities
# ------------------------------------------------------------------

def _normalise_phone(phone: str) -> str:
    """
    Normalise phone number for smsidea.co.in.

    The API accepts 10-digit Indian numbers with or without the +91 / 91 prefix.
    Strip any leading '+' or country code so we send a clean 10-digit number;
    this avoids error API06 for numbers formatted in E.164.

    Examples:
        +919876543210  ->  9876543210
        919876543210   ->  9876543210  (only if starts with 91 and is 12 digits)
        9876543210     ->  9876543210
    """
    digits = phone.lstrip("+")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    return digits


def _truncate_sms(text: str, unicode_mode: bool = False) -> str:
    """
    Truncate SMS body to the maximum length supported by smsidea.co.in.

    Normal (GSM7):   469 characters
    Unicode (msgtype=uc): 800 characters

    If the text exceeds the limit, it is hard-truncated with a trailing
    ellipsis so delivery does not fail with error API10.
    """
    limit = SMS_MAX_LENGTH_UNICODE if unicode_mode else SMS_MAX_LENGTH_NORMAL
    if len(text) <= limit:
        return text
    logger.warning(
        "smsidea_msg_truncated",
        original_length=len(text),
        truncated_to=limit,
    )
    return text[: limit - 1] + "…"
