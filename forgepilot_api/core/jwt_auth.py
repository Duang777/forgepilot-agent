from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any


class JwtValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class JwtValidationOptions:
    secret: str
    algorithms: tuple[str, ...]
    issuer: str | None
    audience: str | None


def _b64url_decode(text: str) -> bytes:
    padded = text + "=" * ((4 - (len(text) % 4)) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as exc:
        raise JwtValidationError("Invalid JWT base64 payload") from exc


def _json_decode(data: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(data.decode("utf-8"))
    except Exception as exc:
        raise JwtValidationError("Invalid JWT JSON") from exc
    if not isinstance(parsed, dict):
        raise JwtValidationError("Invalid JWT JSON type")
    return parsed


def _hash_for_algorithm(alg: str):
    normalized = alg.upper()
    if normalized == "HS256":
        return hashlib.sha256
    if normalized == "HS384":
        return hashlib.sha384
    if normalized == "HS512":
        return hashlib.sha512
    raise JwtValidationError(f"Unsupported JWT algorithm: {alg}")


def _validate_times(payload: dict[str, Any], now_ts: int) -> None:
    exp = payload.get("exp")
    if exp is not None:
        try:
            exp_num = int(exp)
        except Exception as exc:
            raise JwtValidationError("Invalid exp claim") from exc
        if now_ts >= exp_num:
            raise JwtValidationError("Token expired")

    nbf = payload.get("nbf")
    if nbf is not None:
        try:
            nbf_num = int(nbf)
        except Exception as exc:
            raise JwtValidationError("Invalid nbf claim") from exc
        if now_ts < nbf_num:
            raise JwtValidationError("Token not active yet")

    iat = payload.get("iat")
    if iat is not None:
        try:
            iat_num = int(iat)
        except Exception as exc:
            raise JwtValidationError("Invalid iat claim") from exc
        if iat_num > now_ts + 300:
            raise JwtValidationError("Token iat is in the future")


def validate_hs_jwt(token: str, options: JwtValidationOptions) -> dict[str, Any]:
    if not options.secret:
        raise JwtValidationError("JWT secret is not configured")

    parts = token.split(".")
    if len(parts) != 3:
        raise JwtValidationError("Invalid JWT format")

    header_b64, payload_b64, signature_b64 = parts
    header = _json_decode(_b64url_decode(header_b64))
    payload = _json_decode(_b64url_decode(payload_b64))

    alg = str(header.get("alg") or "").upper()
    if not alg:
        raise JwtValidationError("Missing JWT algorithm")
    allowed = {item.upper() for item in options.algorithms}
    if alg not in allowed:
        raise JwtValidationError("JWT algorithm is not allowed")

    hash_fn = _hash_for_algorithm(alg)
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected = hmac.new(options.secret.encode("utf-8"), signing_input, hash_fn).digest()
    actual = _b64url_decode(signature_b64)
    if not hmac.compare_digest(actual, expected):
        raise JwtValidationError("Invalid JWT signature")

    now_ts = int(time.time())
    _validate_times(payload, now_ts)

    if options.issuer is not None:
        issuer = str(payload.get("iss") or "")
        if issuer != options.issuer:
            raise JwtValidationError("Invalid JWT issuer")

    if options.audience is not None:
        aud = payload.get("aud")
        if isinstance(aud, list):
            audiences = {str(item) for item in aud}
        elif isinstance(aud, str):
            audiences = {aud}
        else:
            audiences = set()
        if options.audience not in audiences:
            raise JwtValidationError("Invalid JWT audience")

    return payload
