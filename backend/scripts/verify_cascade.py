"""Full cascade verification: speech in, grounded spoken coaching out.

Drives the real FastAPI app against a real served LLM. This is the first time
the whole Knowledge Mode path runs end to end — every earlier check stopped at
a component boundary.

Under test:
  1. Text turn:      retrieval -> prompt -> LLM -> reply with citations
  2. Groundedness:   an out-of-corpus question is refused, not invented
  3. Audio turn:     Whisper -> analyzer -> prompt -> LLM, with the acoustic
                     context demonstrably reaching the model
  4. Streaming:      the SSE parser in llm_service.stream(), which no test has
                     ever exercised — deltas arrive incrementally over the
                     WebSocket rather than in one lump
  5. Latency:        per-stage timings, to compare against the ~200 ms the live
                     path is expected to hit

The model here is Qwen2.5-0.5B, not the 3B the project will ship. It is far too
small to coach anyone well, and the reply QUALITY below means nothing. What it
proves is that the pipeline is wired correctly and the plumbing carries the
right payloads.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import wave
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="scc-cascade-"))
CORPUS = TMP / "corpus"
CORPUS.mkdir(parents=True)

os.environ["SCC_DATA_DIR"] = str(TMP)
os.environ["SCC_CORPUS_DIR"] = str(CORPUS)
os.environ["SCC_CHROMA_DIR"] = str(TMP / "chroma")
os.environ["SCC_DATABASE_URL"] = f"sqlite+aiosqlite:///{(TMP / 'c.db').as_posix()}"
os.environ["SCC_MOSHI_ENABLED"] = "false"
os.environ["SCC_LLM_MODEL"] = "qwen-test"
os.environ["SCC_LLM_MAX_TOKENS"] = "150"

WAV = Path(sys.argv[1]) if len(sys.argv) > 1 else None

(CORPUS / "pacing.md").write_text(
    """# Pacing and Rate Control

Speaking rate is one of the few things a speaker can adjust consciously in the
moment. Most people speed up under pressure, and a faster rate leaves less time
to plan the next phrase.

The short-first-sentence habit is a reliable drill. Deliberately make the
opening sentence of any answer short. It buys planning time for the sentences
that follow and sets a slower baseline for the whole turn.

Pausing deliberately at phrase boundaries helps too. A planned pause reads as
confidence to a listener, while an unplanned one reads as hesitation, even when
the two are exactly the same length.
""",
    encoding="utf-8",
)

(CORPUS / "fillers.md").write_text(
    """# Filler Words

Filler words such as "um" and "uh" are normal features of spontaneous speech.
Everyone produces them, and removing them entirely is neither realistic nor
necessary.

If reducing fillers is the goal, the effective substitution is silence.
Replacing a filler with a brief pause sounds more composed to a listener and is
easier to do than suppressing the filler outright.
""",
    encoding="utf-8",
)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{('  — ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def section(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


def timings_of(body: dict) -> str:
    return "  ".join(f"{t['stage']}={t['ms']:.0f}ms" for t in body.get("timings", []))


def main() -> int:
    with TestClient(app) as client:
        section("PRE-FLIGHT")
        status = client.get("/api/status").json()
        print(f"  llm_reachable  {status['llm_reachable']}")
        print(f"  prompt         {status['prompt_version']}")
        check("coaching model is reachable", status["llm_reachable"])
        if not status["llm_reachable"]:
            print("\n  llama server not up — start it first. Aborting.")
            return 1

        ing = client.post("/api/corpus/ingest").json()
        print(f"  corpus         {ing['files']} files, {ing['chunks']} chunks")
        check("corpus ingested", ing["chunks"] > 0)

        # ---- 1. grounded text turn ----
        section("1. TEXT TURN THROUGH THE CASCADE")
        q = "How do I stop speaking too fast when I get nervous?"
        print(f"  user: {q!r}\n")
        r = client.post("/api/chat", json={"message": q})
        check("request succeeded", r.status_code == 200, f"HTTP {r.status_code}")
        if r.status_code != 200:
            print(r.text)
            return 1

        body = r.json()
        session_id = body["session_id"]
        print(f"  coach: {body['reply']}\n")
        print(f"  grounded   {body['grounded']}")
        print(f"  citations  {[c['source'] for c in body['citations']]}")
        print(f"  timings    {timings_of(body)}")
        print(f"  total      {body['total_ms']:.0f} ms")
        print(f"  variant    {body['llm_variant']}")

        check("a reply was generated", len(body["reply"]) > 20)
        check("answer is grounded", body["grounded"])
        check("citations attached", len(body["citations"]) > 0)
        check(
            "retrieval reached the pacing document",
            any("pacing" in c["source"] for c in body["citations"]),
        )
        check("per-stage timings recorded", len(body["timings"]) >= 2)

        # ---- 2. groundedness gate ----
        section("2. OUT-OF-CORPUS QUESTION")
        q2 = "How do I change the oil filter in a diesel engine?"
        print(f"  user: {q2!r}\n")
        b2 = client.post("/api/chat", json={"message": q2, "session_id": session_id}).json()
        print(f"  coach: {b2['reply']}\n")
        print(f"  grounded {b2['grounded']}   citations {len(b2['citations'])}")
        check(
            "no citations fabricated for an out-of-corpus question",
            len(b2["citations"]) == 0 and not b2["grounded"],
        )

        # ---- 3. spoken turn ----
        if WAV and WAV.exists():
            section("3. SPOKEN TURN  (Whisper -> analyzer -> RAG -> LLM)")
            with wave.open(str(WAV), "rb") as wf:
                dur = wf.getnframes() / wf.getframerate()
            print(f"  input: {WAV.name}  ({dur:.2f}s, contains a spliced 1400 ms block)\n")

            with WAV.open("rb") as fh:
                r3 = client.post(
                    "/api/chat/audio",
                    files={"audio": (WAV.name, fh, "audio/wav")},
                    data={"session_id": session_id},
                )
            check("spoken turn succeeded", r3.status_code == 200, f"HTTP {r3.status_code}")
            if r3.status_code == 200:
                b3 = r3.json()
                ac = b3.get("acoustic") or {}
                print(f"  coach: {b3['reply']}\n")
                print(f"  events      {ac.get('event_counts')}")
                print(f"  load        {ac.get('fluency_load')}")
                print(f"  timings     {timings_of(b3)}")
                print(f"  total       {b3['total_ms']:.0f} ms")
                check("acoustic profile attached to the turn", bool(ac.get("analyzed")))
                check(
                    "the block was detected on the real request path",
                    "block" in (ac.get("event_counts") or {}),
                    str(ac.get("event_counts")),
                )
                check("a spoken reply was generated", len(b3["reply"]) > 10)
        else:
            print("\n  (no WAV supplied — skipping the spoken turn)")

        # ---- 4. streaming over the websocket ----
        section("4. STREAMING  (exercises the SSE parser)")
        deltas: list[str] = []
        done_frame: dict | None = None
        with client.websocket_connect("/ws/knowledge") as ws:
            ready = ws.receive_json()
            check("socket ready", ready["type"] == "ready")
            ws.send_json({"type": "text", "data": {"message": "Any tips for filler words?"}})
            for _ in range(400):
                frame = ws.receive_json()
                if frame["type"] == "transcript" and frame["data"]["role"] == "coach":
                    if not frame["data"].get("final"):
                        deltas.append(frame["data"]["text"])
                elif frame["type"] == "citations":
                    print(f"  citations frame: {len(frame['data']['citations'])} sources")
                elif frame["type"] == "done":
                    done_frame = frame["data"]
                    break
                elif frame["type"] == "error":
                    print(f"  error: {frame['data']}")
                    break
            ws.send_json({"type": "stop"})

        print(f"\n  coach: {''.join(deltas).strip()}\n")
        print(f"  deltas received  {len(deltas)}")
        if done_frame:
            stage_line = "  ".join(
                "{}={:.0f}ms".format(t["stage"], t["ms"]) for t in done_frame["timings"]
            )
            print(f"  timings          {stage_line}")
            print(f"  total            {done_frame['total_ms']:.0f} ms")

        check(
            "reply streamed incrementally, not in one lump",
            len(deltas) > 5,
            f"{len(deltas)} deltas",
        )
        check("done frame received", done_frame is not None)
        if done_frame:
            check(
                "time-to-first-token measured",
                any(t["stage"] == "llm_ttft" for t in done_frame["timings"]),
            )

        # ---- 5. history ----
        section("5. UNIFIED CONVERSATION HISTORY")
        detail = client.get(f"/api/sessions/{session_id}").json()
        print(f"  turns  {detail['turn_count']}")
        print(f"  title  {detail['title']!r}")
        for t in detail["turns"][:6]:
            mark = "*" if t["acoustic"] else " "
            print(f"   {mark} [{t['mode']:9}] {t['role']:5} {t['text'][:56]!r}")
        check("both sides of every turn persisted", detail["turn_count"] >= 4)
        check("session titled from the first message", bool(detail["title"]))
        check(
            "acoustic profile persisted on the spoken turn",
            any(t["acoustic"] for t in detail["turns"]) if WAV and WAV.exists() else True,
        )

    section("RESULT")
    if failures:
        print(f"  {len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("  All checks passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
