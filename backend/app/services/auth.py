"""Authentication: password hashing, tokens, rotation, and rate limiting.

The design choice worth understanding is that access and refresh tokens are
deliberately *different kinds of thing*.

The access token is a JWT: stateless, fast to verify, and impossible to revoke.
That last property is a genuine weakness for data as sensitive as somebody's
speech transcripts, so it is compensated for rather than ignored — the token
lives ten minutes, is held only in memory on the client, and carries no
authority to renew itself.

The refresh token is not a JWT at all. It is 32 random bytes, stored server-side
as a SHA-256, rotated on every use, and grouped into a family. All revocation
power lives here, where the server actually controls it. Presenting a token that
has already been rotated is the classic signature of theft, and it revokes the
whole family rather than the single leaf.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

# OWASP-recommended argon2id parameters.
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

#: A real hash of a throwaway password. Verifying against this when the email is
#: unknown makes the failure path cost the same as the success path, so response
#: timing cannot be used to enumerate accounts.
_DUMMY_HASH = _hasher.hash("timing-equalisation-placeholder")

#: NIST 800-63B: length is the requirement. Composition rules ("one uppercase,
#: one symbol") measurably reduce entropy by pushing people toward Password1!
MIN_PASSWORD = 12
MAX_PASSWORD = 128

#: A deliberately small bundled list. A real deployment would check HIBP's
#: k-anonymity range API; that needs network access this demo should not require.
COMMON_PASSWORDS = frozenset(
    {
        "password", "password1", "password123", "passw0rd", "123456789012",
        "qwertyuiop", "1234567890", "letmein12345", "iloveyou1234",
        "administrator", "welcome12345", "monkey123456", "dragon123456",
        "baseball1234", "football1234", "trustno12345", "sunshine1234",
        "princess1234", "starwars1234", "whatever1234", "qwerty123456",
        "abc123456789", "changeme1234", "secretsecret", "passwordpassword",
    }
)


class AuthError(Exception):
    """Raised for anything that should surface as a 401 to the client."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-ish time verification.

    Passing None still runs a real argon2 verification against the dummy hash,
    so "no such user" costs the same as "wrong password".
    """
    try:
        _hasher.verify(password_hash or _DUMMY_HASH, password)
        return password_hash is not None
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def password_problem(password: str) -> str | None:
    """Return a human explanation, or None if the password is acceptable."""
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters. Length matters more than symbols."
    if len(password) > MAX_PASSWORD:
        return f"That's longer than {MAX_PASSWORD} characters."
    if password.lower() in COMMON_PASSWORDS:
        return "That password appears on lists of the most common ones. Pick something else."
    return None


def normalise_email(email: str) -> str:
    return email.strip().lower()


# ---- tokens -----------------------------------------------------------


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """Make a datetime read back from storage comparable to `utcnow()`.

    SQLite has no native timezone type, so a value written as aware comes back
    naive. Comparing the two raises TypeError — which would have crashed every
    token refresh and every WebSocket ticket check at runtime, not just in
    tests. Anything naive is assumed UTC, which is the only thing this
    application ever writes.
    """
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def create_access_token(user_id: str) -> tuple[str, int]:
    """Returns (token, seconds_until_expiry)."""
    now = utcnow()
    expires = now + timedelta(minutes=settings.access_token_minutes)
    payload = {
        "sub": user_id,
        "jti": secrets.token_urlsafe(12),
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        # Checked on every decode. Without it a refresh token could be
        # presented as an access token.
        "typ": "access",
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, settings.access_token_minutes * 60


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate, or raise AuthError with a client-safe reason."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("token_expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("token_invalid") from exc

    if payload.get("typ") != "access":
        raise AuthError("token_invalid")
    return payload


def new_opaque_token() -> tuple[str, str]:
    """A refresh or ws token: (plaintext, sha256). Only the hash is stored."""
    raw = secrets.token_urlsafe(32)
    return raw, sha256(raw)


# ---- rate limiting ----------------------------------------------------


@dataclass
class _Bucket:
    hits: deque[float]


class RateLimiter:
    """A fixed-window-per-key limiter.

    In-process and therefore per-worker: with several uvicorn workers the
    effective limit multiplies. That is acceptable for a local demo and is the
    first thing that would move to Redis if this were ever scaled horizontally.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}

    def check(self, key: str, limit: int, window_s: int) -> bool:
        """True if the call is allowed; records it when so."""
        now = time.monotonic()
        bucket = self._buckets.setdefault(key, _Bucket(deque()))

        while bucket.hits and now - bucket.hits[0] > window_s:
            bucket.hits.popleft()

        if len(bucket.hits) >= limit:
            return False

        bucket.hits.append(now)

        # Opportunistic sweep so a long-running process does not accumulate a
        # bucket per attacker IP forever.
        if len(self._buckets) > 4096:
            self._buckets = {
                k: b for k, b in self._buckets.items() if b.hits and now - b.hits[-1] < 3600
            }
        return True

    def reset(self, key: str) -> None:
        self._buckets.pop(key, None)

    def clear(self) -> None:
        self._buckets.clear()


limiter = RateLimiter()


def lockout_delay(failed_attempts: int) -> timedelta:
    """Exponential backoff, capped. Never permanent.

    A permanent lock is a denial of service that anyone who knows an address can
    trigger against its owner.
    """
    over = max(0, failed_attempts - settings.lockout_after + 1)
    seconds = min(settings.lockout_max_seconds, 30 * (2 ** (over - 1)) if over else 0)
    return timedelta(seconds=seconds)
