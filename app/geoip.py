"""Landsuppslag för IP-adresser med en lokal MMDB-fil (DB-IP Lite eller MaxMind GeoLite2)."""
from __future__ import annotations

import asyncio
import datetime as dt
import gzip
import logging
import os
import shutil
import time
from collections import OrderedDict

import httpx
import maxminddb

from .config import Config, is_public

log = logging.getLogger("geoip")

DBIP_URL = "https://download.db-ip.com/free/dbip-country-lite-{y}-{m:02d}.mmdb.gz"
MAX_AGE_DAYS = 32


class GeoIP:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.custom = bool(cfg.geoip_path)
        self.path = cfg.geoip_path or os.path.join(cfg.data_dir, "dbip-country-lite.mmdb")
        self.reader: maxminddb.Reader | None = None
        self.cache: OrderedDict[str, str | None] = OrderedDict()
        self.status = {"ok": False, "error": None, "source": None, "updated": None}

    def _open(self) -> None:
        reader = maxminddb.open_database(self.path)
        old, self.reader = self.reader, reader
        self.cache.clear()
        if old:
            old.close()
        meta = reader.metadata()
        self.status.update(
            ok=True,
            error=None,
            source=meta.database_type,
            updated=dt.datetime.fromtimestamp(meta.build_epoch).date().isoformat(),
        )
        log.info("GeoIP-databas laddad: %s (%s)", self.path, meta.database_type)

    async def ensure(self) -> None:
        """Öppna databasen; ladda ner DB-IP Lite om den saknas eller är gammal."""
        needs = not os.path.exists(self.path)
        if not needs and not self.custom:
            needs = time.time() - os.path.getmtime(self.path) > MAX_AGE_DAYS * 86400
        if needs and not self.custom and self.cfg.geoip_auto_download:
            try:
                await self._download()
            except Exception as exc:  # noqa: BLE001
                log.warning("Kunde inte ladda ner GeoIP-databas: %s", exc)
                self.status["error"] = f"Nedladdning misslyckades: {exc}"
        if os.path.exists(self.path):
            try:
                self._open()
            except Exception as exc:  # noqa: BLE001
                self.status.update(ok=False, error=f"Kunde inte läsa {self.path}: {exc}")
        elif not self.status["error"]:
            self.status["error"] = f"Hittar ingen GeoIP-databas på {self.path}"

    async def _download(self) -> None:
        today = dt.date.today()
        first = today.replace(day=1)
        prev = (first - dt.timedelta(days=1)).replace(day=1)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            for month in (first, prev):
                url = DBIP_URL.format(y=month.year, m=month.month)
                tmp_gz = self.path + ".download.gz"
                try:
                    async with client.stream("GET", url) as resp:
                        resp.raise_for_status()
                        with open(tmp_gz, "wb") as fh:
                            async for chunk in resp.aiter_bytes():
                                fh.write(chunk)
                    await asyncio.to_thread(self._gunzip, tmp_gz)
                    log.info("Laddade ner %s", url)
                    return
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                finally:
                    if os.path.exists(tmp_gz):
                        os.remove(tmp_gz)
        raise RuntimeError(str(last_error))

    def _gunzip(self, src: str) -> None:
        tmp = self.path + ".tmp"
        with gzip.open(src, "rb") as fin, open(tmp, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        os.replace(tmp, self.path)

    async def refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(24 * 3600)
            if self.custom or not self.cfg.geoip_auto_download:
                continue
            if time.time() - os.path.getmtime(self.path) > MAX_AGE_DAYS * 86400:
                try:
                    await self._download()
                    self._open()
                except Exception as exc:  # noqa: BLE001
                    log.warning("GeoIP-uppdatering misslyckades: %s", exc)

    def country(self, ip: str) -> str | None:
        """ISO 3166-1 alpha-2, t.ex. 'DE'. None för privata adresser eller okänt."""
        if ip in self.cache:
            self.cache.move_to_end(ip)
            return self.cache[ip]
        cc = None
        if self.reader and is_public(ip):
            try:
                rec = self.reader.get(ip)
                if isinstance(rec, dict):
                    cc = (rec.get("country") or rec.get("registered_country") or {}).get("iso_code")
            except ValueError:
                cc = None
        self.cache[ip] = cc
        if len(self.cache) > 50000:
            self.cache.popitem(last=False)
        return cc
