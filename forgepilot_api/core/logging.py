from __future__ import annotations

import logging
import os
from pathlib import Path

from forgepilot_api.core.context import get_request_id

_CONFIGURED = False
_DEFAULT_LOG_FILE = "~/.forgepilot/logs/forgepilot.log"


class RequestAwareFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.request_id = get_request_id()
        return super().format(record)


def get_log_file_path() -> str:
    raw = os.getenv("FORGEPILOT_LOG_FILE", "").strip()
    path = Path(raw).expanduser() if raw else Path(_DEFAULT_LOG_FILE).expanduser()
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path
    return str(resolved)


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
    handlers: list[logging.Handler] = [handler]

    # Best-effort file logging for actionable diagnostics in desktop UI.
    log_file_path = get_log_file_path()
    try:
        log_file = Path(log_file_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(
            RequestAwareFormatter(
                fmt="%(asctime)s %(levelname)s [%(name)s] [req=%(request_id)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        handlers.append(file_handler)
    except Exception:
        # Keep stdout logging even if file logging cannot be initialized.
        pass

    # Replace ad-hoc default handlers to keep output consistent.
    root_logger.handlers = handlers
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
