# Demo Script

*Task A18 — Deliverable 3.*

A working end-to-end demonstration covering the seven points the brief lists.
Written to be run live in front of an examiner, with a fallback for every step
that can fail.

Total runtime: **8–10 minutes**. Rehearse it once end to end the day before.
The single most common failure is a cold model adding thirty seconds of silence
at the worst possible moment — the pre-flight below exists to prevent exactly
that.

---

## Pre-flight (do this 15 minutes before, not during)

Four processes, in this order. Each must be confirmed before starting the next.

```bash
# 1. Coaching model  (~20 s to load)
llama-server -m models/qwen2.5-3b-instruct-q4_k_m.gguf --port 8080 -c 4096

# 2. Moshi, if M1/M2 are working  (~60 s to load; skip if not)
#    Track M owns this. If it is not up, the demo still runs — see Beat 2.

# 3. Backend, with models warmed at boot rather than on first use
cd backend && set SCC_EAGER_LOAD=1 && uvicorn app.main:app --port 8000

# 4. Frontend
cd frontend && npm run dev
```

**Confirm everything before you present.** One request, one glance:

```
GET http://127.0.0.1:8000/api/status
```

```json
{ "live_available": true, "llm_reachable": true, "stt_loaded": true,
  "corpus_chunks": 340, "analyzer": "wav2vec2-sep28k", "llm_variant": "finetuned" }
```

| Field | If it is wrong |
|---|---|
| `llm_reachable: false` | llama-server is not up. Nothing will answer. Fix before starting. |
| `corpus_chunks: 0` | Run `POST /api/corpus/ingest`. Beat 4 is dead without this. |
| `live_available: false` | Moshi is down. Fine — use the Beat 2 fallback, and say so up front. |
| `analyzer: heuristic` | M4's model is not loaded. **Say this out loud during Beat 3.** |

Also: **speak the demo utterances out loud once** to check your microphone level.
Browser permission prompts mid-demo are avoidable and look careless.

---

## Beat 1 — The problem (60 s, no software)

Open on the argument, not the app. Say roughly this:

> A conventional voice assistant transcribes speech before it reasons about it.
> If someone says *"I-i-i want... water"*, the transcript reads *"I want water"* —
> and the repetition, the block, and how long it lasted are gone before the model
> sees anything. For a tool meant to help people practise speaking, that is
> exactly the wrong information to throw away.

Then state the scope boundary immediately, before anyone asks:

> This is an accessibility and practice tool. It does not diagnose, assess, or
> treat anyone — speech-language pathology is a licensed profession and this is
> not that.

**Have this measurement ready**, because it makes the argument concrete rather
than rhetorical. From `backend/scripts/bench_whisper.py`:

> We measured this. Whisper `tiny` transcribed our test utterance as
> *"I want water please"* — it removed the repetitions and a 1.4-second block
> entirely. A smaller speech-recognition model doesn't just lose accuracy on
> disfluent speech, it actively smooths it into fluent speech.

---

## Beat 2 — Live voice conversation (2 min) · *covers voice interaction, STT*

Press **Start speaking**. Have a short, natural exchange — you are rehearsing a
presentation, and you deliberately speak with pauses and repetitions.

Point at the mode pill in the header: **Live coach · ~200 ms**.

> This path is native speech-to-speech. Audio tokens in, audio tokens out, no
> text in the middle. It runs full-duplex, so I can interrupt it —

Then interrupt it mid-sentence. Barge-in is the most convincing two seconds in
the whole demo and costs nothing to show.

**Fallback if Moshi is down.** Do not hide it — the design point is that the
product survives it:

> The live path isn't running, so the app fell back automatically to the
> grounded cascade. That fallback is deliberate: five of the required features
> — retrieval, prompt engineering, the fine-tuned model — have nowhere to live
> inside a speech-to-speech model, so the cascade always exists.

The banner in the UI will already say this. Point at it.

---

## Beat 3 — What the transcript lost (90 s) · **the centerpiece**

This is the beat the project is actually about. Do not rush it.

Scroll to your last spoken turn. The transcript sits above; the **dysfluency
timeline** sits below it. Hover an event.

> The text is what a normal pipeline would have kept. The strip underneath is
> everything it would have discarded. Blocks are drawn as gaps in the speech bar,
> because a block *is* an absence — and they are to scale, so a 1.4-second block
> looks four times a 350-millisecond one.

Then show the consequence, which is the part that lands:

> And it changes what the coach does. When the analyzer reports a long block,
> the coach slows its own speaking rate — it's the `speech_rate` field on the
> response. It's matching the person's tempo instead of replying at the same
> brisk pace.

**If `analyzer: heuristic`, say so plainly:**

> The trained SEP-28k classifier isn't loaded here, so this is running our
> heuristic backend — word timings and frame energy. It's a development
> scaffold, and none of the numbers in the report come from it. The interface
> is the same either way; the trained model drops in behind it.

**Verified figure to quote:** against a 1400 ms block spliced into real speech,
the analyzer measured **1480 ms**, located within 105 ms. Whisper's transcript
contained no trace of it.

---

## Beat 4 — Grounded answers and honest refusals (2 min) · *covers RAG*

Type a question the corpus **does** cover:

> "What actually helps with filler words?"

Expand the **sources** disclosure under the reply. Show the retrieved excerpts
and their scores.

> Retrieval is MMR-reranked, so it returns four chunks that are relevant *and*
> different from each other, rather than four near-copies of one paragraph.

Now the more interesting half. Ask something the corpus does **not** cover:

> "How do I change the oil filter in a diesel engine?"

The coach declines and cites nothing.

> That refusal is deliberate and it was harder to get right than the answer.
> These embeddings have a high similarity floor — that diesel question still
> scores 0.48 against a speech-coaching corpus, and the capital of Mongolia
> scores 0.35. Our first threshold was 0.28, which let everything through. We
> measured the real distribution and set the gate at 0.55. Being unhelpful
> about a technique is much better than being confidently wrong about one.

Examiners reward a team that can name a bug they found in their own system.

---

## Beat 5 — Base vs. fine-tuned (2 min) · *covers the fine-tuned model*

**Depends on M8.** If the fine-tuned checkpoint is not deployed, replace this
beat with the M9 evaluation table from the report and say so.

Ask the same question twice, switching `llm_variant`:

```json
POST /api/chat  { "message": "...", "llm_variant": "base" }
POST /api/chat  { "message": "...", "llm_variant": "finetuned" }
```

> Every turn records which checkpoint produced it and which prompt version was
> used, so the comparison is reproducible rather than anecdotal.

Show the M9 numbers: ROUGE-L, BERTScore, the LLM-as-judge rubric, and the human
study. Then say what the base model actually does wrong — examiners want the
qualitative finding, not just the table:

> The base model tends to comment on the dysfluency. The fine-tuned one responds
> to what the person meant and leaves the speech alone unless they asked.

Also show the optimization figures (M10): model size, peak VRAM, tokens/sec,
TTFT, FP16 versus Q4_K_M.

---

## Beat 6 — It is one system (90 s) · *covers React–FastAPI integration*

Open the **history** and the **progress panel**.

> A session can start on the live path, hand off to the cascade for a grounded
> answer, and come back — and it stays one conversation. The turns are in one
> ordered thread with both modes tagged.

Point at the progress panel.

> Practice trends, not a score. There is no grade anywhere in this product, and
> that is a deliberate design constraint, not an omission.

Then be straight about latency, because they will ask:

> The live path is ~200 ms. The cascade is seconds — we measured about 1.9
> seconds to first audio, and honestly our original estimate of one second was
> wrong. That gap is the project's main quantitative result: the cascade buys
> retrieval and grounding, and pays for it in latency.

---

## Beat 7 — Close (30 s)

> Two paths, one product. Native speech-to-speech for real-time conversation
> where the model hears dysfluency directly. A grounded cascade for anything
> needing cited material, which speech-to-speech structurally cannot do. And a
> parallel acoustic branch so the text path doesn't throw away the signal the
> whole project is about.

Offer the repository, the ethics statement, and `backend/scripts/` — the
verification scripts reproduce every number quoted above.

---

## If something breaks live

| Failure | What to do |
|---|---|
| Moshi won't start | Beat 2 fallback. The automatic degradation is itself a design point — present it as one. |
| llama-server died | `GET /api/status` shows `llm_reachable: false`. Restart it; keep talking through Beat 1 while it loads. |
| Microphone blocked | Type instead. Every beat except 2 and 3 works typed. Say the voice path is in the recording. |
| Empty corpus | `POST /api/corpus/ingest`, ~30 s. Do Beat 5 while it runs. |
| Reply is slow | Do not fill the silence apologising. Say what is happening: *"this is the cascade — it retrieves before it answers, which is the tradeoff we measured."* |

**Record a backup run the night before** and have it open in a second tab. If
the live demo fails badly, switch to it rather than debugging in front of an
examiner.
