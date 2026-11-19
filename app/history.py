"""Trafikhistorik i SQLite.

Rader sparas i 5-minutershinkar. Data äldre än 48 timmar slås ihop till timhinkar.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from collections import defaultdict

BUCKET = 300
HOURLY_AFTER = 48 * 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS traffic(
  ts INTEGER NOT NULL, res INTEGER NOT NULL, cid TEXT NOT NULL,
  cc TEXT NOT NULL, dir TEXT NOT NULL, up INTEGER NOT NULL, down INTEGER NOT NULL, conns INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS traffic_ts ON traffic(ts);
CREATE TABLE IF NOT EXISTS clients(
  cid TEXT PRIMARY KEY, mac TEXT, ip TEXT, name TEXT, last_seen INTEGER
);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, level TEXT NOT NULL, kind TEXT NOT NULL,
  title TEXT NOT NULL, message TEXT, created INTEGER, updated INTEGER, resolved INTEGER, acked INTEGER,
  emailed INTEGER DEFAULT 0, count INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS alerts_key ON alerts(key);
CREATE TABLE IF NOT EXISTS security(
  ts INTEGER NOT NULL, source TEXT NOT NULL, label TEXT NOT NULL, prio INTEGER NOT NULL,
  cid TEXT NOT NULL, remote TEXT NOT NULL, cc TEXT NOT NULL, dir TEXT NOT NULL, count INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS security_ts ON security(ts);
CREATE TABLE IF NOT EXISTS remotes(
  ts INTEGER NOT NULL, cid TEXT NOT NULL, ip TEXT NOT NULL, cc TEXT NOT NULL, dir TEXT NOT NULL,
  port INTEGER NOT NULL, up INTEGER NOT NULL, down INTEGER NOT NULL, conns INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS remotes_cc_ts ON remotes(cc, ts);
CREATE INDEX IF NOT EXISTS remotes_ts ON remotes(ts);
CREATE TABLE IF NOT EXISTS aliases(cid TEXT PRIMARY KEY, name TEXT NOT NULL, updated INTEGER);
"""
REMOTE_BUCKET = 3600


class History:
    def __init__(self, path: str) -> None:
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL" if path != ":memory:" else "PRAGMA journal_mode=MEMORY")
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(alerts)")}
        if "slacked" not in cols:  # tillagd i en senare version
            self.db.execute("ALTER TABLE alerts ADD COLUMN slacked INTEGER DEFAULT 0")
            self.db.commit()
        if "source" not in cols:  # vilken server larmet gäller, t.ex. "Alex Plex"
            self.db.execute("ALTER TABLE alerts ADD COLUMN source TEXT")
            self.db.commit()
        self.lock = threading.Lock()
        self.pending: dict[tuple, list[int]] = defaultdict(lambda: [0, 0, 0])
        self.sec_pending: dict[tuple, int] = defaultdict(int)
        self.rem_pending: dict[tuple, list[int]] = defaultdict(lambda: [0, 0, 0])

    # ── skrivning ──
    def add(self, cid: str, cc: str | None, direction: str, up: int, down: int, conns: int = 0, ts: float | None = None,
            remote: str | None = None, port: int | None = None) -> None:
        if up <= 0 and down <= 0 and conns <= 0:
            return
        now = ts or time.time()
        if remote:
            r = self.rem_pending[(int(now // REMOTE_BUCKET * REMOTE_BUCKET), cid, remote, cc or "", direction, port or 0)]
            r[0] += max(0, up)
            r[1] += max(0, down)
            r[2] += max(0, conns)
        bucket = int(now // BUCKET * BUCKET)
        acc = self.pending[(bucket, cid, cc or "", direction)]
        acc[0] += max(0, up)
        acc[1] += max(0, down)
        acc[2] += max(0, conns)

    def add_security(self, source: str, label: str, prio: int, cid: str, remote: str, cc: str, direction: str,
                     ts: float | None = None) -> None:
        bucket = int((ts or time.time()) // BUCKET * BUCKET)
        self.sec_pending[(bucket, source, label[:200], prio, cid, remote, cc, direction)] += 1

    def flush(self) -> int:
        with self.lock:
            rows = [(b, BUCKET, cid, cc, d, v[0], v[1], v[2]) for (b, cid, cc, d), v in self.pending.items()]
            self.pending.clear()
            sec = [k + (n,) for k, n in self.sec_pending.items()]
            self.sec_pending.clear()
            rem = [k + tuple(v) for k, v in self.rem_pending.items()]
            self.rem_pending.clear()
            if rem:
                self.db.executemany("INSERT INTO remotes VALUES(?,?,?,?,?,?,?,?,?)", rem)
            if rows:
                self.db.executemany("INSERT INTO traffic VALUES(?,?,?,?,?,?,?,?)", rows)
            if sec:
                self.db.executemany("INSERT INTO security VALUES(?,?,?,?,?,?,?,?,?)", sec)
            if rows or sec or rem:
                self.db.commit()
        return len(rows) + len(sec) + len(rem)

    def insert_raw(self, rows: list[tuple]) -> None:
        with self.lock:
            self.db.executemany("INSERT INTO traffic VALUES(?,?,?,?,?,?,?,?)", rows)
            self.db.commit()

    def remember_clients(self, rows: list[tuple[str, str | None, str | None, str | None, int]]) -> None:
        if not rows:
            return
        with self.lock:
            self.db.executemany(
                "INSERT INTO clients(cid,mac,ip,name,last_seen) VALUES(?,?,?,?,?) "
                "ON CONFLICT(cid) DO UPDATE SET mac=coalesce(excluded.mac,mac), ip=coalesce(excluded.ip,ip), "
                "name=coalesce(excluded.name,name), last_seen=excluded.last_seen",
                rows,
            )
            self.db.commit()

    def compact(self, retention_days: int, now: float | None = None) -> None:
        now = now or time.time()
        cut = int((now - HOURLY_AFTER) // 3600 * 3600)
        with self.lock:
            cur = self.db.cursor()
            cur.execute(
                "INSERT INTO traffic SELECT (ts/3600)*3600, 3600, cid, cc, dir, sum(up), sum(down), sum(conns) "
                "FROM traffic WHERE res=? AND ts<? GROUP BY ts/3600, cid, cc, dir",
                (BUCKET, cut),
            )
            cur.execute("DELETE FROM traffic WHERE res=? AND ts<?", (BUCKET, cut))
            old = int(now - retention_days * 86400)
            cur.execute("DELETE FROM traffic WHERE ts<?", (old,))
            cur.execute("DELETE FROM security WHERE ts<?", (old,))
            cur.execute("DELETE FROM remotes WHERE ts<?", (old,))
            # slå ihop timrader till dygnsrader efter 48 timmar så att tabellen inte växer för mycket
            day_cut = int((now - HOURLY_AFTER) // 86400 * 86400)
            cur.execute(
                "INSERT INTO remotes SELECT (ts/86400)*86400 + 1, cid, ip, cc, dir, port, sum(up), sum(down), sum(conns) "
                "FROM remotes WHERE ts<? AND ts % 86400 != 1 GROUP BY ts/86400, cid, ip, cc, dir, port", (day_cut,))
            cur.execute("DELETE FROM remotes WHERE ts<? AND ts % 86400 != 1", (day_cut,))
            cur.execute("DELETE FROM clients WHERE last_seen<?", (old,))
            cur.execute("DELETE FROM alerts WHERE updated<? AND (resolved IS NOT NULL OR acked IS NOT NULL)", (old,))
            self.db.commit()

    # ── läsning ──
    def _q(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def client_totals(self, since: int) -> dict[str, tuple[int, int]]:
        rows = self._q("SELECT cid, sum(up), sum(down) FROM traffic WHERE ts>=? GROUP BY cid", (since,))
        return {cid: (up, down) for cid, up, down in rows}

    def country_flows(self, since: int) -> list[dict]:
        rows = self._q(
            "SELECT cc, dir, cid, sum(up+down), sum(conns) FROM traffic WHERE ts>=? AND cc!='' GROUP BY cc, dir, cid",
            (since,),
        )
        agg: dict[tuple, dict] = {}
        for cc, d, cid, total, conns in rows:
            f = agg.setdefault((cc, d), {"cc": cc, "dir": d, "bytes": 0, "conns": 0, "clients": []})
            f["bytes"] += total
            f["conns"] += conns
            f["clients"].append((total, cid))
        out = []
        for f in agg.values():
            f["clients"] = [cid for _, cid in sorted(f["clients"], reverse=True)]
            out.append(f)
        out.sort(key=lambda f: f["bytes"], reverse=True)
        return out

    def series(self, since: int, step: int, now: float | None = None) -> list[dict]:
        now = now or time.time()
        rows = self._q(
            "SELECT (ts/?)*?, sum(down), sum(up) FROM traffic WHERE ts>=? GROUP BY 1", (step, step, since)
        )
        by = {t: (d, u) for t, d, u in rows}
        start = since // step * step
        out = []
        t = start
        while t <= now:
            d, u = by.get(t, (0, 0))
            out.append({"t": t, "down_bps": d * 8 / step, "up_bps": u * 8 / step})
            t += step
        return out

    def known_clients(self, since: int) -> dict[str, dict]:
        rows = self._q("SELECT cid, mac, ip, name, last_seen FROM clients WHERE last_seen>=?", (since,))
        return {r[0]: {"mac": r[1], "ip": r[2], "name": r[3], "last_seen": r[4]} for r in rows}

    def daily_totals(self, now: float, days: int) -> dict[str, list[int]]:
        """Datamängd per klient för hela 24-timmarsperioder bakåt från nu. Index 0 = senaste dygnet."""
        rows = self._q(
            "SELECT cid, CAST((? - ts) / 86400 AS INTEGER) AS d, sum(up+down) FROM traffic "
            "WHERE ts>? GROUP BY cid, d",
            (now, now - days * 86400),
        )
        out: dict[str, list[int]] = {}
        for cid, d, total in rows:
            if 0 <= d < days:
                out.setdefault(cid, [0] * days)[d] += total
        return out

    def first_traffic(self) -> int | None:
        r = self._q("SELECT min(ts) FROM traffic")
        return r[0][0] if r and r[0][0] else None

    def security_summary(self, since: int) -> dict:
        tot = self._q("SELECT source, sum(count) FROM security WHERE ts>=? GROUP BY source", (since,))
        sigs = self._q(
            "SELECT label, min(prio), sum(count), max(ts) FROM security WHERE ts>=? AND source='suricata' "
            "GROUP BY label ORDER BY min(prio), sum(count) DESC LIMIT 8", (since,))
        pfb_clients = self._q(
            "SELECT cid, sum(count) FROM security WHERE ts>=? AND source='pfblockerng' AND dir='out' AND cid!='' "
            "GROUP BY cid ORDER BY sum(count) DESC LIMIT 6", (since,))
        pfb_cc = self._q(
            "SELECT cc, sum(count) FROM security WHERE ts>=? AND source IN ('pfblockerng','firewall') AND dir='in' "
            "AND cc!='' GROUP BY cc ORDER BY sum(count) DESC LIMIT 6", (since,))
        return {
            "totals": {s: n for s, n in tot},
            "suricata": [{"label": l, "prio": p, "count": n, "last": t} for l, p, n, t in sigs],
            "pfb_clients": [{"cid": c, "count": n} for c, n in pfb_clients],
            "blocked_countries": [{"cc": c, "count": n} for c, n in pfb_cc],
        }

    def country_detail(self, cc: str, since: int) -> dict:
        clients = self._q(
            "SELECT cid, dir, sum(up), sum(down), sum(conns) FROM traffic WHERE cc=? AND ts>=? GROUP BY cid, dir "
            "ORDER BY sum(up+down) DESC", (cc, since))
        remotes = self._q(
            "SELECT ip, dir, group_concat(DISTINCT port), group_concat(DISTINCT cid), sum(up), sum(down), sum(conns), max(ts) "
            "FROM remotes WHERE cc=? AND ts>=? GROUP BY ip, dir ORDER BY sum(up+down) DESC, sum(conns) DESC LIMIT 300",
            (cc, since - REMOTE_BUCKET))
        return {
            "clients": [{"cid": c, "dir": d, "up": u, "down": dn, "conns": n} for c, d, u, dn, n in clients],
            "remotes": [{"ip": ip, "dir": d, "ports": sorted({int(p) for p in (ports or "").split(",") if p and p != "0"}),
                         "cids": (cids or "").split(","), "up": u, "down": dn, "conns": n, "last": last}
                        for ip, d, ports, cids, u, dn, n, last in remotes],
        }

    def aliases(self) -> dict[str, str]:
        return dict(self._q("SELECT cid, name FROM aliases"))

    def set_alias(self, cid: str, name: str | None) -> None:
        with self.lock:
            if name:
                self.db.execute("INSERT INTO aliases(cid, name, updated) VALUES(?,?,?) "
                                "ON CONFLICT(cid) DO UPDATE SET name=excluded.name, updated=excluded.updated",
                                (cid, name, int(time.time())))
            else:
                self.db.execute("DELETE FROM aliases WHERE cid=?", (cid,))
            self.db.commit()

    def empty(self) -> bool:
        return not self._q("SELECT 1 FROM traffic LIMIT 1")
