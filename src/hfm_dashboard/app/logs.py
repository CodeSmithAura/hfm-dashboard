"""Logging set-up shared by the console command and the Windows service.

- Timestamps in UTC with a ``Z`` suffix (hard rule 5), set per formatter, not globally.
- Optional log file in ``log_dir``: rotated at 00:00 UTC, ``keep_days`` old files kept
  (rules.yaml ``monitor_service.log_keep_days``); ``None`` keeps every file.
- Library INFO logs (pypsrp, httpx, ...) repeat every poll and include scripts and URLs with host
  names: only their warnings and errors are kept.
"""

from __future__ import annotations

import io
import logging
import logging.handlers
import time
from pathlib import Path

LOG_FILE = "hfm-dashboard.log"
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
NOISY_LOGGERS = ("pypsrp", "spnego", "requests", "urllib3", "httpx", "httpcore", "apscheduler")


class UtcFormatter(logging.Formatter):
    converter = time.gmtime

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        t = self.converter(record.created)
        return time.strftime("%Y-%m-%d %H:%M:%S", t) + f".{int(record.msecs):03d}Z"


def quiet_libraries() -> None:
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def file_handler(log_dir: Path, keep_days: int | None) -> logging.Handler:
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.TimedRotatingFileHandler(
        log_dir / LOG_FILE, when="midnight", utc=True, backupCount=keep_days or 0, encoding="utf-8",
        delay=False)
    handler.setFormatter(UtcFormatter(FORMAT))
    return handler


def configure_logging(*, log_dir: Path | None = None, keep_days: int | None = None,
                      console: bool = True, level: int = logging.INFO) -> list[logging.Handler]:
    """Attach handlers to the root logger; returns them so a caller (or a test) can remove them."""
    handlers: list[logging.Handler] = []
    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(UtcFormatter(FORMAT))
        handlers.append(stream)
    if log_dir is not None:
        handlers.append(file_handler(log_dir, keep_days))
    root = logging.getLogger()
    root.setLevel(level)
    for h in handlers:
        root.addHandler(h)
    quiet_libraries()
    return handlers


def remove_handlers(handlers: list[logging.Handler]) -> None:
    root = logging.getLogger()
    for h in handlers:
        root.removeHandler(h)
        h.close()


class LogWriter(io.TextIOBase):
    """A text stream that sends each written line to a logger (a service has no console)."""

    def __init__(self, logger: logging.Logger, level: int) -> None:
        self._logger = logger
        self._level = level
        self._buffer = ""

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self._buffer += text
        *lines, self._buffer = self._buffer.split("\n")
        for line in lines:
            if line.strip():
                self._logger.log(self._level, line.rstrip())
        return len(text)

    def flush(self) -> None:
        if self._buffer.strip():
            self._logger.log(self._level, self._buffer.rstrip())
        self._buffer = ""
