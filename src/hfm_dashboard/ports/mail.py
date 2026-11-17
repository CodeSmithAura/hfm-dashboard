"""Port: send one email."""

from __future__ import annotations

from typing import Protocol

from hfm_dashboard.domain.notice_text import OutgoingMail


class DeliveryError(Exception):
    """A notice could not be sent. The message is logged: never a password or full address list."""


class MailSender(Protocol):
    def send(self, mail: OutgoingMail) -> None:
        """Send or raise ``DeliveryError``."""
        ...
