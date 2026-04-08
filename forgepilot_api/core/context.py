from __future__ import annotations

from contextvars import ContextVar, Token

_REQUEST_ID_CTX: ContextVar[str] = ContextVar("forgepilot_request_id", default="-")


def get_request_id() -> str:
    return _REQUEST_ID_CTX.get()


def set_request_id(request_id: str) -> Token[str]:
    return _REQUEST_ID_CTX.set(request_id)


def reset_request_id(token: Token[str]) -> None:
    _REQUEST_ID_CTX.reset(token)
