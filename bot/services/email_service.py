"""Email service for sending emails via SMTP."""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import config


def send_email(to_email: str, subject: str, body: str) -> tuple[bool, str]:
    """
    Send an email via SMTP.

    Returns:
        (success: bool, message: str)
    """
    smtp_email = getattr(config, 'SMTP_EMAIL', '')
    smtp_password = getattr(config, 'SMTP_PASSWORD', '')
    smtp_host = getattr(config, 'SMTP_HOST', 'smtp.gmail.com')
    smtp_port = getattr(config, 'SMTP_PORT', 587)

    if not smtp_email or not smtp_password:
        return False, "Email not configured. Set SMTP_EMAIL and SMTP_PASSWORD."

    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = smtp_email
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        # Connect to SMTP server
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_email, smtp_password)
            server.send_message(msg)

        return True, f"Email sent to {to_email}"

    except smtplib.SMTPAuthenticationError:
        return False, "Email auth failed. Check credentials."
    except smtplib.SMTPRecipientsRefused:
        return False, f"Invalid recipient: {to_email}"
    except Exception as e:
        return False, f"Email failed: {type(e).__name__}"


def is_email_configured() -> bool:
    """Check if email is configured."""
    smtp_email = getattr(config, 'SMTP_EMAIL', '')
    smtp_password = getattr(config, 'SMTP_PASSWORD', '')
    return bool(smtp_email and smtp_password)
