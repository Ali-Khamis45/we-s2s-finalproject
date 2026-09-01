"""Resolving a WebSocket's owner from a single-use ticket.

A browser `WebSocket` cannot carry an Authorization header, and putting a JWT in
the query string writes a live credential into every access log, proxy log and
browser history entry. So the socket carries a ticket instead: opaque, valid for
30 seconds, usable once, and worthless to anyone who finds it later.

The ticket is resolved and burned **before a single audio frame is read**.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import User, WsTicket
from app.services.auth import as_utc, sha256, utcnow

log = get_logger(__name__)

#: Close code for "you are not authenticated". 4000-4999 is the application
#: range; 4401 mirrors HTTP 401 so the client can branch on it unambiguously.
WS_UNAUTHORIZED = 4401


async def user_from_ticket(db: AsyncSession, ticket: str | None) -> User | None:
    """Resolve and consume a ticket. Returns None for every failure mode.

    Missing, unknown, expired and already-used all return None rather than
    distinct errors: the socket is about to be closed either way, and the client
    has no legitimate use for knowing which.
    """
    if not ticket:
        return None

    row = (
        await db.execute(select(WsTicket).where(WsTicket.token_hash == sha256(ticket)))
    ).scalar_one_or_none()

    if row is None:
        return None

    now = utcnow()
    if row.used_at is not None or (as_utc(row.expires_at) or now) <= now:
        return None

    # Burn it before returning, so a replay in flight cannot also succeed.
    row.used_at = now
    user = await db.get(User, row.user_id)
    await db.commit()
    return user

