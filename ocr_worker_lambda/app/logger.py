"""
Lightweight logger for OCRWorkerLambda.

Uses stdlib logging only — no colorlog dependency (worker has no TTY).
CloudWatch Logs captures stdout automatically in Lambda.
"""
import logging
import os
import sys

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

_root = logging.getLogger()
if not _root.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(
        logging.Formatter(
            fmt="%(levelname)-8s %(name)s:%(lineno)d - %(message)s",
        )
    )
    _root.addHandler(_handler)

_root.setLevel(LOG_LEVEL)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("boto3").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(name)
