from __future__ import annotations

import hmac
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ApiKeyRecord:
    subject: str
    secret: str


def parse_api_keys(raw_items: list[str]) -> list[ApiKeyRecord]:
    records: list[ApiKeyRecord] = []
    for index, raw in enumerate(raw_items, start=1):
        text = raw.strip()
        if not text:
            continue
        if ":" in text:
            subject, secret = text.split(":", 1)
            subject = subject.strip() or f"key-{index}"
            secret = secret.strip()
        else:
            subject = f"key-{index}"
            secret = text
        if not secret:
            continue
        records.append(ApiKeyRecord(subject=subject, secret=secret))
    return records


def verify_api_key(candidate: str, records: list[ApiKeyRecord]) -> ApiKeyRecord | None:
    key = candidate.strip()
    if not key:
        return None
    for record in records:
        if hmac.compare_digest(key, record.secret):
            return record
    return None
