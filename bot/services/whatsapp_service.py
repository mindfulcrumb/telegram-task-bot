"""WhatsApp OTP delivery via Twilio REST API (httpx, no SDK needed)."""
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "")
_CONTENT_SID = os.getenv("TWILIO_OTP_CONTENT_SID", "HX57226d9d902a1401df9a1715a7130fcb")
_MESSAGING_SERVICE_SID = os.getenv("TWILIO_MESSAGING_SERVICE_SID", "MG43569efb51a03061fa0e328b171546bd")


def is_configured() -> bool:
    """Check if Twilio WhatsApp credentials are set."""
    return bool(_ACCOUNT_SID and _AUTH_TOKEN and _WHATSAPP_FROM)


_last_error = ""


def get_last_error() -> str:
    """Return the last Twilio error for debugging."""
    return _last_error


async def send_otp(phone: str, code: str) -> bool:
    """Send a 6-digit OTP via WhatsApp using Twilio.

    Args:
        phone: International format with + prefix (e.g. +351912345678)
        code: The 6-digit verification code

    Returns:
        True if message was accepted by Twilio, False otherwise.
    """
    global _last_error
    _last_error = ""

    if not is_configured():
        _last_error = "Twilio not configured — missing env vars"
        logger.error(_last_error)
        return False

    # Ensure From number has + prefix
    from_num = _WHATSAPP_FROM if _WHATSAPP_FROM.startswith("+") else f"+{_WHATSAPP_FROM}"

    url = f"https://api.twilio.com/2010-04-01/Accounts/{_ACCOUNT_SID}/Messages.json"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                auth=(_ACCOUNT_SID, _AUTH_TOKEN),
                data={
                    "From": f"whatsapp:{from_num}",
                    "To": f"whatsapp:{phone}",
                    "MessagingServiceSid": _MESSAGING_SERVICE_SID,
                    "ContentSid": _CONTENT_SID,
                    "ContentVariables": f'{{"1":"{code}"}}',
                },
                timeout=15.0,
            )

        if resp.status_code in (200, 201):
            logger.info(f"WhatsApp OTP sent to ***{phone[-4:]}")
            return True

        _last_error = f"Twilio {resp.status_code}: {resp.text[:300]}"
        logger.warning(_last_error)
        return False

    except Exception as e:
        _last_error = f"Request failed: {e}"
        logger.error(_last_error)
        return False
