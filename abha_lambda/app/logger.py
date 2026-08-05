import logging
from contextvars import ContextVar
from typing import Optional
import os
import sys
from colorlog import ColoredFormatter

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# Request-scoped tag distinguishing where an invocation came from:
#   "abdm_webhook" — inbound callback POSTed to us by ABDM
#   "internal"     — anything we triggered ourselves (our /abha endpoints)
# Set once per request by the middleware in main.py and read by SourceFilter so
# that EVERY log line during that request (including downstream service logs)
# carries the tag — not just the lines logged in the webhook router itself.
# Defaults to "internal" so cold-start / non-request logs stay classified.
log_source_var: ContextVar[str] = ContextVar("log_source", default="internal")


class SourceFilter(logging.Filter):
    """Inject the request-scoped source tag onto every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.source = log_source_var.get()
        return True


logger = logging.getLogger()

# The AWS Lambda Python runtime pre-attaches its own handler to the root
# logger before this module is imported, so `if not logger.handlers` is
# already False here — our handler/formatter/filter would silently never be
# added and everything falls back to Lambda's default log format (no
# `source` field). Strip whatever the runtime installed and set our own.
logger.handlers.clear()

handler = logging.StreamHandler(sys.stdout)
handler.addFilter(SourceFilter())
formatter = ColoredFormatter(
    fmt="%(log_color)s%(levelname)-8s%(reset)s [source=%(source)s] %(name)s:%(lineno)d - %(message)s",
    datefmt=None,
    log_colors={
        "DEBUG":    "blue",
        "INFO":     "green",
        "WARNING":  "yellow",
        "ERROR":    "red",
        "CRITICAL": "red,bg_white",
    },
    secondary_log_colors={},
    style="%",
)
handler.setFormatter(formatter)
logger.addHandler(handler)

logger.setLevel(LOG_LEVEL)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("boto3").setLevel(logging.WARNING)


def set_log_source(source: str) -> None:
    """Set the request-scoped source tag (called by the request middleware)."""
    log_source_var.set(source)


def get_logger(name: Optional[str] = None):
    return logging.getLogger(name)
