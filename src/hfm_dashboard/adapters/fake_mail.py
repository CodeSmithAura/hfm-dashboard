"""In-memory mail sender for tests and dry runs."""

from __future__ import annotations

from hfm_dashboard.domain.notice_text import OutgoingMail
from hfm_dashboard.ports.mail import DeliveryError


class RecordingMailSender:
    def __init__(self, fail: str | None = None) -> None:
        self.sent: list[OutgoingMail] = []
        self.fail = fail

    def send(self, mail: OutgoingMail) -> None:
        if self.fail:
            raise DeliveryError(self.fail)
        self.sent.append(mail)
