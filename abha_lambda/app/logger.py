import logging
from typing import Optional
import os
import sys
from colorlog import ColoredFormatter

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logger = logging.getLogger()
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = ColoredFormatter(
        fmt="%(log_color)s%(levelname)-8s%(reset)s %(name)s:%(lineno)d - %(message)s",
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


def get_logger(name: Optional[str] = None):
    return logging.getLogger(name)
