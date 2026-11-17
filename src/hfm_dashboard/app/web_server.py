"""Run the dashboard web app (uvicorn) in a background thread next to the poll scheduler."""

from __future__ import annotations

import logging
import threading

import uvicorn

log = logging.getLogger(__name__)


class WebServer:
    def __init__(self, app, host: str, port: int) -> None:
        config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False,
                                server_header=False, date_header=False)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="dashboard-web", daemon=True)
        self.port = port

    def start(self) -> None:
        self._thread.start()
        log.info("dashboard listening on port %s", self.port)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)

    @property
    def started(self) -> bool:
        return self._server.started
