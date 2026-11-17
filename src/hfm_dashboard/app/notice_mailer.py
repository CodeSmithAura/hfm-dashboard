"""Deliver notices as emails: compose each one, send it, report what could not be sent.

Used as the NoticeTracker's ``deliver`` callable. Every notice is attempted; if any fails, a
``DeliveryError`` summarising the failures is raised afterwards so the tracker logs it.
"""

from __future__ import annotations

import logging

from hfm_dashboard.domain.environment import Environment
from hfm_dashboard.domain.notice_text import compose
from hfm_dashboard.domain.notices import Notice
from hfm_dashboard.domain.owners import OwnersConfig
from hfm_dashboard.domain.rules import Rules
from hfm_dashboard.ports.mail import DeliveryError, MailSender

log = logging.getLogger(__name__)


class NoticeMailer:
    def __init__(self, env: Environment, rules: Rules, owners: OwnersConfig | None, sender: MailSender) -> None:
        self._env, self._rules, self._owners, self._sender = env, rules, owners, sender

    def __call__(self, notices: list[Notice]) -> None:
        if self._owners is None:
            raise DeliveryError("owner list missing")
        failures: list[str] = []
        for n in notices:
            mail = compose(n, self._owners, self._rules, self._env)
            if mail is None:
                failures.append(f"{n.component}: no owners configured")
                continue
            try:
                self._sender.send(mail)
                log.info("notice sent: %s %s %s", n.environment, n.kind.value, n.component)
            except DeliveryError as exc:
                failures.append(f"{n.component}: {exc}")
        if failures:
            raise DeliveryError(f"{len(failures)} of {len(notices)} not sent - " + "; ".join(failures))
