from __future__ import annotations

import logging

from forgepilot_api.core.context import get_request_id

_CONFIGURED = False


class RequestAwareFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.request_id = get_request_id()
        return super().format(record)


def configure_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if _CONFIGURED:
        return

    handler = logging.StreamHandler()
    handler.setFormatter(
        RequestAwareFormatter(
            fmt="%(asctime)s %(levelname)s [%(name)s] [req=%(request_id)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    # Replace ad-hoc default handlers to keep output consistent.
    root_logger.handlers = [handler]
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
