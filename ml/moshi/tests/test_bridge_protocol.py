# ml/moshi/tests/test_bridge_protocol.py
"""Protocol-translation tests for ml/moshi/bridge.py, using fake websocket
doubles. No GPU or Candle server needed."""
from __future__ import annotations

import asyncio
import threading

import pytest

import ml.moshi.bridge as bridge_mod
from ml.moshi.bridge import (
    KT_AUDIO,
    KT_ERROR,
    KT_TEXT,
    OUR_AUDIO,
    OUR_ERROR,
    OUR_TEXT,
    OpusOggDecoder,
    bridge_upstream_to_client,
)


class FakeSocket:
    """A minimal async-iterable + .send() double standing in for a
    websockets connection object."""

    def __init__(self, incoming: list[bytes]) -> None:
        self._incoming = incoming
        self.sent: list[bytes] = []
        self.closed = False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for frame in self._incoming:
            yield frame

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


@pytest.mark.asyncio
async def test_text_tag_translated_and_forwarded() -> None:
    upstream = FakeSocket([bytes([KT_TEXT]) + b"hello"])
    client = FakeSocket([])
    await bridge_upstream_to_client(upstream, client)
    assert client.sent == [bytes([OUR_TEXT]) + b"hello"]


@pytest.mark.asyncio
async def test_upstream_error_tag_translated() -> None:
    upstream = FakeSocket([bytes([KT_ERROR]) + b"model crashed"])
    client = FakeSocket([])
    await bridge_upstream_to_client(upstream, client)
    assert client.sent == [bytes([OUR_ERROR]) + b"model crashed"]


def test_decoder_poll_raises_after_close_on_incomplete_ogg_probe() -> None:
    """Establishes the precondition deterministically, no timing needed:
    feed an OpusOggDecoder a few bytes of an incomplete Ogg capture (enough
    that bytes_received > 0, never enough to complete a valid probe), call
    close() (which blocks until the worker thread has fully torn down and
    queued its result), then verify poll() raises the queued decode error.
    This is the exact condition bridge_upstream_to_client's drain_task can
    be cancelled in the middle of."""
    decoder = OpusOggDecoder()
    # "OggS" is a real Ogg capture pattern but far too little data for PyAV
    # to ever successfully open the container -- feed() sees bytes, but the
    # probe never completes.
    decoder.feed(b"OggS")
    decoder.feed(b"\x00\x02\x00\x00")
    decoder.close()  # blocks until the worker thread queues ("error", ...)

    with pytest.raises(Exception):
        decoder.poll(0)


@pytest.mark.asyncio
async def test_cancellation_during_incomplete_stream_is_not_swallowed() -> None:
    """Final-review finding: handler() cancels whichever bridge direction is
    still running when the other finishes first -- the NORMAL disconnect
    path, not an edge case. If that cancellation lands while drain_task is
    mid-poll() and poll() re-raises a queued decode error from an incomplete
    Ogg probe (bytes fed, container never opened), that error must not be
    allowed to shadow the CancelledError: this coroutine must let
    CancelledError propagate, and must never turn a cancel into an OUR_ERROR
    frame sent to the client.

    Deterministic by construction, not timing-based: the fake upstream
    yields one KT_AUDIO frame carrying the same incomplete-Ogg bytes proven
    above to make poll() raise, then blocks forever on an Event that never
    fires. A single `await asyncio.sleep(0)` lets the event loop run once so
    the task starts, feeds the decoder, and parks on the never-firing wait
    -- not a timing race, just "let the task reach its first real await
    point" -- before the task is cancelled. Because decoder.close() (called
    in bridge_upstream_to_client's finally block) blocks until the worker
    thread has queued its ("error", ...) item, drain_task's next poll() call
    is guaranteed to raise -- no sleep-based race against the worker thread
    is needed."""

    class BlocksForeverSocket:
        """Yields one incomplete-Ogg KT_AUDIO frame, then hangs forever
        (rather than completing or sleeping), so cancellation always lands
        while the coroutine is parked waiting for the "next" frame."""

        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            yield bytes([KT_AUDIO]) + b"OggS\x00\x02\x00\x00"
            await asyncio.Event().wait()  # never set: blocks forever

    upstream = BlocksForeverSocket()
    client = FakeSocket([])

    task = asyncio.create_task(bridge_upstream_to_client(upstream, client))
    await asyncio.sleep(0)  # let the task start and reach its first await
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert not any(
        frame and frame[0] == OUR_ERROR for frame in client.sent
    ), "cancellation must not produce a spurious OUR_ERROR frame"


@pytest.mark.asyncio
async def test_decode_error_on_normal_end_is_not_swallowed(monkeypatch) -> None:
    """Regression test for the fix in this task: a3aef6c's unconditional
    `contextlib.suppress(BaseException)` around `await drain_task` fixed
    cancellation but also silently swallowed genuine decode errors on the
    completely normal, non-cancelled path -- independently measured 8/10 ->
    0/10 OUR_ERROR frames for a corrupt stream that ends without any
    cancellation. That directly undoes one of the three things this module
    exists to fix over relay.py (surfacing real failures as OUR_ERROR
    instead of hiding them).

    Why this needs a controlled fake decoder rather than the real
    OpusOggDecoder: `decoder.close()` and `decoder.poll()` both run via
    `asyncio.to_thread`, and cancelling a task while it's suspended awaiting
    an in-flight `asyncio.to_thread` call delivers CancelledError to that
    task IMMEDIATELY -- it does not wait for the underlying thread-pool call
    to finish, no matter how close that call is to completing. With the
    real decoder, whether drain_task's current poll() call has already
    raised (so `await drain_task` sees the real error) or is still in
    flight (so `await drain_task` sees CancelledError instead, losing the
    error) depends on real OS thread-pool scheduling against
    bridge_upstream_to_client's finally block -- measured flaky across
    several designs (fixed sleeps, wait-until-idle-then-signal, stashing
    the error to replay synchronously).

    Instead, this test patches `bridge.OpusOggDecoder` with a fake whose
    poll() blocks on a threading.Event (`release_poll`) until the test sets
    it, then raises a fixed error -- run via the same asyncio.to_thread the
    real code uses, so the finally block's actual cancellation logic is
    exercised for real, but the *timing* of when the executor thread
    finishes is under this test's control instead of the real decoder's.
    The sequence: let drain_task start and block inside its first poll()
    call (a real await point, not a sleep-based guess); set `release_poll`
    so that thread call proceeds to raise; then give the executor thread a
    brief real interval to run and marshal that exception back into the
    event loop via `asyncio.to_thread`'s internal callback -- confirmed
    reliable (100/100 in isolation) because setting the event and calling
    cancel() are ordered by an explicit intervening sleep, not left to
    chance. Only then does receive_upstream() return, triggering
    bridge_upstream_to_client's finally block, `decoder.close()` (a no-op
    for the fake), and `drain_task.cancel()` -- landing on a task whose
    to_thread future has already completed with the real decode error, so
    cancel() is a no-op and `await drain_task` re-raises that error."""

    class DecodeBoom(Exception):
        pass

    release_poll = threading.Event()
    poll_started = threading.Event()

    class FakeDecoder:
        def feed(self, chunk: bytes) -> None:
            pass

        def poll(self, timeout: float = 0.05) -> bytes:
            poll_started.set()
            release_poll.wait(5)
            raise DecodeBoom("simulated decode failure, no cancellation involved")

        def close(self) -> None:
            pass

    monkeypatch.setattr(bridge_mod, "OpusOggDecoder", FakeDecoder)

    class EndsNormallySocket:
        """Yields one incomplete-Ogg KT_AUDIO frame, then waits for
        drain_task's poll() call to actually raise before ending the
        stream normally (no exception, no cancellation in this test)."""

        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            yield bytes([KT_AUDIO]) + b"OggS\x00\x02\x00\x00"
            # Wait for drain_task's task to actually reach its blocking
            # poll() call (a real await point -- not a guess) ...
            await asyncio.to_thread(poll_started.wait, 5)
            # ... then let that call raise ...
            release_poll.set()
            # ... and give its executor thread a moment to actually finish
            # and marshal the exception back into drain_task before this
            # generator returns and bridge_upstream_to_client's finally
            # block calls drain_task.cancel() -- see docstring above.
            await asyncio.sleep(0.05)

    upstream = EndsNormallySocket()
    client = FakeSocket([])

    await bridge_upstream_to_client(upstream, client)

    assert any(
        frame and frame[0] == OUR_ERROR for frame in client.sent
    ), "a genuine decode error on the normal (non-cancelled) path must reach the client as OUR_ERROR"


@pytest.mark.asyncio
async def test_upstream_disconnect_sends_error_frame() -> None:
    """The relay's third gap: no ERROR translation when the upstream
    connection itself dies uncleanly. bridge_upstream_to_client must catch
    that and tell the client, rather than the client just seeing a close."""

    class DyingSocket:
        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            yield bytes([KT_AUDIO]) + b"\x00\x00"
            raise ConnectionResetError("upstream closed uncleanly")

    upstream = DyingSocket()
    client = FakeSocket([])
    await bridge_upstream_to_client(upstream, client)
    assert client.sent, "expected at least one frame sent to client"
    last = client.sent[-1]
    assert last[0] == OUR_ERROR
    assert b"upstream closed uncleanly" in last[1:] or b"ConnectionResetError" in last[1:]
