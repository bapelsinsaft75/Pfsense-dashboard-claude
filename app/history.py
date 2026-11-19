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
"""


class History:
    def __init__(self, path: str) -> None:
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL" if path != ":memory:" else "PRAGMA journal_mode=MEMORY")
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()
        self.pending: dict[tuple, list[int]] = defaultdict(lambda: [0, 0, 0])

    # ── skrivning ──
    def add(self, cid: str, cc: str | None, direction: str, up: int, down: int, conns: int = 0, ts: float | None = None) -> None:
        if up <= 0 and down <= 0 and conns <= 0:
            return
        bucket = int((ts or time.time()) // BUCKET * BUCKET)
        acc = self.pending[(bucket, cid, cc or "", direction)]
        acc[0] += max(0, up)
        acc[1] += max(0, down)
        acc[2] += max(0, conns)

    def flush(self) -> int:
        with self.lock:
            rows = [(b, BUCKET, cid, cc, d, v[0], v[1], v[2]) for (b, cid, cc, d), v in self.pending.items()]
            self.pending.clear()
            if rows:
                self.db.executemany("INSERT INTO traffic VALUES(?,?,?,?,?,?,?,?)", rows)
                self.db.commit()
        return len(rows)

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
            cur.execute("DELETE FROM traffic WHERE ts<?", (int(now - retention_days * 86400),))
            cur.execute("DELETE FROM clients WHERE last_seen<?", (int(now - retention_days * 86400),))
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

    def empty(self) -> bool:
        return not self._q("SELECT 1 FROM traffic LIMIT 1")
