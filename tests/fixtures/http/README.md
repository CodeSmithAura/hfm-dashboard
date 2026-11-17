# URL probe samples

A URL probe's observable result is only an HTTP status code and a response time, so the probe
tests script responses with `httpx.MockTransport` (see `tests/adapters/test_http_probe.py`) instead
of storing response bodies. Once access exists, the useful recording is the status code and time
per probe and node from the development servers (masked with `tools/masking.py`), to tune
`checks.url_probe.degraded_after_seconds` during shadow mode.
