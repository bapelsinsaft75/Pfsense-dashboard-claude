"""Larm: sparas i SQLite, visas som banner och skickas via e-post och Slack när det är viktigt."""
from __future__ import annotations

import datetime as dt
import logging
import time
from zoneinfo import ZoneInfo

from .history import History
from .mailer import Mailer
from .settings import Settings
from .slack import Slack

log = logging.getLogger("alerts")
TZ = ZoneInfo("Europe/Stockholm")
RESOLVED_VISIBLE = 6 * 3600  # lösta kritiska larm syns i bannern så här länge
MAIL_COOLDOWN = 30 * 60

LEVEL_LABEL = {"critical": "Kritiskt", "warning": "Varning", "info": "Info"}


def _when(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d %H:%M")


class Alerts:
    def __init__(self, history: History, mailer: Mailer, settings: Settings, slack: Slack | None = None) -> None:
        self.h = history
        self.mailer = mailer
        self.slack = slack
        self.settings = settings
        self._last_mail: dict[str, float] = {}
        self._last_slack: dict[str, float] = {}
        self.quiet_queue: list[tuple] = []  # (händelse, nivå, titel, text, tid, typ)

    def recipients_for(self, kind: str, level: str) -> list[str]:
        """Vilka e-postadresser som ska få ett larm av den här typen."""
        if not self.mailer.configured:
            return []
        rec = self.settings.get("recipients")
        if rec is None:  # inga mottagare sparade i inställningarna: MAIL_TO som tidigare
            return list(self.mailer.to) if self._should_mail(level) else []
        return [r["email"] for r in rec if kind in r["kinds"]]

    def _quiet_blocks(self, level: str) -> bool:
        quiet, _ = self.settings.quiet_now()
        return quiet and not (level == "critical" and self.settings.get("quiet_allow_critical"))

    def flush_quiet_digest(self) -> bool:
        """Skicka en sammanfattning av det som hände under den tysta tiden, när den är slut."""
        if not self.quiet_queue or self.settings.quiet_now()[0]:
            return False
        items, self.quiet_queue = self.quiet_queue, []
        if not self.settings.get("quiet_digest"):
            return False
        def digest(subset):
            raised = [i for i in subset if i[0] == "raised"]
            still = [i for i in raised if self.is_active_title(i[2])]
            lines = [f"{_when(i[4])}  {'Löst: ' if i[0] == 'resolved' else ''}{LEVEL_LABEL.get(i[1], i[1])}: {i[2]}"
                     for i in subset]
            head = (f"Under den tysta tiden kom {len(raised)} larm"
                    + (f", varav {len(still)} fortfarande aktiva." if still else ", alla är lösta nu." if raised else "."))
            return raised, still, lines, head

        per_addr: dict[str, list] = {}
        for item in items:
            for addr in self.recipients_for(item[5], item[1]):
                per_addr.setdefault(addr, []).append(item)
        for addr, subset in per_addr.items():
            raised, _still, lines, head = digest(subset)
            self.mailer.send_background(f"Sammanfattning av tysta tiden: {len(raised)} larm",
                                        head + "\n\n" + "\n".join(lines) + "\n", [addr])
        raised, still, lines, head = digest(items)
        subject = f"Sammanfattning av tysta tiden: {len(raised)} larm"
        if self.slack and self.slack.enabled:
            level = "critical" if any(i[1] == "critical" for i in still) else "warning" if still else "info"
            self.slack.send_background(level, subject, head + "\n" + "\n".join(lines[:25]), _when(time.time()))
        return True

    def is_active_title(self, title: str) -> bool:
        with self.h.lock:
            return self.h.db.execute("SELECT 1 FROM alerts WHERE title=? AND resolved IS NULL", (title,)).fetchone() is not None

    def _should_mail(self, level: str) -> bool:
        return level == "critical" or (level == "warning" and self.settings.get("email_warnings"))

    def _should_slack(self, level: str) -> bool:
        return level == "critical" or (level == "warning" and self.settings.get("slack_warnings"))

    def _post_slack(self, key: str, level: str, title: str, message: str, when: str, force: bool = False) -> bool:
        if not self.slack or not self.slack.enabled:
            return False
        now = time.time()
        if not force and now - self._last_slack.get(key, 0) < MAIL_COOLDOWN:
            return False
        self._last_slack[key] = now
        self.slack.send_background(level, title, message, when)
        return True

    def _mail(self, key: str, subject: str, body: str, to: list[str], force: bool = False) -> bool:
        if not to:
            return False
        now = time.time()
        if not force and now - self._last_mail.get(key, 0) < MAIL_COOLDOWN:
            return False
        self._last_mail[key] = now
        self.mailer.send_background(subject, body, to)
        return True

    def raise_(self, key: str, level: str, kind: str, title: str, message: str, now: float | None = None) -> int | None:
        """Skapa ett larm, eller uppdatera det om samma larm redan är aktivt. Avstängda larmtyper ignoreras."""
        if not self.settings.kind_enabled(kind):
            return None
        now = now or time.time()
        with self.h.lock:
            row = self.h.db.execute("SELECT id, level FROM alerts WHERE key=? AND resolved IS NULL", (key,)).fetchone()
            if row:
                self.h.db.execute("UPDATE alerts SET updated=?, message=?, count=count+1 WHERE id=?",
                                  (int(now), message, row[0]))
                self.h.db.commit()
                return row[0]
            cur = self.h.db.execute(
                "INSERT INTO alerts(key, level, kind, title, message, created, updated) VALUES(?,?,?,?,?,?,?)",
                (key, level, kind, title, message, int(now), int(now)),
            )
            self.h.db.commit()
            alert_id = cur.lastrowid
        log.info("Larm (%s): %s – %s", level, title, message)
        to = self.recipients_for(kind, level)
        if self._quiet_blocks(level) and (to or self._should_slack(level)):
            self.quiet_queue.append(("raised", level, title, message, now, kind))
            return alert_id
        if to:
            body = f"{LEVEL_LABEL.get(level, level)}: {title}\n\n{message}\n\nTid: {_when(now)}\n"
            if self.slack and self.slack.dashboard_url:
                body += f"Dashboard: {self.slack.dashboard_url}\n"
            if self._mail(key, title, body, to):
                with self.h.lock:
                    self.h.db.execute("UPDATE alerts SET emailed=1 WHERE id=?", (alert_id,))
                    self.h.db.commit()
        if self._should_slack(level) and self._post_slack(key, level, title, message, _when(now)):
            with self.h.lock:
                self.h.db.execute("UPDATE alerts SET slacked=1 WHERE id=?", (alert_id,))
                self.h.db.commit()
        return alert_id

    def resolve(self, key: str, message: str | None = None, now: float | None = None) -> None:
        now = now or time.time()
        with self.h.lock:
            rows = self.h.db.execute(
                "SELECT id, level, title, created, emailed, slacked, kind FROM alerts WHERE key=? AND resolved IS NULL", (key,)
            ).fetchall()
            if not rows:
                return
            self.h.db.execute("UPDATE alerts SET resolved=?, updated=? WHERE key=? AND resolved IS NULL",
                              (int(now), int(now), key))
            self.h.db.commit()
        for _id, level, title, created, emailed, slacked, kind in rows:
            log.info("Löst: %s", title)
            mins = max(1, int((now - created) / 60))
            if (slacked or emailed) and level == "critical" and self._quiet_blocks(level):
                self.quiet_queue.append(("resolved", level, title, message or "", now, kind))
                continue
            if slacked and level == "critical":
                self._post_slack(key + ":resolved", "resolved", f"Löst: {title}",
                                 f"{message or 'Problemet är inte längre aktivt.'} Varade i cirka {mins} minuter.",
                                 f"{_when(created)} – {_when(now)}", force=True)
            if emailed and level == "critical":
                body = (f"Löst: {title}\n\n{message or 'Problemet är inte längre aktivt.'}\n\n"
                        f"Varade i cirka {mins} minuter ({_when(created)} – {_when(now)}).\n")
                self._mail(key + ":resolved", f"Löst: {title}", body, self.recipients_for(kind, level), force=True)

    def expire(self, kinds: tuple[str, ...], quiet_seconds: int, now: float | None = None) -> None:
        """Avsluta händelselarm (t.ex. Suricata) som varit tysta en tid, utan att mejla."""
        now = now or time.time()
        marks = ",".join("?" * len(kinds))
        with self.h.lock:
            self.h.db.execute(
                f"UPDATE alerts SET resolved=? WHERE resolved IS NULL AND kind IN ({marks}) AND updated<?",
                (int(now), *kinds, int(now - quiet_seconds)),
            )
            self.h.db.commit()

    def is_active(self, key: str) -> bool:
        with self.h.lock:
            return self.h.db.execute("SELECT 1 FROM alerts WHERE key=? AND resolved IS NULL", (key,)).fetchone() is not None

    def ack(self, alert_id: int) -> bool:
        with self.h.lock:
            cur = self.h.db.execute("UPDATE alerts SET acked=? WHERE id=? AND acked IS NULL", (int(time.time()), alert_id))
            self.h.db.commit()
            return cur.rowcount > 0

    def ack_all(self) -> int:
        with self.h.lock:
            cur = self.h.db.execute("UPDATE alerts SET acked=? WHERE acked IS NULL", (int(time.time()),))
            self.h.db.commit()
            return cur.rowcount

    def _rows(self, where: str, args: tuple, limit: int) -> list[dict]:
        cols = ("id", "key", "level", "kind", "title", "message", "created", "updated", "resolved", "acked", "emailed", "count")
        with self.h.lock:
            rows = self.h.db.execute(
                f"SELECT {', '.join(cols)} FROM alerts WHERE {where} ORDER BY "
                "CASE level WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, updated DESC LIMIT ?",
                args + (limit,),
            ).fetchall()
        return [dict(zip(cols, r)) for r in rows]

    def banner(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        return self._rows("acked IS NULL AND (resolved IS NULL OR (level='critical' AND resolved>?))",
                          (int(now - RESOLVED_VISIBLE),), 20)

    def recent(self, limit: int = 30) -> list[dict]:
        with self.h.lock:
            cols = ("id", "level", "kind", "title", "message", "created", "updated", "resolved", "count")
            rows = self.h.db.execute(
                f"SELECT {', '.join(cols)} FROM alerts ORDER BY updated DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(zip(cols, r)) for r in rows]
