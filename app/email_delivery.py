from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger(__name__)


def send_registration_code(email: str, code: str) -> None:
    if not settings.smtp_host:
        logger.warning("Registration code for %s: %s", email, code)
        return

    message = EmailMessage()
    message["Subject"] = "Код регистрации"
    message["From"] = settings.smtp_from_email
    message["To"] = email
    message.set_content(f"Ваш код регистрации: {code}")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        smtp.starttls()
        if settings.smtp_username and settings.smtp_password:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)
