import os
import time

import pytest

from app.config import Config
from app.engine import Engine
from app.syslog import parse_filterlog, parse_suricata


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
    # skicka e-post synkront i testerna
    e.mailer.send_background = lambda s, b: e.mailer.send(s, b)
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
    engine.check_sources(time.time() + 10)
    assert not engine.alerts.is_active("source:pfsense")
    engine.check_sources(time.time() + 400)
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
