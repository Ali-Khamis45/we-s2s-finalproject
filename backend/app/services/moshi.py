"""Live Coach transport (A3) and Inner Monologue consumer (A4).

Moshi runs as a separate process owned by Track M (task M2), because it is the
only thing on the GPU and it has its own Rust/Candle runtime. This module is the
client.

The Inner Monologue is the interesting part. Moshi predicts text tokens as a
prefix to its audio tokens, and that text stream is readable in real time. It is
what lets a native-S2S product satisfy a text-pipeline rubric coherently: it
feeds conversation history, and it is what the orchestrator watches to decide a
turn needs grounded, cited content that native S2S structurally cannot produce.

Protocol: a one-byte tag followed by a payload, per the Kyutai convention. Track
M owns the exact framing on the server side (M2) — if it diverges, the constants
below are the single place to change.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


class Tag(IntEnum):
    """Leading byte of every Moshi frame."""

    HANDSHAKE = 0x00
    AUDIO = 0x01
    TEXT = 0x02
    CONTROL = 0x03
    ERROR = 0x04


@dataclass(slots=True)
class MoshiAudio:
    pcm: bytes
    sample_rate: int


@dataclass(slots=True)
class MoshiText:
    """One Inner Monologue delta."""

    text: str


MoshiEvent = MoshiAudio | MoshiText


class MoshiUnavailable(RuntimeError):
    """The service could not be reached.

    Not an AppError: this is expected and recoverable. The orchestrator catches
    it and degrades to the cascade rather than failing the session (A14).
    """


class MoshiStream:
    """One open conversation with Moshi.

    Full-duplex: audio is pushed in continuously while events stream out. There
    is no turn-taking protocol to implement, because Moshi does not have turns —
    barge-in works because both directions are always live.
    """

    def __init__(self, ws: Any, sample_rate: int) -> None:
        self._ws = ws
        self.sample_rate = sample_rate
        self._closed = False

    async def send_audio(self, pcm: bytes) -> None:
        """Push one chunk of 16-bit PCM at the negotiated sample rate."""
        if self._closed or not pcm:
            return
        try:
            await self._ws.send(bytes([Tag.AUDIO]) + pcm)
        except Exception as exc:
            self._closed = True
            raise MoshiUnavailable(f"Moshi send failed: {exc}") from exc

    async def events(self) -> AsyncIterator[MoshiEvent]:
        """Yield audio and Inner Monologue text as they arrive."""
        try:
            async for frame in self._ws:
                if isinstance(frame, str):
                    # Some builds emit plain-text control frames; ignore rather
                    # than guessing at a schema Track M has not committed to.
                    continue
                if not frame:
                    continue

                tag, payload = frame[0], frame[1:]
                if tag == Tag.AUDIO:
                    if payload:
                        yield MoshiAudio(pcm=payload, sample_rate=self.sample_rate)
                elif tag == Tag.TEXT:
                    if text := payload.decode("utf-8", errors="replace"):
                        yield MoshiText(text=text)
                elif tag == Tag.ERROR:
                    detail = payload.decode("utf-8", errors="replace")
                    raise MoshiUnavailable(f"Moshi reported an error: {detail}")
                # HANDSHAKE and CONTROL carry no application payload.
        except MoshiUnavailable:
            raise
        except Exception as exc:
            raise MoshiUnavailable(f"Moshi stream ended: {exc}") from exc
        finally:
            self._closed = True

    async def close(self) -> None:
        self._closed = True
        with contextlib.suppress(Exception):
            await self._ws.close()


class MoshiClient:
    """Connection factory plus a cached availability probe.

    Availability is cached because it is checked on every session start, and a
    dead service means a TCP timeout each time. The cache keeps a degraded
    session fast to start instead of stalling for the connect timeout.
    """

    #: How long a negative probe result stays cached.
    PROBE_TTL_S = 20.0

    def __init__(self) -> None:
        self._available: bool | None = None
        self._probed_at: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return settings.moshi_enabled

    def invalidate(self) -> None:
        """Force the next probe to actually connect.

        Called when a live stream dies, so recovery is noticed promptly.
        """
        self._available = None
        self._probed_at = 0.0

    async def available(self, *, force: bool = False) -> bool:
        if not self.enabled:
            return False

        fresh = (time.monotonic() - self._probed_at) < self.PROBE_TTL_S
        if not force and self._available is not None and fresh:
            return self._available

        async with self._lock:
            if not force and self._available is not None:
                if (time.monotonic() - self._probed_at) < self.PROBE_TTL_S:
                    return self._available
            try:
                ws = await self._connect()
                await ws.close()
                self._available = True
            except Exception as exc:
                if self._available is not False:
                    log.info("moshi unavailable", extra={"reason": str(exc)[:200]})
                self._available = False
            self._probed_at = time.monotonic()
            return self._available

    async def _connect(self) -> Any:
        try:
            import websockets
        except ImportError as exc:
            raise MoshiUnavailable("The 'websockets' package isn't installed.") from exc

        try:
            return await asyncio.wait_for(
                websockets.connect(
                    settings.moshi_url,
                    max_size=None,        # audio frames are large and continuous
                    ping_interval=20,
                    ping_timeout=20,
                ),
                timeout=settings.moshi_connect_timeout_s,
            )
        except asyncio.TimeoutError as exc:
            raise MoshiUnavailable(
                f"Moshi did not answer within {settings.moshi_connect_timeout_s:.0f}s"
            ) from exc
        except Exception as exc:
            raise MoshiUnavailable(f"Could not reach Moshi: {exc}") from exc

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[MoshiStream]:
        """Open one conversation, closing it on the way out."""
        if not self.enabled:
            raise MoshiUnavailable("The live coach is disabled in configuration.")

        ws = await self._connect()
        stream = MoshiStream(ws, settings.moshi_sample_rate)
        self._available = True
        self._probed_at = time.monotonic()
        log.info("moshi session open", extra={"url": settings.moshi_url})
        try:
            yield stream
        finally:
            await stream.close()
            log.info("moshi session closed")


class MonologueAccumulator:
    """Assembles Inner Monologue deltas into utterances (A4).

    Moshi emits text token by token with no utterance boundaries, so this
    buffers deltas and closes an utterance on sentence-final punctuation or
    after a silence gap. What comes out is what gets written to conversation
    history and tested for a Knowledge Mode handoff.
    """

    #: Silence long enough to treat the buffer as a finished thought.
    IDLE_MS = 900

    def __init__(self) -> None:
        self._buf: list[str] = []
        self._last_delta_at = time.monotonic()

    def add(self, text: str) -> str | None:
        """Add a delta. Returns a completed utterance, or None."""
        if not text:
            return None
        self._buf.append(text)
        self._last_delta_at = time.monotonic()

        joined = "".join(self._buf)
        if joined.rstrip().endswith((".", "!", "?")):
            return self.flush()
        return None

    def check_idle(self) -> str | None:
        """Close the buffer if nothing has arrived for a while."""
        if not self._buf:
            return None
        idle_ms = (time.monotonic() - self._last_delta_at) * 1000
        return self.flush() if idle_ms >= self.IDLE_MS else None

    def flush(self) -> str | None:
        if not self._buf:
            return None
        text = "".join(self._buf).strip()
        self._buf.clear()
        return text or None

    @property
    def pending(self) -> str:
        return "".join(self._buf)


moshi_client = MoshiClient()
