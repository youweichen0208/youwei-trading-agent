"""S02b: structured logging — one JSON object per line, extras ride
along, exceptions captured."""

import json
import logging

from youwei_core.logfmt import JsonFormatter, configure_logging


def test_json_line_structure():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="youwei.worker",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="job claimed %s",
        args=("j-123",),
        exc_info=None,
    )
    record.__dict__["job_id"] = "j-123"
    line = formatter.format(record)
    parsed = json.loads(line)
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "youwei.worker"
    assert parsed["msg"] == "job claimed j-123"
    assert parsed["job_id"] == "j-123"
    assert "ts" in parsed


def test_exception_captured():
    import sys

    formatter = JsonFormatter()
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = logging.LogRecord(
            name="x", level=logging.ERROR, pathname=__file__, lineno=1,
            msg="failed", args=None, exc_info=sys.exc_info(),
        )
    parsed = json.loads(formatter.format(record))
    assert "RuntimeError: boom" in parsed["exc"]


def test_configure_logging_installs_handler():
    configure_logging()
    root = logging.getLogger()
    assert any(isinstance(h.handler if hasattr(h, "handler") else h, logging.StreamHandler) for h in root.handlers)
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
