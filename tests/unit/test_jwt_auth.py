from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest

from forgepilot_api.core.jwt_auth import JwtValidationError, JwtValidationOptions, validate_hs_jwt


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _build_token(payload: dict, secret: str, alg: str = "HS256") -> str:
    header = {"typ": "JWT", "alg": alg}
    header_b64 = _b64url(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    hash_fn = hashlib.sha256 if alg == "HS256" else hashlib.sha384 if alg == "HS384" else hashlib.sha512
    sig = hmac.new(secret.encode("utf-8"), signing_input, hash_fn).digest()
    sig_b64 = _b64url(sig)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def test_validate_hs_jwt_success() -> None:
    secret = "test-secret"
    payload = {
        "sub": "tester",
        "scope": "providers.read files.read",
        "iss": "forgepilot",
        "aud": "forgepilot-client",
        "exp": int(time.time()) + 300,
    }
    token = _build_token(payload, secret=secret, alg="HS256")
    decoded = validate_hs_jwt(
        token,
        JwtValidationOptions(
            secret=secret,
            algorithms=("HS256",),
            issuer="forgepilot",
            audience="forgepilot-client",
        ),
    )
    assert decoded["sub"] == "tester"


def test_validate_hs_jwt_rejects_bad_signature() -> None:
    token = _build_token({"sub": "bad", "exp": int(time.time()) + 300}, secret="right")
    with pytest.raises(JwtValidationError):
        validate_hs_jwt(
            token,
            JwtValidationOptions(secret="wrong", algorithms=("HS256",), issuer=None, audience=None),
        )


def test_validate_hs_jwt_rejects_expired_token() -> None:
    token = _build_token({"sub": "expired", "exp": int(time.time()) - 5}, secret="s")
    with pytest.raises(JwtValidationError):
        validate_hs_jwt(
            token,
            JwtValidationOptions(secret="s", algorithms=("HS256",), issuer=None, audience=None),
        )
