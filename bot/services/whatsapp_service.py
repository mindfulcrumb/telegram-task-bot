"""WhatsApp OTP delivery via Twilio REST API (httpx, no SDK needed)."""
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "")


def is_configured() -> bool:
    """Check if Twilio WhatsApp credentials are set."""
    return bool(_ACCOUNT_SID and _AUTH_TOKEN and _WHATSAPP_FROM)


async def send_otp(phone: str, code: str) -> bool:
    """Send a 6-digit OTP via WhatsApp using Twilio.

    Args:
        phone: International format with + prefix (e.g. +351912345678)
        code: The 6-digit verification code

    Returns:
        True if message was accepted by Twilio, False otherwise.
    """
    if not is_configured():
        logger.error("Twilio WhatsApp not configured — missing env vars")
        return False

    url = f"https://api.twilio.com/2010-04-01/Accounts/{_ACCOUNT_SID}/Messages.json"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                auth=(_ACCOUNT_SID, _AUTH_TOKEN),
                data={
                    "From": f"whatsapp:{_WHATSAPP_FROM}",
                    "To": f"whatsapp:{phone}",
                    "Body": f"Your Zoe verification code: {code}. It expires in 5 minutes.",
                },
                timeout=15.0,
            )

        if resp.status_code in (200, 201):
            logger.info(f"WhatsApp OTP sent to ***{phone[-4:]}")
            return True

        logger.warning(f"Twilio error {resp.status_code}: {resp.text[:200]}")
        return False

    except Exception as e:
        logger.error(f"WhatsApp OTP send failed: {e}")
        return False
