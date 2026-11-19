"""Skickar e-post via SMTP, t.ex. Proton Mail med en SMTP-token (smtp.protonmail.ch:587)."""
from __future__ import annotations

import logging
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .config import Config

log = logging.getLogger("mailer")


class Mailer:
    def __init__(self, cfg: Config) -> None:
        self.host = cfg.smtp_host
        self.port = cfg.smtp_port
        self.user = cfg.smtp_user
        self.token = cfg.smtp_token
        self.sender = cfg.mail_from or cfg.smtp_user
        self.to = [a.strip() for a in cfg.mail_to.split(",") if a.strip()]
        self.prefix = cfg.mail_subject_prefix
        self.status = {"enabled": self.enabled, "ok": False, "error": None, "last": None}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.user and self.token and self.to)

    def send(self, subject: str, body: str) -> None:
        if not self.enabled:
            raise RuntimeError("E-post är inte konfigurerat (SMTP_USER, SMTP_TOKEN och MAIL_TO krävs).")
        msg = EmailMessage()
        msg["Subject"] = f"{self.prefix} {subject}".strip()
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.to)
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=self.sender.split("@")[-1] if "@" in self.sender else None)
        msg.set_content(body)
        ctx = ssl.create_default_context()
        with self._lock:
            try:
                if self.port == 465:
                    with smtplib.SMTP_SSL(self.host, self.port, context=ctx, timeout=30) as s:
                        s.login(self.user, self.token)
                        s.send_message(msg)
                else:
                    with smtplib.SMTP(self.host, self.port, timeout=30) as s:
                        s.starttls(context=ctx)
                        s.login(self.user, self.token)
                        s.send_message(msg)
            except smtplib.SMTPAuthenticationError as exc:
                self.status.update(ok=False, error="SMTP-inloggningen nekades. Kontrollera adressen och SMTP-token.")
                raise RuntimeError(self.status["error"]) from exc
            except (smtplib.SMTPException, OSError) as exc:
                self.status.update(ok=False, error=f"Kunde inte skicka e-post: {exc}")
                raise RuntimeError(self.status["error"]) from exc
        self.status.update(ok=True, error=None, last=time.time())
        log.info("Skickade e-post: %s", subject)

    def send_background(self, subject: str, body: str) -> None:
        if not self.enabled:
            return

        def run():
            try:
                self.send(subject, body)
            except RuntimeError as exc:
                log.warning("%s", exc)

        threading.Thread(target=run, daemon=True).start()
