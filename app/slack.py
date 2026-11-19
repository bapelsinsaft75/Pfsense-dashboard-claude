"""Skickar larm till Slack via en inkommande webhook (Incoming Webhooks)."""
from __future__ import annotations

import logging
import threading
import time

import httpx

from .config import Config

log = logging.getLogger("slack")

COLORS = {"critical": "#e5484d", "warning": "#ffb547", "resolved": "#3ddc84", "info": "#5aa9ff"}
LABELS = {"critical": "Kritiskt", "warning": "Varning", "resolved": "Löst", "info": "Info"}


def _esc(text: str) -> str:
    """Slack kräver att &, < och > kodas i mrkdwn-text."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Slack:
    def __init__(self, cfg: Config) -> None:
        self.url = cfg.slack_webhook_url
        self.dashboard_url = cfg.public_url
        self.status = {"enabled": self.enabled, "ok": False, "error": None, "last": None}

    @property
    def enabled(self) -> bool:
        return self.url.startswith("https://")

    def payload(self, level: str, title: str, message: str, when: str) -> dict:
        context = f"{LABELS.get(level, level)} · {when}"
        if self.dashboard_url:
            context += f" · <{self.dashboard_url}|Öppna dashboarden>"
        return {
            "text": f"{LABELS.get(level, level)}: {title}",  # visas i notiser
            "attachments": [{
                "color": COLORS.get(level, COLORS["info"]),
                "blocks": [
                    {"type": "section", "text": {"type": "mrkdwn", "text": f"*{_esc(title)}*\n{_esc(message)}"}},
                    {"type": "context", "elements": [{"type": "mrkdwn", "text": context}]},
                ],
            }],
        }

    def send(self, level: str, title: str, message: str, when: str) -> None:
        if not self.enabled:
            raise RuntimeError("Slack är inte konfigurerat (SLACK_WEBHOOK_URL saknas).")
        try:
            resp = httpx.post(self.url, json=self.payload(level, title, message, when), timeout=15)
        except httpx.HTTPError as exc:
            self.status.update(ok=False, error=f"Kan inte nå Slack: {exc}")
            raise RuntimeError(self.status["error"]) from exc
        if resp.status_code >= 400:
            hint = {403: "Webhooken är ogiltig eller borttagen.", 404: "Webhooken finns inte.",
                    410: "Kanalen är arkiverad eller borttagen."}.get(resp.status_code, "")
            self.status.update(ok=False, error=f"Slack svarade {resp.status_code}: {resp.text[:100]}. {hint}".strip())
            raise RuntimeError(self.status["error"])
        self.status.update(ok=True, error=None, last=time.time())
        log.info("Skickade till Slack: %s", title)

    def send_background(self, level: str, title: str, message: str, when: str) -> None:
        if not self.enabled:
            return

        def run():
            try:
                self.send(level, title, message, when)
            except RuntimeError as exc:
                log.warning("%s", exc)

        threading.Thread(target=run, daemon=True).start()
