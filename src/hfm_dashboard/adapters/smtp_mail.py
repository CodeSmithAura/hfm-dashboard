"""Send notice emails through the SMTP relay (A13). Plain text, UTF-8.

The relay, port, STARTTLS and From address come from environments.yaml (``notices``); the timeout
from rules.yaml. STARTTLS verifies the relay certificate against the Windows certificate store.
No SMTP login: an internal relay is assumed (A13). Error texts are fixed per failure type.
"""

from __future__ import annotations

import smtplib
import socket
import ssl
from collections.abc import Callable
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import truststore

from hfm_dashboard.domain.notice_text import OutgoingMail
from hfm_dashboard.ports.mail import DeliveryError


def build_message(mail: OutgoingMail, from_address: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = from_address
    msg["To"] = ", ".join(mail.to)
    if mail.cc:
        msg["Cc"] = ", ".join(mail.cc)
    msg["Subject"] = mail.subject
    msg["Date"] = formatdate(usegmt=True)
    msg["Message-ID"] = make_msgid(domain=from_address.rsplit("@", 1)[-1])
    msg["Auto-Submitted"] = "auto-generated"          # RFC 3834: no auto-replies
    msg.set_content(mail.body)
    return msg


class SmtpMailSender:
    def __init__(
        self,
        relay: str,
        port: int,
        from_address: str,
        timeout_seconds: float,
        starttls: bool = True,
        smtp_factory: Callable[..., smtplib.SMTP] = smtplib.SMTP,
    ) -> None:
        self._relay, self._port, self._from = relay, port, from_address
        self._timeout, self._starttls = timeout_seconds, starttls
        self._smtp_factory = smtp_factory

    def send(self, mail: OutgoingMail) -> None:
        if not mail.to:
            raise DeliveryError("no recipients")
        msg = build_message(mail, self._from)
        try:
            with self._smtp_factory(self._relay, self._port, timeout=self._timeout) as smtp:
                if self._starttls:
                    smtp.starttls(context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
                refused = smtp.send_message(msg)
        except smtplib.SMTPRecipientsRefused as exc:
            raise DeliveryError("relay refused every recipient") from exc
        except smtplib.SMTPNotSupportedError as exc:
            raise DeliveryError("relay does not support STARTTLS (set starttls: false?)") from exc
        except (smtplib.SMTPException, ssl.SSLError) as exc:
            raise DeliveryError(f"SMTP error ({type(exc).__name__})") from exc
        except (OSError, socket.timeout) as exc:
            raise DeliveryError(f"SMTP relay not reachable ({type(exc).__name__})") from exc
        if refused:
            raise DeliveryError(f"relay refused {len(refused)} recipient(s)")
