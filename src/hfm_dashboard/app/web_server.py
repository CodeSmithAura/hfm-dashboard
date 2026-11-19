"""Run the dashboard web app (uvicorn) in a background thread next to the poll scheduler."""

from __future__ import annotations

import logging
import threading
import time

import uvicorn

log = logging.getLogger(__name__)

# How long start() waits for uvicorn to bind (a technical limit, not monitoring behaviour).
STARTUP_WAIT_SECONDS = 15


class WebServer:
    def __init__(self, app, host: str, port: int) -> None:
        config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False,
                                server_header=False, date_header=False)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="dashboard-web", daemon=True)
        self.port = port

    def start(self, wait_seconds: float = STARTUP_WAIT_SECONDS) -> bool:
        """Start serving; False if the server could not start (e.g. port in use, bad bind address)."""
        self._thread.start()
        deadline = time.monotonic() + wait_seconds
        while not self._server.started and self._thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not self._server.started:
            log.error("dashboard could not start on port %s (port in use or bind address not on this "
                      "server?)", self.port)
            return False
        log.info("dashboard listening on port %s", self.port)
        return True

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)

    @property
    def started(self) -> bool:
        return self._server.started
