"""Port: fetch one URL and report what happened (read-only GET, no credentials)."""

from __future__ import annotations

from typing import Protocol

from hfm_dashboard.domain.evaluation import UrlProbeObservation


class UrlProber(Protocol):
    def probe(self, url: str) -> UrlProbeObservation:
        """Never raises for network problems: a failed request is an observation too
        (``http_status=None`` with ``timed_out`` or ``error`` set). ``error`` never contains the URL."""
        ...
