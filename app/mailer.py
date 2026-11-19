"""Skickar e-post via SMTP, t.ex. Proton Mail med en SMTP-token (smtp.protonmail.ch:587)."""
from __future__ import annotations

import logging
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
import datetime as dt
from email.utils import format_datetime, make_msgid
from zoneinfo import ZoneInfo

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
        self.status = {"enabled": self.configured, "ok": False, "error": None, "last": None}
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        """SMTP-server och inloggning finns. Mottagare kommer från inställningarna eller MAIL_TO."""
        return bool(self.host and self.user and self.token)

    @property
    def enabled(self) -> bool:
        return self.configured

    def send(self, subject: str, body: str, to: list[str] | None = None) -> None:
        if not self.configured:
            raise RuntimeError("E-post är inte konfigurerat (SMTP_USER och SMTP_TOKEN krävs).")
        to = to or self.to
        if not to:
            raise RuntimeError("Det finns ingen mottagare. Lägg till en under Inställningar → Aviseringar.")
        msg = EmailMessage()
        msg["Subject"] = f"{self.prefix} {subject}".strip()
        msg["From"] = self.sender
        msg["To"] = ", ".join(to)
        msg["Date"] = format_datetime(dt.datetime.now(ZoneInfo("Europe/Stockholm")))
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

    def send_background(self, subject: str, body: str, to: list[str] | None = None) -> None:
        if not self.configured:
            return

        def run():
            try:
                self.send(subject, body, to)
            except RuntimeError as exc:
                log.warning("%s", exc)

        threading.Thread(target=run, daemon=True).start()
