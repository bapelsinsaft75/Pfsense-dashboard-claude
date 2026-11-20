import os
import time

import pytest

from app.config import Config
from app.engine import Engine
from app.syslog import parse_filterlog, parse_suricata


def _fast_confirm(e):
    """Låt varje kontroll i testerna räknas som en ny kontroll, utan att vänta på intervallet."""
    orig = e.confirm
    clock = {"t": time.time()}

    def confirm(key, bad, now=None):
        clock["t"] += 60
        return orig(key, bad, clock["t"])
    e.confirm = confirm


class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.tls = True

    def login(self, user, token):
        if token != "rätt-token":
            import smtplib
            raise smtplib.SMTPAuthenticationError(535, b"nej")
        self.user = user

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("PFSENSE", "UNIFI", "SMTP", "MAIL", "ALERT", "RETENTION")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PFSENSE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("LAN_SUBNETS", "192.168.1.0/24")
    monkeypatch.setenv("SMTP_USER", "larm@exempel.se")
    monkeypatch.setenv("SMTP_TOKEN", "rätt-token")
    monkeypatch.setenv("MAIL_TO", "jag@exempel.se")
    import app.mailer as mailer
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    FakeSMTP.sent = []
    e = Engine(Config())
    e.settings.update({"confirm_count": 2, "confirm_interval": 10})
    _fast_confirm(e)
    # skicka e-post synkront i testerna
    e.mailer.send_background = lambda s, b, to=None: e.mailer.send(s, b, to)
    e.geo.country = lambda ip: {"45.148.10.5": "NL", "47.88.0.12": "CN"}.get(ip)
    return e


def test_defaults_and_settings(engine, tmp_path):
    assert engine.settings.get("retention_days") == 14
    engine.settings.update({"retention_days": 30, "email_warnings": True})
    assert (tmp_path / "settings.json").exists()
    from app.settings import Settings
    again = Settings(Config())
    assert again.get("retention_days") == 30 and again.get("email_warnings") is True
    with pytest.raises(ValueError):
        engine.settings.update({"retention_days": 0})
    with pytest.raises(ValueError):
        engine.settings.update({"okänd": 1})


def test_vpn_down_mail_and_resolve(engine):
    rows = [{"name": "WAN_DHCP", "status": "online", "loss": "0.0%"}, {"name": "PROTONVPN_WG", "status": "down", "loss": "100%"}]
    engine.update_gateways(rows)
    assert not engine.alerts.banner()  # en enstaka mätning räcker inte
    engine.update_gateways(rows)
    banner = engine.alerts.banner()
    assert len(banner) == 1 and banner[0]["level"] == "critical" and "VPN-anslutningen PROTONVPN_WG" in banner[0]["title"]
    assert len(FakeSMTP.sent) == 1 and "PROTONVPN_WG" in FakeSMTP.sent[0]["Subject"]
    engine.update_gateways(rows)  # samma larm igen: inget nytt mejl
    assert len(FakeSMTP.sent) == 1
    engine.update_gateways([{"name": "PROTONVPN_WG", "status": "online"}])
    assert len(FakeSMTP.sent) == 2 and FakeSMTP.sent[1]["Subject"].endswith("Löst: VPN-anslutningen PROTONVPN_WG är nere")
    banner = engine.alerts.banner()
    assert banner and banner[0]["resolved"]  # löst larm syns tills man stänger det
    engine.alerts.ack(banner[0]["id"])
    assert not engine.alerts.banner()


def test_openvpn(engine):
    engine.update_openvpn([{"name": "Proton", "status": "down"}])
    engine.update_openvpn([{"name": "Proton", "status": "down"}])
    assert engine.alerts.is_active("ovpn:Proton")
    engine.update_openvpn([{"name": "Proton", "status": "up"}])
    assert not engine.alerts.is_active("ovpn:Proton")
    # tillståndet från OpenVPN går före: en klient som återansluter räknas som nere
    engine.update_openvpn([{"name": "Proton", "status": "up", "state": "RECONNECTING"}])
    engine.update_openvpn([{"name": "Proton", "status": "up", "state": "RECONNECTING"}])
    assert engine.alerts.is_active("ovpn:Proton")
    engine.update_openvpn([{"name": "Proton", "status": "up", "state": "CONNECTED", "connect_time": "2026-11-19 12:00"}])
    assert not engine.alerts.is_active("ovpn:Proton")


def test_suricata_and_pfblocker(engine):
    line = ("<36>Nov 19 12:00:01 pfSense suricata[1]: [1:2001219:20] ET SCAN Potential SSH Scan "
            "[Classification: Attempted Information Leak] [Priority: 2] {TCP} 45.148.10.5:41860 -> 192.168.1.10:22")
    engine.on_syslog(line)
    assert not engine.alerts.is_active("suricata:2001219")  # prioritet 2 larmar inte som standard
    engine.on_syslog(line.replace("[Priority: 2]", "[Priority: 1]"))
    assert engine.alerts.is_active("suricata:2001219")
    assert len(FakeSMTP.sent) == 1
    engine.settings.update({"pfblocker_threshold": 3})
    fl = ("<134>1 2026-11-19T12:00:00+01:00 pfSense.home filterlog 123 - - 5,,,1770008891,igb1,match,block,in,4,0x0,,64,"
          "1,0,DF,6,tcp,60,192.168.1.62,47.88.0.12,51515,443,0,S,1,,64240,,mss")
    for _ in range(3):
        engine.on_syslog(fl)
    assert engine.alerts.is_active("pfb:ip-192.168.1.62")
    assert len(FakeSMTP.sent) == 1  # varningar mejlas inte som standard
    engine.history.flush()
    sec = engine.security(0)
    assert sec["totals"] == {"suricata": 2, "pfblockerng": 3}
    assert sec["pfb_clients"][0]["count"] == 3
    # händelselarm avslutas efter en timmes tystnad
    engine.alerts.expire(("suricata", "pfblocker"), 3600, now=time.time() + 4000)
    assert not engine.alerts.is_active("suricata:2001219")


def test_data_anomaly(engine):
    now = time.time()
    rows = []
    for day in range(1, 10):
        rows.append((int(now - day * 86400 - 3600), 300, "aa:aa:aa:aa:aa:01", "DE", "out", 0, int(2e9), 1))
        rows.append((int(now - day * 86400 - 3600), 300, "aa:aa:aa:aa:aa:02", "DE", "out", 0, int(5e9), 1))
    rows.append((int(now - 3600), 300, "aa:aa:aa:aa:aa:01", "DE", "out", 0, int(9e9), 1))  # 4,5 gånger normalt
    rows.append((int(now - 3600), 300, "aa:aa:aa:aa:aa:02", "DE", "out", 0, int(6e9), 1))  # normalt
    engine.history.insert_raw(rows)
    engine.clients["aa:aa:aa:aa:aa:01"] = {"mac": "aa:aa:aa:aa:aa:01", "descr": "PS5"}
    engine.check_data_anomalies(now)
    active = engine.alerts.banner()
    assert [a["title"] for a in active] == ["PS5 använder ovanligt mycket data"]
    assert "9.0 GB" in active[0]["message"] and "2.0 GB" in active[0]["message"]


def test_source_down_and_retention(engine):
    engine.mark("pfsense", False, "timeout")
    assert not engine.alerts.is_active("source:pfsense")  # första misslyckade kontrollen räcker inte
    engine.mark("pfsense", False, "timeout")
    assert engine.alerts.is_active("source:pfsense")
    engine.mark("pfsense", True)
    assert not engine.alerts.is_active("source:pfsense")

    now = time.time()
    engine.history.insert_raw([(int(now - 20 * 86400), 3600, "x", "DE", "out", 1, 1, 1),
                               (int(now - 3 * 86400), 3600, "x", "DE", "out", 1, 1, 1)])
    engine.history.compact(engine.settings.get("retention_days"), now)
    assert engine.history._q("SELECT count(*) FROM traffic")[0][0] == 1


def test_wrong_token(engine):
    engine.mailer.token = "fel"
    with pytest.raises(RuntimeError, match="SMTP-inloggningen nekades"):
        engine.mailer.send("x", "y")


def test_new_device(engine):
    engine.update_clients([], [{"ip_address": "192.168.1.5", "mac_address": "aa:bb:cc:dd:ee:01", "expires": "in 100"}])
    assert not engine.alerts.banner()  # första gången lär den sig vilka som finns
    engine.update_clients([], [{"ip_address": "192.168.1.5", "mac_address": "aa:bb:cc:dd:ee:01"},
                               {"ip_address": "192.168.1.6", "mac_address": "aa:bb:cc:dd:ee:02", "hostname": "okand"}])
    titles = [a["title"] for a in engine.alerts.banner()]
    assert titles == ["Ny enhet i nätverket: okand"]


def test_syslog_parsers():
    assert parse_suricata("pfSense suricata[1]: something else") is None
    ev = parse_filterlog("filterlog[1]: 9,,,1000000103,igb0,match,pass,out,4,0x0,,64,0,0,DF,17,udp,76,203.0.113.7,1.1.1.1,123,123,56")
    assert ev.action == "pass" and ev.proto == "udp" and ev.dport == 123


def test_netflow_diagnostics(engine):
    engine.tick()
    assert "Inga NetFlow-paket har kommit fram" in engine.status["netflow"]["error"]
    from tests.test_parsers import _v9_packet
    engine.nf_parser.parse(_v9_packet(template=False), "192.168.2.1")
    engine.tick()
    assert "väntar på mallen" in engine.status["netflow"]["error"]
    assert engine.nf_parser.stats()["received"] == 1


def test_slack(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("PFSENSE", "UNIFI", "SMTP", "MAIL", "ALERT", "RETENTION", "SLACK", "PUBLIC")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PFSENSE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T000/B000/XXXX")
    monkeypatch.setenv("PUBLIC_URL", "http://192.168.2.30:8080/")
    posts = []

    class Resp:
        def __init__(self, code=200, text="ok"):
            self.status_code, self.text = code, text

    import app.slack as slackmod
    monkeypatch.setattr(slackmod.httpx, "post", lambda url, json, timeout: posts.append((url, json)) or Resp())
    e = Engine(Config())
    e.settings.update({"confirm_count": 2, "confirm_interval": 10})
    _fast_confirm(e)
    e.slack.send_background = lambda *a: e.slack.send(*a)
    # kritiskt: skickas, och löst skickas också
    e.update_openvpn([{"name": "Littorin VPN UDP4", "status": "down"}])
    e.update_openvpn([{"name": "Littorin VPN UDP4", "status": "down"}])
    assert len(posts) == 1
    url, body = posts[0]
    assert url.startswith("https://hooks.slack.com/") and body["attachments"][0]["color"] == "#e5484d"
    assert "Littorin VPN UDP4" in body["text"] and "http://192.168.2.30:8080|" in str(body)
    e.update_openvpn([{"name": "Littorin VPN UDP4", "status": "up", "state": "CONNECTED"}])
    assert len(posts) == 2 and posts[1][1]["text"].startswith("Löst:") and posts[1][1]["attachments"][0]["color"] == "#3ddc84"
    # varningar skickas som standard men kan stängas av
    e.alerts.raise_("x1", "warning", "test", "Varning 1", "a < b & c")
    assert len(posts) == 3 and "a &lt; b &amp; c" in str(posts[2][1])
    e.settings.update({"slack_warnings": False})
    e.alerts.raise_("x2", "warning", "test", "Varning 2", "")
    assert len(posts) == 3
    # fel från Slack syns i statusen
    monkeypatch.setattr(slackmod.httpx, "post", lambda url, json, timeout: Resp(404, "no_service"))
    with pytest.raises(RuntimeError, match="Webhooken finns inte"):
        e.slack.send("info", "t", "m", "nu")
    assert e.status["slack"]["error"]



def test_confirm_counts_once_per_interval(engine):
    e = engine
    e.confirm = Engine.confirm.__get__(e)  # den riktiga, utan testgenvägen
    e.settings.update({"confirm_interval": 60, "confirm_count": 3})
    t = 1_000_000.0
    assert not e.confirm("k", True, t)
    assert not e.confirm("k", True, t + 10)   # för tätt: räknas inte
    assert not e.confirm("k", True, t + 60)   # andra
    assert e.confirm("k", True, t + 120)      # tredje: larm
    assert "3 kontroller under 2 min" in e.confirm_note("k")
    assert not e.confirm("k", False, t + 130)  # lyckad kontroll nollställer
    assert not e.confirm("k", True, t + 140)


def test_quiet_hours_and_digest(engine, monkeypatch):
    import datetime as dt
    from app.settings import TZ
    s = engine.settings
    s.update({"quiet_enabled": True, "quiet_start": "22:00", "quiet_end": "07:00", "quiet_days": [4]})  # fredag
    fri_late = dt.datetime(2026, 11, 20, 23, 30, tzinfo=TZ)   # fredag
    sat_early = dt.datetime(2026, 11, 21, 6, 59, tzinfo=TZ)   # natten mot lördag hör till fredag
    sat_late = dt.datetime(2026, 11, 21, 23, 0, tzinfo=TZ)    # lördag: inte vald
    assert s.quiet_now(fri_late) == (True, "07:00")
    assert s.quiet_now(sat_early)[0] and not s.quiet_now(sat_late)[0]
    with pytest.raises(ValueError):
        s.update({"quiet_start": "25:00"})

    quiet = {"on": True}
    monkeypatch.setattr(s, "quiet_now", lambda now=None: (quiet["on"], "07:00" if quiet["on"] else None))
    engine.alerts.raise_("gw:X", "critical", "gateway_down", "Gatewayen X är nere", "nere")
    assert FakeSMTP.sent == [] and engine.alerts.banner()  # tyst: inget mejl, men syns i bannern
    s.update({"quiet_allow_critical": True})
    engine.alerts.raise_("gw:Y", "critical", "gateway_down", "Gatewayen Y är nere", "nere")
    assert len(FakeSMTP.sent) == 1  # kritiska släpps igenom när det är valt
    s.update({"quiet_allow_critical": False})
    assert not engine.alerts.flush_quiet_digest()  # fortfarande tyst
    quiet["on"] = False
    assert engine.alerts.flush_quiet_digest()
    assert FakeSMTP.sent[-1]["Subject"].endswith("Sammanfattning av tysta tiden: 1 larm")
    assert "Gatewayen X är nere" in FakeSMTP.sent[-1].get_content()


def test_disabled_kind_and_watch(engine):
    from app.unraid import Unraid
    engine.settings.update({"alert_kinds": {"new_device": False}, "channels": {"new_device": {"email": False, "slack": False}}})
    engine.settings.update({"recipients": [{"email": "vm@exempel.se", "kinds": ["unraid_vm"]},
                                           {"email": "jag@exempel.se", "kinds": ["unraid_watch"]}]})
    assert engine.alerts.raise_("n", "warning", "new_device", "Ny", "x") is None
    u = Unraid("LillaNasen", "http://u.test", "k", False)
    u.status.update(ok=True, level="ok", name="LillaNasen", detail="ok")
    u.data = {"containers": [{"name": "plex", "state": "running"}, {"name": "qbit", "state": "bad"}],
              "vms": [{"name": "Win", "running": False, "state": "shutoff"}]}
    engine.settings.set_watch("LillaNasen", "containers", "qbit", True)
    engine.settings.set_watch("LillaNasen", "vms", "Win", True)
    engine.check_unraid(u)
    engine.check_unraid(u)
    titles = sorted(a["title"] for a in engine.alerts.banner())
    assert titles == ["Containern qbit på LillaNasen har stannat", "Den virtuella maskinen Win på LillaNasen har stannat"]
    by_subject = {m["Subject"].split("] ", 1)[-1]: m["To"] for m in FakeSMTP.sent}
    assert by_subject["Den virtuella maskinen Win på LillaNasen har stannat"] == "vm@exempel.se"
    assert by_subject["Containern qbit på LillaNasen har stannat"] == "jag@exempel.se"
    engine.settings.set_watch("LillaNasen", "vms", "Win", False)
    engine.check_unraid(u)
    assert not engine.alerts.is_active("watch:LillaNasen:vms:Win")
    u.data["containers"][1]["state"] = "running"
    engine.check_unraid(u)
    assert not engine.alerts.is_active("watch:LillaNasen:containers:qbit")


def test_recipients_per_kind(engine):
    s = engine.settings
    s.update({"recipients": [
        {"email": "jag@exempel.se", "name": "Peter", "kinds": ["vpn_down", "data_anomaly"]},
        {"email": "alex@exempel.se", "kinds": ["vpn_down", "unraid_watch"]},
    ]})
    engine.alerts.raise_("ovpn:A", "critical", "vpn_down", "VPN-anslutningen A är nere", "nere")
    assert FakeSMTP.sent[-1]["To"] == "jag@exempel.se, alex@exempel.se"
    engine.alerts.raise_("data:x", "warning", "data_anomaly", "PS5 använder ovanligt mycket data", "mycket")
    assert FakeSMTP.sent[-1]["To"] == "jag@exempel.se"  # varning går till den som valt typen
    n = len(FakeSMTP.sent)
    engine.alerts.raise_("gw:W", "critical", "gateway_down", "Gatewayen W är nere", "nere")
    assert len(FakeSMTP.sent) == n  # ingen har valt gateway_down
    engine.alerts.resolve("ovpn:A", "uppe")
    assert FakeSMTP.sent[-1]["To"] == "jag@exempel.se, alex@exempel.se" and "Löst" in FakeSMTP.sent[-1]["Subject"]
    with pytest.raises(ValueError, match="Ogiltig e-postadress"):
        s.update({"recipients": [{"email": "inte-en-adress", "kinds": []}]})
    with pytest.raises(ValueError, match="finns redan"):
        s.update({"recipients": [{"email": "a@b.se", "kinds": []}, {"email": "A@b.se", "kinds": []}]})


def test_digest_per_recipient(engine, monkeypatch):
    s = engine.settings
    s.update({"recipients": [{"email": "jag@exempel.se", "kinds": ["vpn_down", "new_device"]},
                             {"email": "alex@exempel.se", "kinds": ["vpn_down"]}]})
    quiet = {"on": True}
    monkeypatch.setattr(s, "quiet_now", lambda now=None: (quiet["on"], "07:00" if quiet["on"] else None))
    engine.alerts.raise_("ovpn:A", "critical", "vpn_down", "VPN-anslutningen A är nere", "nere")
    engine.alerts.raise_("new:x", "warning", "new_device", "Ny enhet i nätverket: X", "x")
    assert FakeSMTP.sent == []
    quiet["on"] = False
    engine.alerts.flush_quiet_digest()
    by_to = {m["To"]: m.get_content() for m in FakeSMTP.sent}
    assert "Ny enhet" in by_to["jag@exempel.se"] and "VPN-anslutningen A" in by_to["jag@exempel.se"]
    assert "Ny enhet" not in by_to["alex@exempel.se"] and "VPN-anslutningen A" in by_to["alex@exempel.se"]


def test_recipients_per_server(engine):
    engine.settings.update({"recipients": [
        {"email": "peter@exempel.se", "kinds": ["plex_down", "unraid_vm"], "servers": ["Peters Plex", "LillaNasen"]},
        {"email": "alex@exempel.se", "kinds": ["plex_down", "unraid_vm"], "servers": ["Alex Plex", "Tower"]},
        {"email": "alla@exempel.se", "kinds": ["plex_down"], "servers": None},
    ]})
    engine.alerts.raise_("plex:a", "critical", "plex_down", "Alex Plex svarar inte", "nere", source="Alex Plex")
    assert FakeSMTP.sent[-1]["To"] == "alex@exempel.se, alla@exempel.se"
    engine.alerts.raise_("watch:LillaNasen:vms:Win", "critical", "unraid_vm", "Den virtuella maskinen Win på LillaNasen har stannat",
                         "x", source="LillaNasen")
    assert FakeSMTP.sent[-1]["To"] == "peter@exempel.se"
    engine.alerts.resolve("plex:a", "uppe")
    assert FakeSMTP.sent[-1]["To"] == "alex@exempel.se, alla@exempel.se" and "Löst" in FakeSMTP.sent[-1]["Subject"]


def test_top_list(engine):
    now = 1_800_000_000.0
    h = engine.history
    engine.clients["aa:00:00:00:00:01"] = {"mac": "aa:00:00:00:00:01", "ip": "192.168.1.41", "descr": "PS5"}
    engine.clients["aa:00:00:00:00:02"] = {"mac": "aa:00:00:00:00:02", "ip": "192.168.1.10", "descr": "Unraid"}
    # PS5: 30 MB per 30 s de senaste 5 minuterna (= 8 Mbit/s), med en topp
    for i in range(10):
        h.add_recent("aa:00:00:00:00:01", "DE", 0, 30_000_000 if i != 3 else 60_000_000, now - 290 + i * 30)
    # Unraid: lite trafik för 10 minuter sedan, inget de senaste 5
    h.add_recent("aa:00:00:00:00:02", "US", 50_000_000, 0, now - 600)
    r5 = h.top_recent(300, now)
    assert set(r5) == {"aa:00:00:00:00:01"} and r5["aa:00:00:00:00:01"]["down"] == 330_000_000
    assert r5["aa:00:00:00:00:01"]["peak_bps"] == 60_000_000 * 8 / 30 and r5["aa:00:00:00:00:01"]["country"] == "DE"
    r15 = h.top_recent(900, now)
    assert set(r15) == {"aa:00:00:00:00:01", "aa:00:00:00:00:02"}
    import app.engine as eng
    import time as _t
    real = _t.time
    try:
        eng.time.time = lambda: now
        t = engine.top("5m")
    finally:
        eng.time.time = real
    assert t["items"][0]["name"] == "PS5" and round(t["items"][0]["avg_bps"] / 1e6, 1) == 8.8
    h.prune_recent(now + 4000)
    assert not h.recent


def test_top_day(engine):
    now = 1_800_000_000
    engine.history.insert_raw([(now - 3600, 300, "x", "SE", "out", 1000, 9000, 1), (now - 1800, 300, "x", "DE", "out", 0, 30000, 1)])
    d = engine.history.top_day(now - 86400, now)
    assert d["x"]["down"] == 39000 and d["x"]["country"] == "DE" and d["x"]["peak_bps"] == 30000 * 8 / 300
    assert len(d["x"]["spark"]) == 24


def test_scopes_internet_internal_all(engine):
    now = 1_800_000_000.0
    h = engine.history
    tv, plex, phone = "aa:00:00:00:00:40", "aa:00:00:00:00:10", "aa:00:00:00:00:34"
    for c, ip in ((tv, "192.168.1.40"), (plex, "192.168.1.10"), (phone, "192.168.1.34")):
        engine.clients[c] = {"mac": c, "ip": ip, "descr": c[-2:]}
    # internet: TV laddar ner 10 MB, mobilen 4 MB (mobilen finns inte i UniFi)
    h.add_recent(tv, "NL", 0, 10_000_000, now - 100)
    h.add_recent(phone, "SE", 0, 4_000_000, now - 100)
    # UniFi: TV har totalt 60 MB ned (50 MB internt från Plex), Plex-servern 50 MB upp internt
    h.add_total(tv, 100_000, 60_000_000, now - 100)
    h.add_total(plex, 50_000_000, 200_000, now - 100)
    inet = h.top_scoped("internet", 300, now)
    lan = h.top_scoped("internal", 300, now)
    allt = h.top_scoped("all", 300, now)
    assert set(inet) == {tv, phone}
    assert lan[tv]["down"] == 50_000_000 and lan[plex]["up"] == 50_000_000 and phone not in lan
    assert allt[tv]["down"] == 60_000_000 and allt[tv]["internet"] == 10_000_000
    assert allt[phone]["down"] == 4_000_000  # klient utanför UniFi räknas ändå med sin internettrafik
    ser = h.series_scoped("all", 300, 30, now)
    assert sum(p["internet"] for p in ser) == 14_000_000
    assert sum(p["internal"] for p in ser) == 100_000 + 50_000_000 + 50_000_000 + 200_000
    h.flush()
    d = h.top_scoped("internal", 86400, now + 60)
    assert d[plex]["up"] == 50_000_000


def test_unifi_counters_and_orientation(engine):
    t = 1_800_000_000.0
    mac = "aa:00:00:00:00:21"
    engine.clients[mac] = {"mac": mac, "ip": "192.168.1.21", "descr": "MacBook"}
    # pfSense ser 300 MB ned mot internet för klienten
    engine.history.add_recent(mac, "IE", 5_000_000, 300_000_000, t)
    # UniFi: rx_bytes ökar mest, alltså är rx nedladdning ur klientens synvinkel
    engine._unifi_traffic({mac: {"tx_bytes": 1_000, "rx_bytes": 1_000}}, t)
    engine._unifi_traffic({mac: {"tx_bytes": 20_000_000, "rx_bytes": 400_000_000}}, t + 30)
    assert engine.unifi_tx_is_down is False
    # nollställd räknare (klienten återanslöt) ger ingen negativ trafik
    engine._unifi_traffic({mac: {"tx_bytes": 5, "rx_bytes": 5}}, t + 60)
    total = engine.history.top_scoped("all", 3600, t + 90)[mac]
    assert total["up"] >= 5_000_000 and total["down"] >= 300_000_000


def test_channels_per_kind(engine):
    sent_slack = []
    engine.alerts.slack = type("S", (), {"enabled": True, "dashboard_url": "", "send_background": lambda self, *a: sent_slack.append(a)})()
    engine.settings.update({"recipients": [{"email": "jag@exempel.se", "kinds": ["vpn_down", "new_device"]}],
                            "channels": {"vpn_down": {"email": False, "slack": True},
                                         "new_device": {"email": True, "slack": False}}})
    engine.alerts.raise_("ovpn:A", "critical", "vpn_down", "VPN-anslutningen A är nere", "nere")
    assert FakeSMTP.sent == [] and len(sent_slack) == 1  # bara Slack
    engine.alerts.raise_("new:x", "warning", "new_device", "Ny enhet i nätverket: X", "x")
    assert len(FakeSMTP.sent) == 1 and len(sent_slack) == 1  # bara e-post
    # skickas men visas inte i bannern
    engine.settings.update({"alert_kinds": {"gateway_down": False}, "channels": {"gateway_down": {"email": False, "slack": True}}})
    aid = engine.alerts.raise_("gw:W", "critical", "gateway_down", "Gatewayen W är nere", "nere")
    assert aid and len(sent_slack) == 2 and all(a["id"] != aid for a in engine.alerts.banner())
    assert any(e["id"] == aid for e in engine.alerts.recent())
    # alla tre av: avstängd
    engine.settings.update({"alert_kinds": {"plex_down": False}, "channels": {"plex_down": {"email": False, "slack": False}}})
    assert engine.alerts.raise_("plex:p", "critical", "plex_down", "Plex svarar inte", "x") is None


def test_channels_follow_old_off_kinds(engine):
    ch = engine.settings.get("channels")
    assert ch["unraid_down"] == {"email": False, "slack": False}  # var avstängd från början
    assert ch["vpn_down"] == {"email": True, "slack": True}
    assert engine.alerts.raise_("unraid:x", "critical", "unraid_down", "Tower svarar inte", "x") is None


def test_routes_per_server_and_container(engine):
    sent_slack = []
    engine.alerts.slack = type("S", (), {"enabled": True, "dashboard_url": "", "send_background": lambda self, *a: sent_slack.append(a)})()
    engine.settings.update({
        "recipients": [{"email": "peter@exempel.se", "kinds": []}, {"email": "ida@exempel.se", "kinds": []}],
        "routes": {
            "unraid_watch": {"banner": True, "slack": False, "emails": ["peter@exempel.se"]},
            "unraid_watch|Tower|plex": {"banner": True, "slack": True, "emails": ["peter@exempel.se"]},
            "unraid_vm|LillaNasen": {"banner": False, "slack": False, "emails": ["ida@exempel.se"]},
            "unraid_vm|LillaNasen|Windows11": {"banner": True, "slack": False, "emails": ["ida@exempel.se"]},
            "vpn_down": {"banner": False, "slack": False, "emails": []},
        }})
    s = engine.settings
    # mest specifika raden gäller, annars föräldern
    assert s.route("unraid_watch", "Tower|plex")["slack"] is True
    assert s.route("unraid_watch", "Tower|immich") == {"banner": True, "slack": False, "emails": ["peter@exempel.se"]}
    assert s.route("unraid_vm", "LillaNasen|Ubuntu") == {"banner": False, "slack": False, "emails": ["ida@exempel.se"]}
    # en typ utan rader följer de äldre inställningarna
    assert s.route("gateway_down")["banner"] is True
    # larmen går till rätt mottagare
    engine.alerts.raise_("watch:Tower:containers:plex", "critical", "unraid_watch", "plex har stannat", "x", source="Tower|plex")
    assert len(sent_slack) == 1 and FakeSMTP.sent[-1]["To"] == "peter@exempel.se"
    engine.alerts.raise_("watch:LillaNasen:vms:Windows11", "critical", "unraid_vm", "Windows11 har stannat", "x",
                         source="LillaNasen|Windows11")
    assert len(sent_slack) == 1 and FakeSMTP.sent[-1]["To"] == "ida@exempel.se"
    # alla kanaler av: avstängd
    assert engine.alerts.raise_("ovpn:A", "critical", "vpn_down", "VPN nere", "x") is None


def test_alert_matrix_rows(engine):
    rows = {r["key"]: r for r in engine.alert_matrix()}
    assert rows["vpn_down"]["group"] == "Nätverk" and rows["vpn_down"]["critical"]
    assert "plex_down" not in rows  # ingen Plex konfigurerad
    assert rows["vpn_down"]["route"]["emails"] == ["jag@exempel.se"]  # MAIL_TO som tidigare
