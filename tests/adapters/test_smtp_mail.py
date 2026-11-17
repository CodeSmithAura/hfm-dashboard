"""SMTP sender with a fake smtplib.SMTP (no network)."""

from __future__ import annotations

import smtplib

import pytest

from hfm_dashboard.adapters.fake_mail import RecordingMailSender
from hfm_dashboard.adapters.smtp_mail import SmtpMailSender, build_message
from hfm_dashboard.domain.notice_text import OutgoingMail
from hfm_dashboard.ports.mail import DeliveryError

MAIL = OutgoingMail(("owner@example.test",), ("manager@example.test",), "[HFM PROD] DOWN: X", "body\n")


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout=None, *, raise_on=None, refused=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.raise_on, self.refused = raise_on or {}, refused or {}
        self.tls = False
        self.messages = []
        FakeSMTP.instances.append(self)
        if "connect" in self.raise_on:
            raise self.raise_on["connect"]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        if "starttls" in self.raise_on:
            raise self.raise_on["starttls"]
        self.tls = context is not None

    def send_message(self, msg):
        if "send" in self.raise_on:
            raise self.raise_on["send"]
        self.messages.append(msg)
        return self.refused


def sender(starttls=True, **kw):
    def factory(host, port, timeout=None):
        return FakeSMTP(host, port, timeout, **kw)
    return SmtpMailSender("relay.example.test", 1234, "hfm-monitor@example.test", 20, starttls=starttls,
                          smtp_factory=factory)


class TestSend:
    def test_sends_with_starttls(self):
        FakeSMTP.instances.clear()
        sender().send(MAIL)
        (s,) = FakeSMTP.instances
        assert (s.host, s.port, s.timeout, s.tls) == ("relay.example.test", 1234, 20, True)
        (msg,) = s.messages
        assert msg["To"] == "owner@example.test" and msg["Cc"] == "manager@example.test"
        assert msg["From"] == "hfm-monitor@example.test" and msg["Subject"] == "[HFM PROD] DOWN: X"

    def test_without_starttls(self):
        FakeSMTP.instances.clear()
        sender(starttls=False).send(MAIL)
        assert FakeSMTP.instances[0].tls is False

    @pytest.mark.parametrize(("raise_on", "expected"), [
        ({"connect": ConnectionRefusedError()}, "not reachable"),
        ({"connect": TimeoutError()}, "not reachable"),
        ({"starttls": smtplib.SMTPNotSupportedError()}, "does not support STARTTLS"),
        ({"send": smtplib.SMTPRecipientsRefused({"owner@example.test": (550, b"no")})}, "refused every recipient"),
        ({"send": smtplib.SMTPServerDisconnected()}, "SMTP error (SMTPServerDisconnected)"),
    ])
    def test_errors_fixed_text(self, raise_on, expected):
        with pytest.raises(DeliveryError) as info:
            sender(raise_on=raise_on).send(MAIL)
        assert expected in str(info.value) and "example.test" not in str(info.value)

    def test_some_recipients_refused(self):
        with pytest.raises(DeliveryError, match="refused 1 recipient"):
            sender(refused={"manager@example.test": (550, b"no")}).send(MAIL)

    def test_no_recipients(self):
        with pytest.raises(DeliveryError, match="no recipients"):
            sender().send(OutgoingMail((), (), "s", "b"))


class TestMessage:
    def test_headers(self):
        msg = build_message(MAIL, "hfm-monitor@example.test")
        assert msg["Auto-Submitted"] == "auto-generated"
        assert msg["Message-ID"].endswith("@example.test>")
        assert msg["Date"].endswith("+0000")          # UTC
        assert msg.get_content() == "body\n"

    def test_no_cc_header_when_empty(self):
        assert build_message(OutgoingMail(("a@example.test",), (), "s", "b"), "f@example.test")["Cc"] is None


def test_recording_sender():
    r = RecordingMailSender()
    r.send(MAIL)
    assert r.sent == [MAIL]
    with pytest.raises(DeliveryError, match="down"):
        RecordingMailSender(fail="down").send(MAIL)


def test_real_smtplib_against_local_server():
    """Real smtplib, real socket: a minimal SMTP server on localhost records one message."""
    import socket
    import threading

    received: dict[str, object] = {"rcpt": []}
    srv = socket.socket()
    srv.bind(("localhost", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        conn, _ = srv.accept()
        f = conn.makefile("rwb")

        def reply(line):
            f.write(line.encode() + b"\r\n")
            f.flush()

        reply("220 test")
        while True:
            line = f.readline().decode().strip()
            cmd = line.upper()
            if cmd.startswith(("EHLO", "HELO")):
                reply("250 test")
            elif cmd.startswith("MAIL FROM"):
                received["from"] = line
                reply("250 ok")
            elif cmd.startswith("RCPT TO"):
                received["rcpt"].append(line)
                reply("250 ok")
            elif cmd == "DATA":
                reply("354 go")
                data = []
                while (row := f.readline()) not in (b".\r\n", b""):
                    data.append(row)
                received["data"] = b"".join(data).decode()
                reply("250 queued")
            elif cmd == "QUIT":
                reply("221 bye")
                break
            else:
                reply("250 ok")
        conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    SmtpMailSender("localhost", port, "hfm-monitor@example.test", 5, starttls=False).send(MAIL)
    t.join(5)
    srv.close()
    assert "hfm-monitor@example.test" in received["from"]
    assert len(received["rcpt"]) == 2                        # To and Cc
    assert "Subject: [HFM PROD] DOWN: X" in received["data"]
