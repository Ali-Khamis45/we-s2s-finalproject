"""Request dependencies: who is calling, and what they are allowed to touch.

Every ownership decision in the application routes through here. Scattering
`row.user_id != user.id` across handlers is how one route eventually forgets,
and the thing it leaks is a record of somebody's speech difficulties.
"""

from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError
from app.db.models import Session as SessionRow
from app.db.models import User
from app.db.session import get_db
from app.services.auth import AuthError, decode_access_token

#: auto_error=False so a missing header produces our own envelope rather than
#: FastAPI's, keeping every failure shaped the same for the client.
_bearer = HTTPBearer(auto_error=False)


class UnauthorizedError(AppError):
    code = "unauthorized"
    status_code = 401
    message = "Sign in to continue."


class TokenExpiredError(AppError):
    """Distinct from `unauthorized` on purpose.

    The client refreshes exactly on this code. Making expiry look identical to
    a bad token would mean either refreshing on every 401 (a loop when the
    refresh itself is dead) or never refreshing at all.
    """

    code = "token_expired"
    status_code = 401
    message = "Your session timed out. Signing you back in…"


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the caller from the Authorization header, or 401."""
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError()

    try:
        payload = decode_access_token(credentials.credentials)
    except AuthError as exc:
        if str(exc) == "token_expired":
            raise TokenExpiredError() from exc
        raise UnauthorizedError() from exc

    user = await db.get(User, payload["sub"])
    if user is None:
        # The account was deleted while a valid token was still in flight.
        raise UnauthorizedError()
    return user


async def owned_session(
    session_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionRow:
    """Fetch a session, or 404 if it is absent *or* belongs to someone else.

    404 rather than 403 is deliberate. A 403 confirms the id exists, which is
    itself a disclosure — it tells a stranger that a given session is real and
    simply not theirs. "Absent" and "not yours" must be indistinguishable.
    """
    row = await db.get(SessionRow, session_id)
    if row is None or row.user_id != user.id:
        raise NotFoundError("That practice session doesn't exist.")
    return row


def client_ip(request: Request) -> str:
    """Best-effort client address for rate limiting.

    X-Forwarded-For is honoured because a reverse proxy is the normal
    deployment, but it is client-controlled and therefore only ever used as a
    rate-limit key — never for authorisation.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
