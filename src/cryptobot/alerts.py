"""Email alerts through Gmail. Settings come from .env; if they are missing, alerts are only logged.

Gmail needs an "app password" (Google account -> Security -> 2-Step Verification -> App passwords),
not your normal password. Sending never crashes the bot: a failed email is logged and ignored.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger("cryptobot.alerts")


def send(subject: str, body: str) -> bool:
    sender = os.getenv("ALERT_EMAIL_FROM")
    to = os.getenv("ALERT_EMAIL_TO") or sender
    password = os.getenv("ALERT_EMAIL_APP_PASSWORD")
    log.info("ALERT: %s | %s", subject, body.replace("\n", " / ")[:300])
    if not (sender and password):
        log.warning("email not configured; alert only logged")
        return False
    msg = EmailMessage()
    msg["Subject"] = f"[cryptobot] {subject}"
    msg["From"], msg["To"] = sender, to
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)
        return True
    except Exception as e:
        log.error("email failed: %s: %s", type(e).__name__, e)
        return False
