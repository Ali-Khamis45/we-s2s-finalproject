"""Production bridge between Kyutai's real Candle wire protocol
(wss://, Opus-in-Ogg audio, tags 0-6 -- rust/protocol.md) and the plain
ws://+PCM protocol backend/app/services/moshi.py already speaks.

Replaces ml/moshi/relay.py (M1's throwaway proof-of-concept), whose
hand-rolled ctypes Ogg/Opus muxer/demuxer had three bugs: a segfault on
connection teardown, a crash on real (non-silent) speech content, and no
ERROR-tag translation on upstream connect failure. This module fixes all
three by replacing the ctypes codec layer with PyAV (bindings over
ffmpeg's libavformat/libavcodec) and adding explicit error translation.

See docs/superpowers/specs/2026-09-05-m2-moshi-bridge-design.md for the
full design record, including the three findings from the pre-implementation
spike that fixed the config values used below.
"""
from __future__ import annotations

import asyncio
import fractions
import io
import logging
import queue
import ssl
import sys
import threading

import av
import websockets
from av.audio.resampler import AudioResampler

log = logging.getLogger("moshi.bridge")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

UPSTREAM_URL = "wss://127.0.0.1:8999/api/chat"
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8998

# Kyutai's real tags (rust/protocol.md)
KT_HANDSHAKE, KT_AUDIO, KT_TEXT, KT_CONTROL, KT_METADATA, KT_ERROR, KT_PING = range(7)

# backend/app/services/moshi.py's Tag enum
OUR_HANDSHAKE, OUR_AUDIO, OUR_TEXT, OUR_CONTROL, OUR_ERROR = range(5)

SAMPLE_RATE = 24_000
CHANNELS = 1
# Must match Candle server's stream_both.rs PCM flush quantum
# (spawn_recv_loops: size_in_buf >= 24_000/25) -- 20ms silently stalls its
# decoder after ~3 frames (found in M1's bench_moshi_latency.py).
FRAME_MS = 40
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 960


class _ByteSink(io.RawIOBase):
    """Captures bytes a PyAV muxer writes, for immediate forwarding."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []

    def writable(self) -> bool:
        return True

    def write(self, b: bytes) -> int:
        self._chunks.append(bytes(b))
        return len(b)

    def drain(self) -> bytes:
        out = b"".join(self._chunks)
        self._chunks.clear()
        return out


class OpusOggEncoder:
    """Encodes a stream of raw 16-bit PCM into a continuous Ogg/Opus bitstream.

    One instance per outgoing direction of one connection. Call `feed(pcm)`
    with any amount of PCM; it buffers to 40ms frames internally (matching
    Candle's PCM flush quantum) and returns any Ogg page bytes produced by
    that call, ready to send as-is.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self._sink = _ByteSink()
        # page_duration forces a flushed Ogg page per frame fed -- without
        # it PyAV's muxer buffers roughly a second of audio before emitting
        # any bytes at all (verified by spike; see design spec finding 1).
        self._container = av.open(
            self._sink, mode="w", format="ogg", options={"page_duration": "20000"}
        )
        self._stream = self._container.add_stream(
            "libopus", rate=sample_rate, layout="mono" if channels == 1 else "stereo"
        )
        self._time_base = fractions.Fraction(1, sample_rate)
        self._pcm_buf = bytearray()
        self._pts = 0
        self._closed = False

    def feed(self, pcm: bytes) -> bytes:
        """Feed raw PCM bytes; returns any complete Ogg page bytes produced."""
        self._pcm_buf += pcm
        frame_bytes = FRAME_SAMPLES * 2 * self.channels
        while len(self._pcm_buf) >= frame_bytes:
            chunk = bytes(self._pcm_buf[:frame_bytes])
            del self._pcm_buf[:frame_bytes]

            frame = av.AudioFrame(
                format="s16", layout="mono" if self.channels == 1 else "stereo",
                samples=FRAME_SAMPLES,
            )
            frame.planes[0].update(chunk)
            frame.rate = self.sample_rate
            frame.pts = self._pts
            frame.time_base = self._time_base
            self._pts += FRAME_SAMPLES

            for packet in self._stream.encode(frame):
                self._container.mux(packet)

        return self._sink.drain()

    def close(self) -> bytes:
        """Finalize the stream (flush encoder + close container)."""
        if self._closed:
            return b""
        self._closed = True
        for packet in self._stream.encode(None):
            self._container.mux(packet)
        self._container.close()
        return self._sink.drain()


class OpusOggDecoder:
    """Decodes a continuous, arbitrarily-chunked Ogg/Opus byte stream into PCM.

    Runs its own `av.open()` + demux loop once, on a dedicated thread, over a
    BLOCKING reader whose `readinto` waits on a queue for the next chunk
    instead of ever returning 0 for "nothing available yet." This is
    required, not a style choice: PyAV 18.1.0's Ogg demuxer treats a
    `readinto` returning 0 as permanent end-of-stream, so a synchronous
    feed()-returns-bytes design that lets the reader signal "nothing right
    now" via a 0 return breaks the first time the worker catches up to the
    buffered bytes -- confirmed by spike during this task's implementation
    (see this task's Revision note above). A blocking reader in its own
    thread sidesteps the issue entirely: the demuxer's `readinto` call
    simply blocks until real bytes (or close()'s EOF sentinel) arrive.

    Call `feed(chunk)` with whatever bytes arrived in one websocket message
    to push them to the worker (non-blocking). Call `poll()` to drain
    whatever 16-bit PCM has been decoded so far, resampled to `sample_rate`
    (Opus decodes at a fixed 48kHz internally regardless of the stream's
    negotiated rate -- see design spec finding 2). Call `close()` once no
    more input is coming, to signal real EOF and join the worker thread.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self._in_q: queue.Queue[bytes | None] = queue.Queue()
        self._out_q: queue.Queue[tuple[str, object]] = queue.Queue()
        self._resampler = AudioResampler(
            format="s16", layout="mono" if channels == 1 else "stereo", rate=sample_rate
        )
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    class _BlockingReader(io.RawIOBase):
        """readinto() blocks on the queue; only returns 0 for real EOF."""

        def __init__(self, in_q: "queue.Queue[bytes | None]") -> None:
            self._q = in_q
            self._buf = b""
            self._eof = False
            self.bytes_received = 0

        def readable(self) -> bool:
            return True

        def readinto(self, dst: bytearray) -> int:
            while not self._buf:
                if self._eof:
                    return 0
                item = self._q.get()  # blocks until feed() or close() posts
                if item is None:
                    self._eof = True
                    return 0
                self._buf = item
            n = min(len(dst), len(self._buf))
            dst[:n] = self._buf[:n]
            self._buf = self._buf[n:]
            if n > 0:
                self.bytes_received += n
            return n

    def _run(self) -> None:
        reader = self._BlockingReader(self._in_q)
        try:
            container = av.open(reader, mode="r", format="ogg")
        except av.error.EOFError as exc:
            if reader.bytes_received == 0:
                # close() was called before any bytes were ever fed (e.g. a
                # connection that carried no KT_AUDIO at all) -- an empty
                # stream, not a decode failure. Treat it like EOF during an
                # in-progress demux: end quietly with no PCM produced.
                self._out_q.put(("done", None))
            else:
                # Some bytes were fed but the probe never completed (e.g. a
                # truncated/corrupt Ogg capture followed by an unclean
                # upstream disconnect) -- a real failure, not an empty
                # stream. Must be reported, not silently swallowed, or a
                # genuine decode failure never reaches the client.
                self._out_q.put(("error", exc))
            return
        except Exception as exc:
            self._out_q.put(("error", exc))
            return
        stream = container.streams.audio[0]
        try:
            for packet in container.demux(stream):
                for frame in packet.decode():
                    for out_frame in self._resampler.resample(frame):
                        pcm = bytes(out_frame.planes[0])[
                            : out_frame.samples * 2 * self.channels
                        ]
                        self._out_q.put(("pcm", pcm))
        except av.error.EOFError:
            pass
        except Exception as exc:
            self._out_q.put(("error", exc))
        container.close()
        self._out_q.put(("done", None))

    def feed(self, chunk: bytes) -> None:
        if chunk:
            self._in_q.put(chunk)

    def poll(self, timeout: float = 0.05) -> bytes:
        """Drain whatever PCM has been decoded so far.

        Blocks up to `timeout` waiting for at least one item, then drains
        anything else already queued without waiting further.
        """
        out = bytearray()
        try:
            kind, payload = self._out_q.get(timeout=timeout)
            if kind == "pcm":
                out += payload  # type: ignore[arg-type]
            elif kind == "error":
                raise payload  # type: ignore[misc]
            # "done": nothing to add, just stop waiting for more.
        except queue.Empty:
            return b""
        while True:
            try:
                kind, payload = self._out_q.get_nowait()
            except queue.Empty:
                break
            if kind == "pcm":
                out += payload  # type: ignore[arg-type]
            elif kind == "error":
                raise payload  # type: ignore[misc]
        return bytes(out)

    def close(self) -> None:
        self._in_q.put(None)
        self._thread.join(timeout=5)


async def bridge_upstream_to_client(upstream, client) -> None:
    """Translate Candle's real protocol into ours, one direction.

    OpusOggDecoder decodes on its own worker thread (see Task 2's Revision
    note -- required by a real PyAV EOF-latching limitation, not a style
    choice), so this coroutine calls `decoder.poll()` via `asyncio.to_thread`
    to drain decoded PCM without blocking the event loop while `poll()`
    waits on its internal queue.

    Any failure reading from upstream -- a decode error, or the connection
    itself dying uncleanly -- is reported to the client as an OUR_ERROR
    frame before this coroutine returns, rather than the client just seeing
    an abrupt close (the relay's third known gap).
    """
    decoder = OpusOggDecoder()

    async def drain_decoded() -> None:
        """Forward decoded PCM to the client until cancelled.

        Runs as a concurrent task alongside receive_upstream() below, since
        PCM can finish decoding well after the last KT_AUDIO frame was fed
        to the (already-buffering) worker thread -- see the closing
        sequence below for why decoder.close() must complete before this
        task is cancelled.
        """
        while True:
            pcm = await asyncio.to_thread(decoder.poll, 0.05)
            if pcm:
                await client.send(bytes([OUR_AUDIO]) + pcm)

    async def receive_upstream() -> None:
        async for frame in upstream:
            if not frame:
                continue
            tag, payload = frame[0], frame[1:]
            log.debug("upstream->client: tag=%s payload_len=%d", tag, len(payload))
            if tag == KT_AUDIO:
                decoder.feed(payload)
            elif tag == KT_TEXT:
                await client.send(bytes([OUR_TEXT]) + payload)
            elif tag == KT_ERROR:
                await client.send(bytes([OUR_ERROR]) + payload)
            # KT_HANDSHAKE, KT_CONTROL, KT_METADATA, KT_PING: no equivalent
            # our client models: drop rather than guess at a mapping.

    try:
        drain_task = asyncio.create_task(drain_decoded())
        try:
            await receive_upstream()
        finally:
            # Signal real EOF and let the worker thread finish emitting any
            # PCM still in flight BEFORE cancelling the drain task -- verified
            # by spike that cancelling immediately drops 100% of a short
            # real-audio test case's output, since decode can finish shortly
            # after the last KT_AUDIO frame is fed.
            await asyncio.to_thread(decoder.close)
            drain_task.cancel()
            # drain_task can be mid-poll() when cancelled, and poll() re-raises
            # any queued decode error (e.g. an Ogg stream that received some
            # bytes but never completed its probe). What we do with that error
            # depends on why this finally block is running:
            #   - If receive_upstream() above is already propagating a
            #     CancelledError (the normal shutdown path -- e.g. the other
            #     bridge direction finished first and handler() cancelled us),
            #     that decode error is not actionable and must not replace the
            #     real CancelledError -- discard it.
            #   - If nothing is propagating (receive_upstream() returned
            #     normally), a real decode error from drain_task IS actionable
            #     and must reach the client as OUR_ERROR -- this is one of the
            #     three things this module exists to fix over relay.py, and an
            #     earlier fix attempt broke it by suppressing unconditionally.
            cancelling = sys.exc_info()[0] is not None and issubclass(
                sys.exc_info()[0], asyncio.CancelledError
            )
            try:
                await drain_task
            except asyncio.CancelledError:
                pass  # drain_task's own cancellation is always expected here
            except Exception:
                if not cancelling:
                    raise
    except asyncio.CancelledError:
        raise  # never translate a cancel into an ERROR frame
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}".encode("utf-8", errors="replace")
        try:
            await client.send(bytes([OUR_ERROR]) + detail)
        except Exception:
            pass  # client already gone; nothing more to do


async def bridge_client_to_upstream(client, upstream) -> None:
    encoder = OpusOggEncoder()
    try:
        async for frame in client:
            if not frame:
                continue
            tag, payload = frame[0], frame[1:]
            log.debug("client->upstream: tag=%s payload_len=%d", tag, len(payload))
            if tag == OUR_AUDIO:
                ogg_bytes = encoder.feed(payload)
                if ogg_bytes:
                    await upstream.send(bytes([KT_AUDIO]) + ogg_bytes)
            # OUR_HANDSHAKE/OUR_CONTROL carry no payload our client sends today;
            # extend here if a later task starts sending them.
    finally:
        tail = encoder.close()
        if tail:
            try:
                await upstream.send(bytes([KT_AUDIO]) + tail)
            except Exception as exc:
                log.debug("  tail send failed: %r", exc)


async def handler(client) -> None:
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE  # local self-signed dev cert only
    log.info("client connected, dialing upstream %s", UPSTREAM_URL)
    try:
        # No keepalive on the upstream leg. `websockets` defaults to a 20s ping
        # timeout, and Moshi is GPU-bound: on an 8GB card with ~300MB of
        # headroom it routinely stalls longer than that mid-session, so the
        # library was killing a working connection and reporting
        # "1011 keepalive ping timeout" — which surfaced as "the live coach
        # dropped out" seconds into a conversation.
        #
        # A stalled Moshi is a real problem, but it is not a dead socket, and
        # tearing down the session guarantees the stall becomes a failure. The
        # session already ends when either pump task finishes, so a genuinely
        # dead upstream is still detected.
        async with websockets.connect(
            UPSTREAM_URL,
            ssl=ssl_ctx,
            max_size=None,
            ping_interval=None,
            ping_timeout=None,
            close_timeout=5,
        ) as upstream:
            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(bridge_upstream_to_client(upstream, client)),
                    asyncio.create_task(bridge_client_to_upstream(client, upstream)),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc:
                    log.info("bridge task ended: %r", exc)
    except Exception as exc:
        detail = f"could not reach Moshi: {type(exc).__name__}: {exc}"
        log.info(detail)
        try:
            await client.send(bytes([OUR_ERROR]) + detail.encode("utf-8", errors="replace"))
        except Exception:
            pass
    log.info("client disconnected")


async def main() -> None:
    # Same reasoning as the upstream leg: the backend sits on this side, and a
    # ping timeout here would tear down a session that is merely waiting on a
    # busy GPU rather than one that has actually gone away.
    async with websockets.serve(
        handler,
        LISTEN_HOST,
        LISTEN_PORT,
        max_size=None,
        ping_interval=None,
        ping_timeout=None,
        close_timeout=5,
    ):
        print(f"bridge listening on ws://{LISTEN_HOST}:{LISTEN_PORT}/api/chat")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
