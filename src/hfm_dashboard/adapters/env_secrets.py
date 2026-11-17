"""Secret store backed by environment variables (development, and interim until Q55 is decided).

Secret name ``hfm-mon/PROD/weblogic`` is read from ``HFM_MON_PROD_WEBLOGIC_USERNAME`` and
``HFM_MON_PROD_WEBLOGIC_PASSWORD``: every character that is not a letter or digit becomes ``_``,
upper case.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

from hfm_dashboard.ports.secrets import Secret, SecretNotFound


def variable_prefix(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", name).upper()


class EnvironmentSecretStore:
    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = os.environ if environ is None else environ

    def get(self, name: str) -> Secret:
        prefix = variable_prefix(name)
        user = self._environ.get(f"{prefix}_USERNAME")
        password = self._environ.get(f"{prefix}_PASSWORD")
        if not user or password is None:
            raise SecretNotFound(f"secret {name!r} not set (expected {prefix}_USERNAME and {prefix}_PASSWORD)")
        return Secret(user, password)


class FakeSecretStore:
    """In-memory store for tests: ``{name: (username, password)}``."""

    def __init__(self, secrets: Mapping[str, tuple[str, str]] | None = None) -> None:
        self._secrets = dict(secrets or {})

    def get(self, name: str) -> Secret:
        if name not in self._secrets:
            raise SecretNotFound(f"secret {name!r} not found")
        return Secret(*self._secrets[name])
