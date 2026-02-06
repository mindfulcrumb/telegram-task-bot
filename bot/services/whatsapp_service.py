"""WhatsApp service for sending messages via Twilio."""
import config


def send_whatsapp(to_number: str, message: str) -> tuple[bool, str]:
    """
    Send a WhatsApp message via Twilio.

    Args:
        to_number: Phone number with country code (e.g., +1234567890)
        message: Message text to send

    Returns:
        (success: bool, message: str)
    """
    account_sid = getattr(config, 'TWILIO_ACCOUNT_SID', '')
    auth_token = getattr(config, 'TWILIO_AUTH_TOKEN', '')
    whatsapp_from = getattr(config, 'TWILIO_WHATSAPP_FROM', '')

    if not all([account_sid, auth_token, whatsapp_from]):
        return False, "WhatsApp not configured. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_WHATSAPP_FROM."

    try:
        from twilio.rest import Client

        client = Client(account_sid, auth_token)

        # Ensure proper format
        if not to_number.startswith('+'):
            to_number = '+' + to_number

        # Send via WhatsApp
        msg = client.messages.create(
            body=message,
            from_=f'whatsapp:{whatsapp_from}',
            to=f'whatsapp:{to_number}'
        )

        return True, f"WhatsApp sent to {to_number}"

    except ImportError:
        return False, "Twilio library not installed. Run: pip install twilio"
    except Exception as e:
        error_msg = str(e)
        if "unverified" in error_msg.lower():
            return False, f"Number {to_number} not verified in Twilio sandbox. They need to join first."
        return False, f"WhatsApp failed: {type(e).__name__}"


def is_whatsapp_configured() -> bool:
    """Check if WhatsApp is configured."""
    account_sid = getattr(config, 'TWILIO_ACCOUNT_SID', '')
    auth_token = getattr(config, 'TWILIO_AUTH_TOKEN', '')
    whatsapp_from = getattr(config, 'TWILIO_WHATSAPP_FROM', '')
    return bool(account_sid and auth_token and whatsapp_from)
