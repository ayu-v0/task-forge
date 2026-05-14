from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone

from src.apps.api.settings import settings


CONSOLE_SESSION_COOKIE = "taskForgeSession"
PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 260_000
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30


def hash_password(password: str) -> str:
    salt = secrets.token_urlsafe(18)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_HASH_ITERATIONS,
    )
    encoded_digest = base64.urlsafe_b64encode(digest).decode("ascii")
    return f"{PASSWORD_HASH_ALGORITHM}${PASSWORD_HASH_ITERATIONS}${salt}${encoded_digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt, encoded_digest = password_hash.split("$", 3)
        iterations = int(iterations_raw)
    except ValueError:
        return False

    if algorithm != PASSWORD_HASH_ALGORITHM:
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )
    expected = base64.urlsafe_b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, encoded_digest)


def _session_signature(payload: str) -> str:
    digest = hmac.new(
        settings.console_session_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def create_console_session_token(account: str) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=SESSION_TTL_SECONDS)
    payload = {
        "account": account,
        "expires_at": int(expires_at.timestamp()),
    }
    payload_raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    payload_encoded = base64.urlsafe_b64encode(payload_raw.encode("utf-8")).decode("ascii").rstrip("=")
    signature = _session_signature(payload_encoded)
    return f"{payload_encoded}.{signature}"


def verify_console_session_token(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None

    payload_encoded, signature = token.rsplit(".", 1)
    expected_signature = _session_signature(payload_encoded)
    if not hmac.compare_digest(signature, expected_signature):
        return None

    padded_payload = payload_encoded + ("=" * (-len(payload_encoded) % 4))
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded_payload.encode("ascii")).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None

    expires_at = payload.get("expires_at")
    if not isinstance(expires_at, int):
        return None
    if expires_at <= int(datetime.now(timezone.utc).timestamp()):
        return None
    if not isinstance(payload.get("account"), str):
        return None
    return payload
