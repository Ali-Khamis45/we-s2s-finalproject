"""Protocol relay bridging Kyutai's real moshi-backend wire protocol
(rust/protocol.md: Opus-in-Ogg audio, wss://, tags 0-6) to the plain-PCM,
plain-ws:// tag scheme backend/app/services/moshi.py already speaks.

This is a permanent part of the integration, not a throwaway shim — M2
(Track M) builds the production version of this bridge; this one exists
to prove the bridge is possible and to unblock M1's latency measurement.

Codec note: the brief's original sketch assumed PyOgg exposed high-level
``OpusEncoder``/``OpusDecoder``/``OggOpusWriter``/``OggOpusReader`` classes.
The installed version (0.6.14a1) exposes none of those at the Python level —
only ``OpusFile``/``OpusFileStream`` (path-based, whole-file decode) plus the
raw ctypes bindings in ``pyogg.opus`` and ``pyogg.ogg``. Kyutai's protocol
streams a *continuous* Ogg/Opus bitstream split arbitrarily across websocket
messages (see protocol.md: "Binary data for the ogg frames containing opus
encoded audio"), which rules out whole-file APIs anyway — a real streaming
muxer/demuxer with persistent encoder/decoder state is required. This module
hand-rolls that streaming Ogg/Opus codec directly on top of ``pyogg.ogg`` and
``pyogg.opus``'s raw ctypes bindings (libogg + libopus), one encoder/decoder
pair per connection.
"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import random
import ssl

import websockets
from pyogg import ogg, opus

log = logging.getLogger("moshi.relay")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# NOTE: pyogg.ogg.ogg_sync_buffer declares its ctypes restype as c_char_p,
# which makes ctypes auto-marshal the return value into an immutable Python
# bytes object instead of a raw pointer. memmove-ing into that segfaults (the
# whole point of ogg_sync_buffer is to hand back a writable staging area
# inside libogg's internal buffer). Re-declare the restype as c_void_p here
# so the returned address can be written into safely.
ogg.libogg.ogg_sync_buffer.restype = ctypes.c_void_p

UPSTREAM_URL = "wss://127.0.0.1:8999/api/chat"
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8998

# Kyutai's real tags (rust/protocol.md)
KT_HANDSHAKE, KT_AUDIO, KT_TEXT, KT_CONTROL, KT_METADATA, KT_ERROR, KT_PING = range(7)

# backend/app/services/moshi.py's Tag enum
OUR_HANDSHAKE, OUR_AUDIO, OUR_TEXT, OUR_CONTROL, OUR_ERROR = range(5)

SAMPLE_RATE = 24_000
CHANNELS = 1
# Opus requires one of {2.5, 5, 10, 20, 40, 60} ms per frame. 20 ms is the
# usual real-time-audio default and keeps latency low while amortizing
# per-frame overhead.
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480 samples @ 24kHz/20ms
MAX_OPUS_PACKET = 4000  # generous upper bound for one compressed Opus frame


def _check(ret: int, what: str) -> int:
    if ret < 0:
        raise RuntimeError(f"{what} failed: {opus.opus_strerror(ret).decode(errors='replace')}")
    return ret


class OpusOggEncoder:
    """Encodes a stream of raw 16-bit PCM into a continuous Ogg/Opus bitstream.

    One instance per outgoing direction of one connection. Call `feed(pcm)`
    with any amount of PCM; it buffers to 20ms frames internally and returns
    the Ogg page bytes (if any) produced by that call, ready to send as-is.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self._pcm_buf = bytearray()
        self._packetno = 0
        self._granulepos = 0
        self._started = False

        err = ctypes.c_int()
        self._enc = opus.opus_encoder_create(
            sample_rate, channels, opus.OPUS_APPLICATION_AUDIO, ctypes.byref(err)
        )
        _check(err.value, "opus_encoder_create")

        self._serialno = random.randint(0, 0x7FFFFFFF)
        self._os = ogg.ogg_stream_state()
        if ogg.ogg_stream_init(ctypes.byref(self._os), self._serialno) != 0:
            raise RuntimeError("ogg_stream_init failed")

    def _pageout_all(self) -> bytes:
        out = bytearray()
        page = ogg.ogg_page()
        while ogg.ogg_stream_pageout(ctypes.byref(self._os), ctypes.byref(page)) != 0:
            out += ctypes.string_at(page.header, page.header_len)
            out += ctypes.string_at(page.body, page.body_len)
        return bytes(out)

    def _flush_all(self) -> bytes:
        out = bytearray()
        page = ogg.ogg_page()
        while ogg.ogg_stream_flush(ctypes.byref(self._os), ctypes.byref(page)) != 0:
            out += ctypes.string_at(page.header, page.header_len)
            out += ctypes.string_at(page.body, page.body_len)
        return bytes(out)

    def _packetin(self, data: bytes, *, bos: bool, eos: bool, granulepos: int) -> None:
        buf = ctypes.create_string_buffer(data, len(data))
        pkt = ogg.ogg_packet()
        pkt.packet = ctypes.cast(buf, ogg.c_uchar_p)
        pkt.bytes = len(data)
        pkt.b_o_s = 1 if bos else 0
        pkt.e_o_s = 1 if eos else 0
        pkt.granulepos = granulepos
        pkt.packetno = self._packetno
        self._packetno += 1
        ogg.ogg_stream_packetin(ctypes.byref(self._os), ctypes.byref(pkt))
        # Keep the backing buffer alive until libogg has copied it in.
        self._last_packet_buf = buf

    def _start_stream(self) -> bytes:
        """Emit the RFC 7845 OpusHead/OpusTags header pages."""
        out = bytearray()

        # OpusHead (identification header).
        head = bytearray()
        head += b"OpusHead"
        head += bytes([1])  # version
        head += bytes([self.channels])
        head += (0).to_bytes(2, "little")  # pre-skip
        head += self.sample_rate.to_bytes(4, "little")  # input sample rate
        head += (0).to_bytes(2, "little")  # output gain
        head += bytes([0])  # channel mapping family
        self._packetin(bytes(head), bos=True, eos=False, granulepos=0)
        out += self._flush_all()  # header page must be flushed on its own

        # OpusTags (comment header).
        vendor = b"we-s2s-relay"
        tags = bytearray()
        tags += b"OpusTags"
        tags += len(vendor).to_bytes(4, "little")
        tags += vendor
        tags += (0).to_bytes(4, "little")  # zero user comments
        self._packetin(bytes(tags), bos=False, eos=False, granulepos=0)
        out += self._flush_all()

        self._started = True
        return bytes(out)

    def feed(self, pcm: bytes) -> bytes:
        """Feed raw PCM bytes; returns any complete Ogg page bytes produced."""
        out = bytearray()
        if not self._started:
            out += self._start_stream()

        self._pcm_buf += pcm
        frame_bytes = FRAME_SAMPLES * 2 * self.channels
        while len(self._pcm_buf) >= frame_bytes:
            chunk = bytes(self._pcm_buf[:frame_bytes])
            del self._pcm_buf[:frame_bytes]

            pcm16 = (ctypes.c_int16 * (FRAME_SAMPLES * self.channels)).from_buffer_copy(chunk)
            data_buf = (ctypes.c_ubyte * MAX_OPUS_PACKET)()
            n = opus.opus_encode(
                self._enc,
                ctypes.cast(pcm16, opus.opus_int16_p),
                FRAME_SAMPLES,
                data_buf,
                MAX_OPUS_PACKET,
            )
            _check(n, "opus_encode")

            self._granulepos += FRAME_SAMPLES
            self._packetin(bytes(data_buf[:n]), bos=False, eos=False, granulepos=self._granulepos)
            # Force a page out per Opus packet (ogg_stream_flush, not
            # ogg_stream_pageout) so each ~20ms frame reaches the wire
            # immediately. ogg_stream_pageout only emits a page once its
            # internal lacing buffer is "full enough", which — since one
            # compressed Opus frame is only tens of bytes — silently batches
            # dozens of frames (roughly a second of audio) before flushing.
            # That defeats real-time streaming and is why Kyutai's own Rust
            # server (stream_both.rs) uses PacketWriteEndInfo::EndPage on
            # every packet it writes.
            out += self._flush_all()

        return bytes(out)

    def close(self) -> bytes:
        """Finalize the stream (empty final packet + EOS page)."""
        out = bytearray()
        if self._started:
            self._packetin(b"", bos=False, eos=True, granulepos=self._granulepos)
            out += self._pageout_all()
            out += self._flush_all()
        ogg.ogg_stream_clear(ctypes.byref(self._os))
        opus.opus_encoder_destroy(self._enc)
        return bytes(out)


class OpusOggDecoder:
    """Decodes a continuous, arbitrarily-chunked Ogg/Opus byte stream into PCM.

    Call `feed(chunk)` with whatever bytes arrived in one websocket message;
    it returns all raw 16-bit PCM decoded from any complete audio packets
    found so far.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS) -> None:
        self.sample_rate = sample_rate
        self.channels = channels

        self._oy = ogg.ogg_sync_state()
        ogg.ogg_sync_init(ctypes.byref(self._oy))

        self._os: ogg.ogg_stream_state | None = None
        self._serialno: int | None = None
        self._dec = None
        self._headers_seen = 0  # OpusHead + OpusTags

        self._pcm_frame = (ctypes.c_int16 * (5760 * channels))()  # max opus frame (120ms@48k)

    def _ensure_stream(self, page: "ogg.ogg_page") -> None:
        serialno = ogg.ogg_page_serialno(ctypes.byref(page))
        if self._os is None:
            self._serialno = serialno
            self._os = ogg.ogg_stream_state()
            if ogg.ogg_stream_init(ctypes.byref(self._os), serialno) != 0:
                raise RuntimeError("ogg_stream_init failed")

    def _ensure_decoder(self) -> None:
        if self._dec is None:
            err = ctypes.c_int()
            self._dec = opus.opus_decoder_create(self.sample_rate, self.channels, ctypes.byref(err))
            _check(err.value, "opus_decoder_create")

    def feed(self, chunk: bytes) -> bytes:
        if not chunk:
            return b""

        buf_ptr = ogg.ogg_sync_buffer(ctypes.byref(self._oy), len(chunk))
        ctypes.memmove(buf_ptr, chunk, len(chunk))
        ogg.ogg_sync_wrote(ctypes.byref(self._oy), len(chunk))

        pcm_out = bytearray()
        page = ogg.ogg_page()
        while ogg.ogg_sync_pageout(ctypes.byref(self._oy), ctypes.byref(page)) == 1:
            self._ensure_stream(page)
            # NOTE: this PyOgg version's ogg_stream_pagein/ogg_stream_packetout
            # Python wrappers reference an out-of-scope variable name (a
            # copy-paste bug — see OpusOggDecoder.close), so call libogg
            # directly rather than through pyogg.ogg's wrapper functions.
            if ogg.libogg.ogg_stream_pagein(ctypes.byref(self._os), ctypes.byref(page)) != 0:
                continue

            pkt = ogg.ogg_packet()
            while ogg.libogg.ogg_stream_packetout(ctypes.byref(self._os), ctypes.byref(pkt)) == 1:
                data = ctypes.string_at(pkt.packet, pkt.bytes)
                if data[:8] == b"OpusHead" or data[:8] == b"OpusTags":
                    self._headers_seen += 1
                    continue
                if not data:
                    continue

                self._ensure_decoder()
                data_buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
                n = opus.opus_decode(
                    self._dec,
                    data_buf,
                    len(data),
                    ctypes.cast(self._pcm_frame, opus.opus_int16_p),
                    len(self._pcm_frame) // self.channels,
                    0,
                )
                if n < 0:
                    log.warning("opus_decode error: %s", opus.opus_strerror(n))
                    continue
                pcm_out += ctypes.string_at(self._pcm_frame, n * self.channels * 2)

        return bytes(pcm_out)

    def close(self) -> None:
        if self._os is not None:
            ogg.ogg_stream_clear(ctypes.byref(self._os))
        # NOTE: this PyOgg version (0.6.14a1) has a copy-paste bug where the
        # ogg_sync_clear ctypes signature is registered but no Python wrapper
        # function is defined for it (the wrapper is accidentally named
        # oggpack_writeinit instead) — call the raw libogg binding directly.
        ogg.libogg.ogg_sync_clear(ctypes.byref(self._oy))
        if self._dec is not None:
            opus.opus_decoder_destroy(self._dec)


async def bridge_upstream_to_client(upstream, client) -> None:
    decoder = OpusOggDecoder()
    try:
        async for frame in upstream:
            if not frame:
                continue
            tag, payload = frame[0], frame[1:]
            log.debug("upstream->client: tag=%s payload_len=%d", tag, len(payload))
            if tag == KT_AUDIO:
                pcm = decoder.feed(payload)
                if pcm:
                    await client.send(bytes([OUR_AUDIO]) + pcm)
            elif tag == KT_TEXT:
                await client.send(bytes([OUR_TEXT]) + payload)
            elif tag == KT_ERROR:
                await client.send(bytes([OUR_ERROR]) + payload)
            # KT_HANDSHAKE, KT_CONTROL, KT_METADATA, KT_PING: no equivalent
            # our client models: drop rather than guess at a mapping.
    finally:
        decoder.close()


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
                    log.debug("  forwarding %d ogg bytes upstream", len(ogg_bytes))
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
    async with websockets.connect(UPSTREAM_URL, ssl=ssl_ctx, max_size=None) as upstream:
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
    log.info("client disconnected")


async def main() -> None:
    async with websockets.serve(handler, LISTEN_HOST, LISTEN_PORT, max_size=None):
        print(f"relay listening on ws://{LISTEN_HOST}:{LISTEN_PORT}/api/chat")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
