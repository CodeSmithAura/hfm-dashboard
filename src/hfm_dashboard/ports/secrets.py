"""Port: look up a credential by its secret NAME (from environments.yaml ``secrets:``).

Secret values never appear in files, logs or error messages (hard rule 2). Which store holds them
in production is still open (Q55); adapters exist for environment variables and for tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class SecretNotFound(Exception):
    """No secret under that name. The message names the secret, never a value."""


@dataclass(frozen=True, slots=True)
class Secret:
    username: str
    password: str

    def __repr__(self) -> str:
        return f"Secret(username={self.username!r}, password=***)"


class SecretStore(Protocol):
    def get(self, name: str) -> Secret: ...
