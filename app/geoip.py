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
DBIP_ASN_URL = "https://download.db-ip.com/free/dbip-asn-lite-{y}-{m:02d}.mmdb.gz"
MAX_AGE_DAYS = 32


class GeoIP:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.custom = bool(cfg.geoip_path)
        self.path = cfg.geoip_path or os.path.join(cfg.data_dir, "dbip-country-lite.mmdb")
        self.reader: maxminddb.Reader | None = None
        self.cache: OrderedDict[str, str | None] = OrderedDict()
        self.status = {"ok": False, "error": None, "source": None, "updated": None}
        self.asn_path = cfg.asn_path or os.path.join(cfg.data_dir, "dbip-asn-lite.mmdb")
        self.asn_reader: maxminddb.Reader | None = None
        self.asn_cache: OrderedDict[str, str | None] = OrderedDict()

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
        await self.ensure_asn()

    async def ensure_asn(self) -> None:
        """Organisation (AS-nummer) per IP-adress. Valfritt: saknas den visas bara IP och värdnamn."""
        stale = not os.path.exists(self.asn_path) or time.time() - os.path.getmtime(self.asn_path) > MAX_AGE_DAYS * 86400
        if stale and self.cfg.geoip_auto_download and not self.cfg.asn_path:
            try:
                await self._download(DBIP_ASN_URL, self.asn_path)
            except Exception as exc:  # noqa: BLE001
                log.warning("Kunde inte ladda ner ASN-databas: %s", exc)
        if os.path.exists(self.asn_path):
            try:
                old, self.asn_reader = self.asn_reader, maxminddb.open_database(self.asn_path)
                self.asn_cache.clear()
                if old:
                    old.close()
            except Exception as exc:  # noqa: BLE001
                log.warning("Kunde inte läsa %s: %s", self.asn_path, exc)

    def org(self, ip: str) -> str | None:
        """Organisationen som äger adressen, t.ex. 'Google LLC'."""
        if ip in self.asn_cache:
            return self.asn_cache[ip]
        org = None
        if self.asn_reader and is_public(ip):
            try:
                rec = self.asn_reader.get(ip)
                if isinstance(rec, dict):
                    org = rec.get("autonomous_system_organization")
                    num = rec.get("autonomous_system_number")
                    if org and num:
                        org = f"{org} (AS{num})"
            except ValueError:
                org = None
        self.asn_cache[ip] = org
        if len(self.asn_cache) > 50000:
            self.asn_cache.popitem(last=False)
        return org

    async def _download(self, url_tpl: str = DBIP_URL, path: str | None = None) -> None:
        path = path or self.path
        today = dt.date.today()
        first = today.replace(day=1)
        prev = (first - dt.timedelta(days=1)).replace(day=1)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            for month in (first, prev):
                url = url_tpl.format(y=month.year, m=month.month)
                tmp_gz = path + ".download.gz"
                try:
                    async with client.stream("GET", url) as resp:
                        resp.raise_for_status()
                        with open(tmp_gz, "wb") as fh:
                            async for chunk in resp.aiter_bytes():
                                fh.write(chunk)
                    await asyncio.to_thread(self._gunzip, tmp_gz, path)
                    log.info("Laddade ner %s", url)
                    return
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                finally:
                    if os.path.exists(tmp_gz):
                        os.remove(tmp_gz)
        raise RuntimeError(str(last_error))

    def _gunzip(self, src: str, dest: str) -> None:
        tmp = dest + ".tmp"
        with gzip.open(src, "rb") as fin, open(tmp, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        os.replace(tmp, dest)

    async def refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(24 * 3600)
            if self.custom or not self.cfg.geoip_auto_download:
                continue
            if time.time() - os.path.getmtime(self.path) > MAX_AGE_DAYS * 86400:
                try:
                    await self._download()
                    self._open()
                    await self.ensure_asn()
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
