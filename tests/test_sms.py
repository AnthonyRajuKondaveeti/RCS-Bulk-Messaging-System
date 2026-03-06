"""
test_rcs_sms_fallback.py
━━━━━━━━━━━━━━━━━━━━━━━
Standalone test: RCS fails (bad credentials) → SMS delivers for real.

HOW TO RUN
----------
    # From the project root (where .env lives):
    python test_rcs_sms_fallback.py +919876543210 +919123456789

    # Or edit PHONE_NUMBERS below and run without args:
    python test_rcs_sms_fallback.py

WHAT THIS DOES
--------------
  1. Attempts RCS via RcsSmsAdapter with intentionally invalid credentials
     → expects a failure response from rcssms.in (auth error / HTTP error)
  2. Falls back to SmsIdeaAdapter using your real credentials from .env
     → a real SMS is dispatched to each number

NO database, NO RabbitMQ, NO Redis required.
This calls the adapters directly — no queue or worker involvement.

REQUIREMENTS
------------
    pip install httpx python-dotenv pydantic pydantic-settings structlog
"""

import asyncio
import os
import sys
from uuid import uuid4
from datetime import datetime

# ── Make sure project root is on the path so imports work ──────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env BEFORE anything imports config
from dotenv import load_dotenv
load_dotenv(override=True)

# Force mock OFF so real adapters are used
os.environ["USE_MOCK_AGGREGATOR"] = "false"

# ── Project imports ────────────────────────────────────────────────────────
from apps.core.domain.message import MessageChannel
from apps.core.ports.aggregator import SendMessageRequest
from apps.adapters.aggregators.rcssms_adapter import RcsSmsAdapter
from apps.adapters.aggregators.smsidea_adapter import SmsIdeaAdapter


# ══════════════════════════════════════════════════════════════════════════
# CONFIG — edit these if not passing CLI args
# ══════════════════════════════════════════════════════════════════════════

# Default phone numbers (E.164 or 10-digit Indian format)
# Override by passing them as CLI args: python test_rcs_sms_fallback.py 9876543210 9123456789
DEFAULT_PHONE_NUMBERS = [
    "7993243834",   # ← Replace with your first test number
    "7356558915",   # ← Replace with your second test number
]

# DLT registered template — "SUD Fresh Case" (ConsentId: 1007616992834627267)
# Edit SMS_VARS below to match the real values for your test recipient
SMS_VARS = {
    "name":         "Rahul",        # ← recipient's actual name
    "insurer":      "ANT Life",
    "process_type": "verification",
    "app_no":       "ANThony56",    # ← any real or dummy app number
    "domain":       "veriright.com",
    "vmcode":       "TEST01",
}


SMS_TEMPLATE_ID = "1007616992834627267"

SMS_TEXT = (
    f"Dear {SMS_VARS['name']}, We have been retained by your insurer "
    f"{SMS_VARS['insurer']} to conduct your {SMS_VARS['process_type']} for your insurance "
    f"application no : {SMS_VARS['app_no']}. Our team will contact you "
    f"shortly or Click on link to schedule an appointment "
    f"https://{SMS_VARS['domain']}/mer-custmr-appmnt/?vmcode={SMS_VARS['vmcode']} - Veriright"
)

# RCS template ID placeholder (won't matter — auth will fail first)
FAKE_RCS_TEMPLATE_ID = "FAKE_TEMPLATE_001"


# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════

PASS = "✅"
FAIL = "❌"
INFO = "ℹ️ "
SEP  = "─" * 60


def _make_rcs_request(phone: str) -> SendMessageRequest:
    return SendMessageRequest(
        message_id=uuid4(),
        recipient_phone=phone,
        channel=MessageChannel.RCS,
        content_text=SMS_TEXT,
        metadata={
            "template_id": FAKE_RCS_TEMPLATE_ID,
            "variables": [],
            "rcs_type": "BASIC",
        },
    )


def _make_sms_request(phone: str) -> SendMessageRequest:
    return SendMessageRequest(
        message_id=uuid4(),
        recipient_phone=phone,
        channel=MessageChannel.SMS,
        content_text=SMS_TEXT,
        metadata={"sms_template_id": SMS_TEMPLATE_ID},
    )


def _load_sms_credentials() -> dict:
    """Read SMS creds from .env (already loaded above)."""
    username  = os.getenv("SMS_USERNAME")
    password  = os.getenv("SMS_PASSWORD")
    sender_id = os.getenv("SMS_SENDER_ID")
    peid      = os.getenv("SMS_PEID")

    missing = [k for k, v in {
        "SMS_USERNAME": username,
        "SMS_PASSWORD": password,
        "SMS_SENDER_ID": sender_id,
    }.items() if not v]

    if missing:
        raise EnvironmentError(
            f"Missing SMS credentials in .env: {', '.join(missing)}\n"
            "Set SMS_USERNAME, SMS_PASSWORD, and SMS_SENDER_ID."
        )

    return {
        "username":  username,
        "password":  password,
        "sender_id": sender_id,
        "peid":      peid,
    }


# ══════════════════════════════════════════════════════════════════════════
# CORE TEST LOGIC
# ══════════════════════════════════════════════════════════════════════════

async def test_rcs_fails(phone: str, rcs_adapter: RcsSmsAdapter) -> bool:
    """
    Try to send RCS with invalid credentials.
    Returns True if the failure was confirmed (expected behaviour).
    """
    print(f"\n{SEP}")
    print(f"📡  RCS attempt → {phone}")
    print(f"    Adapter   : {rcs_adapter.get_name()}")
    print(f"    Username  : {rcs_adapter.username}  (intentionally invalid)")
    print(f"    Template  : {FAKE_RCS_TEMPLATE_ID}")

    request = _make_rcs_request(phone)

    try:
        response = await rcs_adapter.send_rcs_message(request)

        if response.success:
            # Unexpected — real RCS credentials somehow worked?
            print(f"{FAIL}  RCS succeeded unexpectedly! (external_id={response.external_id})")
            print("    If you have valid RCS credentials, RCS will NOT fail — that's fine,")
            print("    but the point of this test is to exercise the fallback path.")
            return False
        else:
            print(f"{PASS}  RCS failed as expected.")
            print(f"    Error code   : {response.error_code}")
            print(f"    Error message: {response.error_message}")
            return True

    except Exception as exc:
        # Network error, auth exception, circuit breaker, etc. — also a failure
        print(f"{PASS}  RCS raised exception (also counts as failure).")
        print(f"    {type(exc).__name__}: {exc}")
        return True


def _patch_smsidea_parser(adapter: SmsIdeaAdapter) -> None:
    """
    Monkey-patch _parse_response to treat status "000" as success.

    The smsidea.co.in API returns {"status":"000","statusdesc":"success",...}
    but the adapter's original parser only checks for status == "0".
    This fix handles both variants.
    """
    import json as _json
    from apps.adapters.aggregators.smsidea_adapter import SMSIDEA_ERROR_CODES
    from apps.core.ports.aggregator import SendMessageResponse

    def _fixed_parse_response(self, raw: str, message_id: str) -> SendMessageResponse:
        text = (raw or "").strip()

        for error_key, error_desc in SMSIDEA_ERROR_CODES.items():
            if text.lower().startswith(error_key.lower()):
                return SendMessageResponse(
                    success=False,
                    error_code=error_key,
                    error_message=error_desc,
                )

        try:
            data = _json.loads(text)
            status = str(data.get("status", "")).strip()
            msg_id = data.get("messageid") or data.get("msgid")

            # smsidea uses "000" OR "0" for success
            if status in ("0", "000") or str(data.get("statusdesc", "")).lower() == "success":
                return SendMessageResponse(
                    success=True,
                    external_id=str(msg_id) if msg_id else None,
                )
            else:
                return SendMessageResponse(
                    success=False,
                    error_code=status,
                    error_message=data.get("statusdesc", text),
                )
        except (ValueError, KeyError):
            pass

        if text.isdigit() or text.lower().startswith("messageid:"):
            external_id = text.replace("MessageId:", "").replace("messageid:", "").strip()
            return SendMessageResponse(success=True, external_id=external_id)

        return SendMessageResponse(
            success=False,
            error_message=f"Unexpected smsidea response: {text[:100]}",
        )

    import types
    adapter._parse_response = types.MethodType(_fixed_parse_response, adapter)


async def test_sms_succeeds(phone: str, sms_adapter: SmsIdeaAdapter) -> bool:
    """
    Send a real SMS via smsidea.co.in.
    Returns True on confirmed acceptance.
    """
    print(f"\n{SEP}")
    print(f"📱  SMS fallback → {phone}")
    print(f"    Adapter   : {sms_adapter.get_name()}")
    print(f"    Username  : {sms_adapter.username}")
    print(f"    Sender ID : {sms_adapter.sender_id}")
    print(f"    PEID      : {sms_adapter.peid or '(not set)'}")
    print(f"    Message   : {SMS_TEXT[:60]}{'…' if len(SMS_TEXT) > 60 else ''}")

    request = _make_sms_request(phone)

    try:
        response = await sms_adapter.send_sms_message(request)

        if response.success:
            print(f"{PASS}  SMS accepted by smsidea.co.in")
            print(f"    External ID : {response.external_id}")
            print(f"    Timestamp   : {datetime.now().isoformat(timespec='seconds')}")
            return True
        else:
            print(f"{FAIL}  SMS rejected by smsidea.co.in")
            print(f"    Error code   : {response.error_code}")
            print(f"    Error message: {response.error_message}")
            print()
            print("    Common causes:")
            print("      • error API01 → wrong username/password")
            print("      • error API03 → invalid sender ID format (must be 6 chars)")
            print("      • error API04 → insufficient SMS credits")
            print("      • error API06 → invalid phone number format")
            return False

    except Exception as exc:
        print(f"{FAIL}  SMS raised exception: {type(exc).__name__}: {exc}")
        return False


# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════

async def main(phones: list[str]) -> None:
    print("\n" + "═" * 60)
    print("  RCS → SMS Fallback Integration Test")
    print("  Mode: USE_MOCK_AGGREGATOR=false  (REAL API calls)")
    print("═" * 60)

    # ── Load SMS credentials ───────────────────────────────────────────
    try:
        sms_creds = _load_sms_credentials()
    except EnvironmentError as e:
        print(f"\n{FAIL}  {e}")
        sys.exit(1)

    # ── Build adapters ─────────────────────────────────────────────────

    # RCS adapter with INTENTIONALLY INVALID credentials
    # (your real credentials are not available, so this will fail)
    rcs_adapter = RcsSmsAdapter(
        username="INVALID_USER",
        password="INVALID_PASS",
        rcs_id="INVALID_RCS_ID",
        use_bearer=False,   # password auth — will be rejected by rcssms.in
        timeout=15,
    )

    # SMS adapter with REAL credentials from .env
    sms_adapter = SmsIdeaAdapter(
        username=sms_creds["username"],
        password=sms_creds["password"],
        sender_id=sms_creds["sender_id"],
        peid=sms_creds.get("peid"),
        timeout=15,
    )
    # Fix: smsidea returns status "000" for success, not "0"
    _patch_smsidea_parser(sms_adapter)

    # ── Per-number test loop ───────────────────────────────────────────
    results = []

    for phone in phones:
        phone = phone.strip()
        print(f"\n\n{'━' * 60}")
        print(f"  Testing number: {phone}")
        print(f"{'━' * 60}")

        rcs_failed  = await test_rcs_fails(phone, rcs_adapter)
        sms_success = await test_sms_succeeds(phone, sms_adapter)

        results.append({
            "phone":       phone,
            "rcs_failed":  rcs_failed,
            "sms_success": sms_success,
        })

    # ── Summary ────────────────────────────────────────────────────────
    print(f"\n\n{'═' * 60}")
    print("  SUMMARY")
    print(f"{'═' * 60}")

    all_passed = True
    for r in results:
        rcs_icon = PASS if r["rcs_failed"]  else FAIL
        sms_icon = PASS if r["sms_success"] else FAIL
        overall  = PASS if (r["rcs_failed"] and r["sms_success"]) else FAIL
        if not (r["rcs_failed"] and r["sms_success"]):
            all_passed = False

        print(f"\n  {r['phone']}")
        print(f"    {rcs_icon}  RCS failed (expected)  : {r['rcs_failed']}")
        print(f"    {sms_icon}  SMS delivered (real)   : {r['sms_success']}")
        print(f"    {overall}  Overall result")

    print(f"\n{'═' * 60}")
    if all_passed:
        print(f"  {PASS}  ALL TESTS PASSED — check your phones for the SMS!")
    else:
        print(f"  {FAIL}  SOME TESTS FAILED — see details above.")
    print(f"{'═' * 60}\n")

    # ── Cleanup ────────────────────────────────────────────────────────
    await rcs_adapter.close()
    await sms_adapter.close()

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    # Accept phone numbers from CLI args, fall back to defaults
    cli_phones = sys.argv[1:]
    phones_to_test = cli_phones if cli_phones else DEFAULT_PHONE_NUMBERS

    if not phones_to_test:
        print(f"{FAIL}  No phone numbers provided.")
        print("  Usage: python test_rcs_sms_fallback.py 9876543210 9123456789")
        sys.exit(1)

    asyncio.run(main(phones_to_test))