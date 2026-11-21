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


# larm som är händelser (inget som går ner och upp igen) – räknas ihop per typ i sammanfattningen
EVENT_KINDS = {"suricata", "pfblocker", "new_device", "data_anomaly", "gateway_degraded"}
EVENT_LABEL = {"suricata": "Suricata-larm", "pfblocker": "pfBlockerNG-blockeringar", "new_device": "Nya enheter",
               "data_anomaly": "Ovanligt hög dataförbrukning", "gateway_degraded": "Paketförlust eller hög fördröjning"}


def _hm(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, TZ).strftime("%H:%M")


def digest_text(rows: list[dict], now: float, quiet_start: str, quiet_end: str, emoji: bool = False) -> dict:
    """Formulera sammanfattningen: ”Mellan 03:12 och 05:48 hände följande: …”."""
    groups: dict[str, list[dict]] = {}
    events: dict[str, list[dict]] = {}
    for r in sorted(rows, key=lambda r: r["created"]):
        (events.setdefault(r["kind"], []) if r["kind"] in EVENT_KINDS else groups.setdefault(r["key"], [])).append(r)
    lines: list[tuple[float, str]] = []
    still = []
    for rs in groups.values():
        first, last = rs[0], rs[-1]
        mins = sum(max(1, round(((r["resolved"] or now) - r["created"]) / 60)) for r in rs)
        if len(rs) == 1:
            txt = f"{_hm(first['created'])} {first['title']}"
            txt += f" – uppe igen {_hm(first['resolved'])} ({mins} min)" if first["resolved"] else " – fortfarande nere"
        else:
            txt = f"{_hm(first['created'])} {first['title']} – {len(rs)} gånger, totalt {mins} min"
            if not last["resolved"]:
                txt += ", fortfarande nere"
        if not last["resolved"]:
            still.append(f"{first['title']} (sedan {_hm(last['created'])})")
        lines.append((first["created"], txt))
    for kind, rs in events.items():
        names = ", ".join(dict.fromkeys(r["title"] for r in rs[:3]))
        label = EVENT_LABEL.get(kind, kind)
        lines.append((rs[0]["created"], f"{_hm(rs[0]['created'])} {label}: {len(rs)} st – {names}" if len(rs) > 1 else f"{_hm(rs[0]['created'])} {rs[0]['title']}"))
    lines.sort()
    times = [r["created"] for r in rows] + [r["resolved"] for r in rows if r["resolved"]]
    start, end = min(times), max(times)
    head = f"Kl {_hm(start)} hände följande:" if _hm(start) == _hm(end) else f"Mellan {_hm(start)} och {_hm(end)} hände följande:"
    n = len(lines)
    if still:
        status = ("⚠️ " if emoji else "") + "Fortfarande nere: " + ", ".join(still) + ". Allt annat är uppe igen."
    else:
        status = ("✅ " if emoji else "✓ ") + "Samtliga enheter, servrar, containrar och virtuella maskiner är nu uppe."
    return {"subject": f"Sammanfattning: {n} {'händelse' if n == 1 else 'händelser'} mellan {_hm(start)} och {_hm(end)}"
            if _hm(start) != _hm(end) else f"Sammanfattning: {n} {'händelse' if n == 1 else 'händelser'} kl {_hm(start)}",
            "head": head, "lines": [t for _, t in lines], "status": status, "still": still,
            "footer": f"Larmen skickades inte när de hände eftersom tysta tider var på ({quiet_start}–{quiet_end})."}


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

    def recipients_for(self, kind: str, level: str, source: str | None = None) -> list[str]:
        """Vilka e-postadresser som ska få ett larm av den här typen (och från den här servern)."""
        if not self.mailer.configured:
            return []
        return self.settings.route(kind, source)["emails"]

    def _quiet_blocks(self, level: str) -> bool:
        quiet, _ = self.settings.quiet_now()
        return quiet and not (level == "critical" and self.settings.get("quiet_allow_critical"))

    def flush_quiet_digest(self) -> bool:
        """Sammanfattning av det som hände under den tysta tiden, när den är slut.

        Den är en egen larmtyp (quiet_digest) som kan gå till bannern, Slack och valda e-postadresser.
        Varje mottagare får bara med de larm den själv skulle ha fått.
        """
        if not self.quiet_queue or self.settings.quiet_now()[0]:
            return False
        items, self.quiet_queue = self.quiet_queue, []
        route = self.settings.route("quiet_digest")
        if not (route["banner"] or route["slack"] or route["emails"]):
            return False
        ids = [i[8] for i in items if len(i) > 8 and i[8]]
        if not ids:
            return False
        marks = ",".join("?" * len(ids))
        with self.h.lock:
            rows = self.h.db.execute(
                f"SELECT id, key, level, kind, title, created, resolved, source FROM alerts WHERE id IN ({marks})", ids).fetchall()
        rows = [dict(zip(("id", "key", "level", "kind", "title", "created", "resolved", "source"), r)) for r in rows]
        if not rows:
            return False
        now = time.time()
        qs, qe = self.settings.get("quiet_start"), self.settings.get("quiet_end")
        dash = self.slack.dashboard_url if self.slack and self.slack.dashboard_url else ""
        sent = False
        # e-post: per adress, bara de larm adressen själv skulle ha fått
        allowed = {e.lower() for e in route["emails"]}
        if allowed and self.mailer.configured:
            per_addr: dict[str, list] = {}
            for r in rows:
                for addr in self.recipients_for(r["kind"], r["level"], r["source"]):
                    if addr.lower() in allowed:
                        per_addr.setdefault(addr, []).append(r)
            for addr, subset in per_addr.items():
                d = digest_text(subset, now, qs, qe)
                body = "\n".join([d["head"], "", *d["lines"], "", d["status"], "", d["footer"]]) + "\n"
                if dash:
                    body += f"Dashboard: {dash}\n"
                self.mailer.send_background(d["subject"], body, [addr])
                sent = True
        if route["slack"] and self.slack and self.slack.enabled:
            subset = [r for r in rows if self._should_slack(r["kind"], r["level"], r["source"])]
            if subset:
                d = digest_text(subset, now, qs, qe, emoji=True)
                self.slack.send_background("resolved" if not d["still"] else "warning", d["subject"],
                                           "\n".join([d["head"], *("• " + x for x in d["lines"][:25]), d["status"], f"_{d['footer']}_"]),
                                           _when(now))
                sent = True
        if route["banner"]:
            d = digest_text(rows, now, qs, qe)
            with self.h.lock:
                self.h.db.execute(
                    "INSERT INTO alerts(key, level, kind, title, message, created, updated, source) VALUES(?,?,?,?,?,?,?,?)",
                    (f"digest:{int(now)}", "info", "quiet_digest", d["subject"],
                     "\n".join([*d["lines"], d["status"], d["footer"]]), int(now), int(now), None))
                self.h.db.commit()
            sent = True
        return sent

    def is_active_title(self, title: str) -> bool:
        with self.h.lock:
            return self.h.db.execute("SELECT 1 FROM alerts WHERE title=? AND resolved IS NULL", (title,)).fetchone() is not None

    def _should_mail(self, level: str) -> bool:
        return level == "critical" or (level == "warning" and self.settings.get("email_warnings"))

    def _should_slack(self, kind: str, level: str, source: str | None = None) -> bool:
        return bool(self.settings.route(kind, source)["slack"])

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

    def raise_(self, key: str, level: str, kind: str, title: str, message: str, now: float | None = None,
               source: str | None = None) -> int | None:
        """Skapa ett larm, eller uppdatera det om samma larm redan är aktivt. Avstängda larmtyper ignoreras."""
        route = self.settings.route(kind, source)
        banner = route["banner"]
        if not (banner or route["slack"] or route["emails"]):
            return None  # helt avstängd larmtyp
        now = now or time.time()
        with self.h.lock:
            row = self.h.db.execute("SELECT id, level FROM alerts WHERE key=? AND resolved IS NULL", (key,)).fetchone()
            if row:
                self.h.db.execute("UPDATE alerts SET updated=?, message=?, count=count+1 WHERE id=?",
                                  (int(now), message, row[0]))
                self.h.db.commit()
                return row[0]
            cur = self.h.db.execute(
                "INSERT INTO alerts(key, level, kind, title, message, created, updated, source) VALUES(?,?,?,?,?,?,?,?)",
                (key, level, kind, title, message, int(now), int(now), source),
            )
            self.h.db.commit()
            alert_id = cur.lastrowid
            if not banner:  # skickas men visas inte i bannern (finns kvar under Händelser)
                self.h.db.execute("UPDATE alerts SET acked=? WHERE id=?", (int(now), alert_id))
                self.h.db.commit()
        log.info("Larm (%s): %s – %s", level, title, message)
        to = self.recipients_for(kind, level, source)
        if self._quiet_blocks(level):
            # sparas till sammanfattningen när den tysta tiden är slut
            self.quiet_queue.append(("raised", level, title, message, now, kind, source, key, alert_id))
            if to or self._should_slack(kind, level, source):
                return alert_id
        if to:
            body = f"{LEVEL_LABEL.get(level, level)}: {title}\n\n{message}\n\nTid: {_when(now)}\n"
            if self.slack and self.slack.dashboard_url:
                body += f"Dashboard: {self.slack.dashboard_url}\n"
            if self._mail(key, title, body, to):
                with self.h.lock:
                    self.h.db.execute("UPDATE alerts SET emailed=1 WHERE id=?", (alert_id,))
                    self.h.db.commit()
        if self._should_slack(kind, level, source) and self._post_slack(key, level, title, message, _when(now)):
            with self.h.lock:
                self.h.db.execute("UPDATE alerts SET slacked=1 WHERE id=?", (alert_id,))
                self.h.db.commit()
        return alert_id

    def resolve(self, key: str, message: str | None = None, now: float | None = None) -> None:
        now = now or time.time()
        with self.h.lock:
            rows = self.h.db.execute(
                "SELECT id, level, title, created, emailed, slacked, kind, source FROM alerts WHERE key=? AND resolved IS NULL",
                (key,)
            ).fetchall()
            if not rows:
                return
            self.h.db.execute("UPDATE alerts SET resolved=?, updated=? WHERE key=? AND resolved IS NULL",
                              (int(now), int(now), key))
            self.h.db.commit()
        for _id, level, title, created, emailed, slacked, kind, source in rows:
            log.info("Löst: %s", title)
            mins = max(1, int((now - created) / 60))
            if (slacked or emailed) and level == "critical" and self._quiet_blocks(level):
                self.quiet_queue.append(("resolved", level, title, message or "", now, kind, source))
                continue
            if slacked and level == "critical":
                self._post_slack(key + ":resolved", "resolved", f"Löst: {title}",
                                 f"{message or 'Problemet är inte längre aktivt.'} Varade i cirka {mins} minuter.",
                                 f"{_when(created)} – {_when(now)}", force=True)
            if emailed and level == "critical":
                body = (f"Löst: {title}\n\n{message or 'Problemet är inte längre aktivt.'}\n\n"
                        f"Varade i cirka {mins} minuter ({_when(created)} – {_when(now)}).\n")
                self._mail(key + ":resolved", f"Löst: {title}", body, self.recipients_for(kind, level, source), force=True)

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

    def note(self, kind: str, title: str, message: str, now: float | None = None) -> None:
        """En händelse som bara visas under Händelser (ingen banner, inget mejl)."""
        now = int(now or time.time())
        with self.h.lock:
            self.h.db.execute(
                "INSERT INTO alerts(key, level, kind, title, message, created, updated, resolved, acked) VALUES(?,?,?,?,?,?,?,?,?)",
                (f"note:{kind}:{now}", "info", kind, title, message, now, now, now, now))
            self.h.db.commit()
        log.info("Händelse: %s – %s", title, message)

    def recent(self, limit: int = 30) -> list[dict]:
        with self.h.lock:
            cols = ("id", "level", "kind", "title", "message", "created", "updated", "resolved", "count")
            rows = self.h.db.execute(
                f"SELECT {', '.join(cols)} FROM alerts ORDER BY updated DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(zip(cols, r)) for r in rows]
