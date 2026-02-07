"""Email service for sending emails via Agentmail or SMTP."""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import config


def send_via_agentmail(to_email: str, subject: str, body: str) -> tuple[bool, str]:
    """Send email via Agentmail API."""
    try:
        from agentmail import AgentMail

        api_key = getattr(config, 'AGENTMAIL_API_KEY', '')
        inbox_email = getattr(config, 'AGENTMAIL_INBOX', '')

        if not api_key or not inbox_email:
            return False, "Agentmail not configured"

        client = AgentMail(api_key=api_key)

        client.inboxes.messages.send(
            inbox_id=inbox_email,
            to=[to_email],
            subject=subject,
            text=body,
            html=f"<p>{body}</p>"
        )

        return True, f"Email sent to {to_email}"

    except ImportError:
        return False, "Agentmail library not installed"
    except Exception as e:
        return False, f"Agentmail error: {type(e).__name__}"


def send_via_smtp(to_email: str, subject: str, body: str) -> tuple[bool, str]:
    """Send email via SMTP."""
    smtp_email = getattr(config, 'SMTP_EMAIL', '')
    smtp_password = getattr(config, 'SMTP_PASSWORD', '')
    smtp_host = getattr(config, 'SMTP_HOST', 'smtp.gmail.com')
    smtp_port = getattr(config, 'SMTP_PORT', 587)

    if not smtp_email or not smtp_password:
        return False, "SMTP not configured"

    try:
        msg = MIMEMultipart()
        msg['From'] = smtp_email
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_email, smtp_password)
            server.send_message(msg)

        return True, f"Email sent to {to_email}"

    except smtplib.SMTPAuthenticationError:
        return False, "SMTP auth failed"
    except smtplib.SMTPRecipientsRefused:
        return False, f"Invalid recipient: {to_email}"
    except Exception as e:
        return False, f"SMTP error: {type(e).__name__}"


def send_email(to_email: str, subject: str, body: str) -> tuple[bool, str]:
    """
    Send an email - tries Agentmail first, then SMTP.

    Returns:
        (success: bool, message: str)
    """
    # Try Agentmail first if configured
    if getattr(config, 'AGENTMAIL_API_KEY', ''):
        return send_via_agentmail(to_email, subject, body)

    # Fall back to SMTP
    if getattr(config, 'SMTP_EMAIL', '') and getattr(config, 'SMTP_PASSWORD', ''):
        return send_via_smtp(to_email, subject, body)

    return False, "Email not configured. Set AGENTMAIL_API_KEY or SMTP credentials."


def is_email_configured() -> bool:
    """Check if email is configured."""
    has_agentmail = bool(getattr(config, 'AGENTMAIL_API_KEY', ''))
    has_smtp = bool(getattr(config, 'SMTP_EMAIL', '') and getattr(config, 'SMTP_PASSWORD', ''))
    return has_agentmail or has_smtp
