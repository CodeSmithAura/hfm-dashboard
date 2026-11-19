"""Shared logging set-up: UTC timestamps, daily UTC rotation, retention, line-wise stream capture."""

from __future__ import annotations

import logging
import logging.handlers

from hfm_dashboard.app.logs import LOG_FILE, LogWriter, UtcFormatter, configure_logging, remove_handlers


def record(created: float) -> logging.LogRecord:
    r = logging.LogRecord("hfm_dashboard", logging.INFO, __file__, 1, "hello", None, None)
    r.created, r.msecs = created, 250.0
    return r


def test_timestamps_are_utc_with_z():
    # 2026-11-04 09:00:00 UTC, whatever the workstation's time zone
    line = UtcFormatter("%(asctime)s %(message)s").format(record(1793782800.0))
    assert line == "2026-11-04 09:00:00.250Z hello"


def test_formatter_does_not_change_the_global_converter():
    import time
    UtcFormatter("%(asctime)s")
    assert logging.Formatter.converter is time.localtime


def test_file_rotates_at_utc_midnight_and_keeps_n_days(tmp_path):
    handlers = configure_logging(log_dir=tmp_path / "logs", keep_days=30, console=False)
    try:
        (fh,) = handlers
        assert isinstance(fh, logging.handlers.TimedRotatingFileHandler)
        assert fh.when == "MIDNIGHT" and fh.utc and fh.backupCount == 30
        logging.getLogger("hfm_dashboard.test").info("written to file")
        fh.flush()
        text = (tmp_path / "logs" / LOG_FILE).read_text(encoding="utf-8")
        assert "INFO hfm_dashboard.test: written to file" in text and "Z " in text
    finally:
        remove_handlers(handlers)
    assert not [h for h in logging.getLogger().handlers if h in handlers]


def test_unknown_retention_keeps_every_file(tmp_path):
    handlers = configure_logging(log_dir=tmp_path, keep_days=None, console=False)
    try:
        assert handlers[0].backupCount == 0     # 0 = never delete
    finally:
        remove_handlers(handlers)


def test_library_loggers_quietened(tmp_path):
    handlers = configure_logging(console=False)
    try:
        assert logging.getLogger("pypsrp").level == logging.WARNING
    finally:
        remove_handlers(handlers)


def test_log_writer_sends_whole_lines(caplog):
    w = LogWriter(logging.getLogger("hfm_dashboard.poll"), logging.INFO)
    with caplog.at_level(logging.INFO, logger="hfm_dashboard.poll"):
        w.write("PROD: 100.00% GREEN\n  db/db1: DOWN")
        assert [r.getMessage() for r in caplog.records] == ["PROD: 100.00% GREEN"]
        w.write(" - stopped\n\n")
        w.write("tail")
        w.flush()
    assert [r.getMessage() for r in caplog.records] == [
        "PROD: 100.00% GREEN", "  db/db1: DOWN - stopped", "tail"]
