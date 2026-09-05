# ml/moshi/tests/test_bridge_protocol.py
"""Protocol-translation tests for ml/moshi/bridge.py, using fake websocket
doubles. No GPU or Candle server needed."""
from __future__ import annotations

import asyncio

import pytest

from ml.moshi.bridge import (
    KT_AUDIO,
    KT_ERROR,
    KT_TEXT,
    OUR_AUDIO,
    OUR_ERROR,
    OUR_TEXT,
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


@pytest.mark.asyncio
async def test_cancellation_during_incomplete_stream_is_not_swallowed() -> None:
    """Final-review finding: handler() cancels whichever bridge direction is
    still running when the other finishes first -- the NORMAL disconnect
    path, not an edge case. If that cancellation lands while decoder.close()
    is tearing down an incomplete Ogg probe (bytes fed, container never
    opened), the worker thread queues a real decode error. That error must
    not be allowed to shadow the CancelledError: this coroutine must let
    CancelledError propagate, and must never turn a cancel into an OUR_ERROR
    frame sent to the client."""

    class TricklingSocket:
        """Yields a few bytes of a valid-but-incomplete Ogg stream, slowly,
        so there's a window to cancel mid-stream before the container ever
        finishes probing."""

        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            # "OggS" is a real Ogg capture pattern but far too little data
            # for PyAV to ever successfully open the container -- feed()
            # sees bytes, but the probe never completes.
            for chunk in (b"OggS", b"\x00\x02\x00\x00"):
                await asyncio.sleep(0.05)
                yield bytes([KT_AUDIO]) + chunk

    upstream = TricklingSocket()
    client = FakeSocket([])

    task = asyncio.create_task(bridge_upstream_to_client(upstream, client))
    await asyncio.sleep(0.06)  # let at least one chunk be fed
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert not any(
        frame and frame[0] == OUR_ERROR for frame in client.sent
    ), "cancellation must not produce a spurious OUR_ERROR frame"


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
