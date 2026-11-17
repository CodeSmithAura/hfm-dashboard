"""In-memory fake of the URL prober, for tests and offline dashboard runs."""

from __future__ import annotations

from hfm_dashboard.domain.evaluation import UrlProbeObservation

HEALTHY = UrlProbeObservation(http_status=200, elapsed_seconds=0.5)


class FakeUrlProber:
    """``responses[url]`` is returned for that URL; any other URL gets ``default``."""

    def __init__(self, responses: dict[str, UrlProbeObservation] | None = None,
                 default: UrlProbeObservation = HEALTHY) -> None:
        self.responses = dict(responses or {})
        self.default = default
        self.calls: list[str] = []

    def probe(self, url: str) -> UrlProbeObservation:
        self.calls.append(url)
        return self.responses.get(url, self.default)
