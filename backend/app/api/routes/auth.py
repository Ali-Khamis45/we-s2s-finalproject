"""Accounts: register, sign in, rotate, sign out, export, delete."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import delete as sql_delete
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import UnauthorizedError, client_ip, current_user
from app.core.config import settings
from app.core.errors import AppError, ValidationError
from app.core.logging import get_logger
from app.db.models import RefreshToken, Session as SessionRow, Turn, User, WsTicket
from app.db.session import get_db
from app.schemas.auth import (
    AuthResponse,
    DeleteAccountRequest,
    LoginRequest,
    PasswordChangeRequest,
    RegisterRequest,
    UpdateMeRequest,
    UserOut,
    WsTicketOut,
)
from app.services import auth as svc

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = get_logger(__name__)

#: One message for every credential failure. Distinguishing "no such account"
#: from "wrong password" tells a stranger which addresses are registered.
BAD_CREDENTIALS = "Those details don't match an account."


class RateLimitedError(AppError):
    code = "rate_limited"
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    message = "Too many attempts. Wait a few minutes and try again."


class LockedError(AppError):
    code = "account_locked"
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    message = "Too many failed attempts. Try again shortly."


class CredentialsError(AppError):
    code = "bad_credentials"
    status_code = status.HTTP_401_UNAUTHORIZED
    message = BAD_CREDENTIALS


# ---- cookie helpers ---------------------------------------------------


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.refresh_cookie_name,
        token,
        max_age=settings.refresh_token_days * 24 * 3600,
        httponly=True,
        # Conditioned on debug so localhost http works — never removed.
        secure=not settings.debug,
        samesite="strict",
        path=settings.refresh_cookie_path,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        httponly=True,
        secure=not settings.debug,
        samesite="strict",
    )


async def _issue_refresh(
    db: AsyncSession, user_id: str, family_id: str | None, user_agent: str | None
) -> str:
    raw, hashed = svc.new_opaque_token()
    db.add(
        RefreshToken(
            user_id=user_id,
            token_hash=hashed,
            family_id=family_id or svc.secrets.token_urlsafe(12),
            expires_at=svc.utcnow() + timedelta(days=settings.refresh_token_days),
            user_agent=(user_agent or "")[:255] or None,
        )
    )
    return raw


async def _auth_response(
    db: AsyncSession,
    response: Response,
    user: User,
    request: Request,
    family_id: str | None = None,
) -> AuthResponse:
    raw = await _issue_refresh(db, user.id, family_id, request.headers.get("user-agent"))
    _set_refresh_cookie(response, raw)
    access, expires_in = svc.create_access_token(user.id)
    return AuthResponse(
        access_token=access, expires_in=expires_in, user=UserOut.model_validate(user)
    )


# ---- routes -----------------------------------------------------------


@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    ip = client_ip(request)
    if not svc.limiter.check(f"register:{ip}", *settings.register_rate):
        raise RateLimitedError()

    if problem := svc.password_problem(payload.password):
        raise ValidationError(problem)

    email = svc.normalise_email(payload.email)
    existing = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()

    if existing is not None:
        # Deliberately indistinguishable from a fresh signup: same status, same
        # shape, same cost. Returning 409 here would turn this endpoint into an
        # oracle for which addresses have accounts. The existing account is NOT
        # signed in — that would hand it to whoever guessed the address.
        log.info("register: address already in use", extra={"outcome": "silent"})
        svc.verify_password(None, payload.password)
        return await _decoy_response(db, response, request, payload)

    user = User(
        email=email,
        password_hash=svc.hash_password(payload.password),
        display_name=(payload.display_name or "").strip() or None,
        last_login_at=svc.utcnow(),
    )
    db.add(user)
    await db.flush()
    result = await _auth_response(db, response, user, request)
    await db.commit()
    return result


async def _decoy_response(
    db: AsyncSession, response: Response, request: Request, payload: RegisterRequest
) -> AuthResponse:
    """A 201-shaped answer for an address that already exists.

    No account is created and no session is issued — the returned token belongs
    to nobody and will fail on first use. The point is only that the *response*
    reveals nothing.
    """
    await db.rollback()
    access, expires_in = svc.create_access_token("00000000000000000000000000000000")
    return AuthResponse(
        access_token=access,
        expires_in=expires_in,
        user=UserOut(
            id="00000000000000000000000000000000",
            email=svc.normalise_email(payload.email),
            display_name=(payload.display_name or "").strip() or None,
            created_at=svc.utcnow(),
            memory_enabled=True,
        ),
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    email = svc.normalise_email(payload.email)
    ip = client_ip(request)
    if not svc.limiter.check(f"login:{email}:{ip}", *settings.login_rate):
        raise RateLimitedError()

    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()

    if user and (locked := svc.as_utc(user.locked_until)) and locked > svc.utcnow():
        raise LockedError()

    # Runs a real argon2 verification either way, so an unknown address costs
    # the same as a wrong password.
    if not svc.verify_password(user.password_hash if user else None, payload.password):
        if user is not None:
            user.failed_attempts += 1
            if user.failed_attempts >= settings.lockout_after:
                user.locked_until = svc.utcnow() + svc.lockout_delay(user.failed_attempts)
            await db.commit()
        raise CredentialsError()

    assert user is not None
    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = svc.utcnow()
    if svc.needs_rehash(user.password_hash):
        user.password_hash = svc.hash_password(payload.password)

    svc.limiter.reset(f"login:{email}:{ip}")
    result = await _auth_response(db, response, user, request)
    await db.commit()
    return result


@router.post("/refresh", response_model=AuthResponse)
async def refresh(
    request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> AuthResponse:
    raw = request.cookies.get(settings.refresh_cookie_name)
    if not raw:
        raise UnauthorizedError()

    row = (
        await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == svc.sha256(raw))
        )
    ).scalar_one_or_none()

    if row is None:
        _clear_refresh_cookie(response)
        raise UnauthorizedError()

    # Reuse detection. A token that has already been rotated should never be
    # presented again — if it is, either it was stolen or a clone is running,
    # and in both cases every descendant of that login is suspect. Revoking the
    # whole family is the entire reason rotation is worth the complexity.
    if row.used_at is not None:
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == row.family_id)
            .values(revoked_at=svc.utcnow())
        )
        await db.commit()
        _clear_refresh_cookie(response)
        log.warning("refresh token reuse; family revoked", extra={"family": row.family_id})
        raise UnauthorizedError()

    if row.revoked_at is not None or svc.as_utc(row.expires_at) <= svc.utcnow():
        _clear_refresh_cookie(response)
        raise UnauthorizedError()

    if not svc.limiter.check(f"refresh:{row.user_id}", *settings.refresh_rate):
        raise RateLimitedError()

    user = await db.get(User, row.user_id)
    if user is None:
        _clear_refresh_cookie(response)
        raise UnauthorizedError()

    row.used_at = svc.utcnow()
    result = await _auth_response(db, response, user, request, family_id=row.family_id)
    await db.commit()
    return result


@router.post("/logout", status_code=204)
async def logout(
    request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> None:
    raw = request.cookies.get(settings.refresh_cookie_name)
    if raw:
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == svc.sha256(raw))
            .values(revoked_at=svc.utcnow())
        )
        await db.commit()
    _clear_refresh_cookie(response)


@router.post("/logout-all", status_code=204)
async def logout_all(
    response: Response,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=svc.utcnow())
    )
    await db.commit()
    _clear_refresh_cookie(response)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)) -> UserOut:
    return UserOut.model_validate(user)


@router.patch("/me", response_model=UserOut)
async def update_me(
    payload: UpdateMeRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    if payload.display_name is not None:
        user.display_name = payload.display_name.strip() or None
    if payload.memory_enabled is not None:
        user.memory_enabled = payload.memory_enabled
    await db.commit()
    return UserOut.model_validate(user)


@router.post("/me/password", status_code=204)
async def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    if not svc.verify_password(user.password_hash, payload.current_password):
        raise CredentialsError("That current password isn't right.")
    if problem := svc.password_problem(payload.new_password):
        raise ValidationError(problem)

    user.password_hash = svc.hash_password(payload.new_password)

    # Every other device is signed out; the one making the change keeps working.
    current = request.cookies.get(settings.refresh_cookie_name)
    stmt = update(RefreshToken).where(
        RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
    )
    if current:
        stmt = stmt.where(RefreshToken.token_hash != svc.sha256(current))
    await db.execute(stmt.values(revoked_at=svc.utcnow()))
    await db.commit()


@router.post("/ws-ticket", response_model=WsTicketOut)
async def ws_ticket(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> WsTicketOut:
    """A short-lived, single-use ticket for opening a WebSocket.

    Browsers cannot set headers on a WebSocket, and a JWT in the query string
    would be written into access logs and browser history. This is neither: it
    is worthless 30 seconds after issue and worthless after one use.
    """
    raw, hashed = svc.new_opaque_token()
    db.add(
        WsTicket(
            user_id=user.id,
            token_hash=hashed,
            expires_at=svc.utcnow() + timedelta(seconds=settings.ws_ticket_seconds),
        )
    )
    await db.commit()
    return WsTicketOut(ticket=raw, expires_in=settings.ws_ticket_seconds)


@router.get("/me/export")
async def export_me(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    """Everything this account owns, and nothing else."""
    sessions = list(
        (
            await db.execute(select(SessionRow).where(SessionRow.user_id == user.id))
        ).scalars()
    )
    return {
        "account": UserOut.model_validate(user).model_dump(mode="json"),
        "exported_at": svc.utcnow().isoformat(),
        "sessions": [
            {
                "id": s.id,
                "title": s.title,
                "started_at": s.started_at.isoformat(),
                "turns": [
                    {
                        "role": t.role,
                        "mode": t.mode,
                        "text": t.text,
                        "created_at": t.created_at.isoformat(),
                        "acoustic": t.acoustic,
                        "citations": t.citations,
                    }
                    for t in s.turns
                ],
            }
            for s in sessions
        ],
        # Stated explicitly so an export reads as complete rather than partial.
        "note": "Audio is never stored. Transcripts and derived acoustic measures only.",
    }


@router.delete("/me", status_code=204)
async def delete_me(
    payload: DeleteAccountRequest,
    response: Response,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Hard delete. No `is_deleted` flag.

    Someone asking to be erased is not asking to be hidden. Turns are removed
    explicitly because SQLite does not enforce ON DELETE CASCADE unless foreign
    keys are switched on per connection, and relying on that would leave
    orphaned transcripts behind.
    """
    if not svc.verify_password(user.password_hash, payload.current_password):
        raise CredentialsError("That password isn't right.")

    session_ids = list(
        (
            await db.execute(select(SessionRow.id).where(SessionRow.user_id == user.id))
        ).scalars()
    )
    if session_ids:
        await db.execute(sql_delete(Turn).where(Turn.session_id.in_(session_ids)))
    await db.execute(sql_delete(SessionRow).where(SessionRow.user_id == user.id))
    await db.execute(sql_delete(RefreshToken).where(RefreshToken.user_id == user.id))
    await db.execute(sql_delete(WsTicket).where(WsTicket.user_id == user.id))
    await db.execute(sql_delete(User).where(User.id == user.id))
    await db.commit()
    _clear_refresh_cookie(response)

