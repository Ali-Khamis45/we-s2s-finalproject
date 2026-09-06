# Feature prompt — "Hear it again": replay any coach reply as speech

## Why this exists

Right now the coach's voice only exists in the moment. The live and knowledge
sockets stream synthesized audio while the reply is being generated, and once it
has played it is gone. Two situations lose it entirely:

1. The user types instead of speaking. `POST /api/chat` returns text only, so a
   typed conversation is silent even though the whole product is about how
   speech sounds.
2. The user missed it, or wants to hear the pacing again. There is no way back —
   audio is never persisted, and reopening a past session from
   `GET /api/sessions/{id}` replays the transcript with no sound at all.

Build a speaker button on every coach turn. Press it and the reply is spoken.
Press it again and it stops. It works on a live turn, a typed turn, and a turn
loaded from history.

Keep the point of the project intact: the coach's delivery rate is derived from
the user's acoustic profile, so a replay must sound the way the reply sounded
(or would have sounded) when it was produced — not at a flat default speed.

## Scope

Backend: one new endpoint plus caching. Frontend: one new control, one small
playback module, and the plumbing that lets a rendered message know its
persisted turn id. No model work, no changes to the live path's streaming
behaviour, no audio recording or storage of user speech.

---

## Backend

### 1. Endpoint

Add to `backend/app/api/routes/` (a new `speech.py`, registered like the other
routers):

```
GET /api/sessions/{session_id}/turns/{turn_id}/speech  ->  audio/wav
```

Rules:

- Owner-scoped. Reuse the `owned_session` dependency from `app/api/deps.py`.
  There must be no way to synthesize another account's turn, and no route that
  accepts free text to speak — the text always comes from the database row.
  That keeps the endpoint from becoming an open TTS service.
- 404 if the turn does not exist or does not belong to that session.
- 422 (`ValidationError`) if the turn's role is not `coach`. The user's own
  words are not read back to them.
- 503 through the existing error envelope when Kokoro is unavailable
  (`DependencyMissingError` / `ModelUnavailableError` from
  `app.core.errors`). The message should say the voice is not loaded and that
  the text is still there, in the voice the rest of the app uses.
- Response: a complete WAV, built with `app.services.audio.wav_bytes` at
  `settings.tts_sample_rate`. Set `Content-Type: audio/wav`,
  `Content-Length`, and `Cache-Control: private, max-age=3600`.

Use `tts_service.synthesize(text, speed=rate)`. Whole-file rather than
streaming: a replay is short, the caller wants a seekable buffer, and adding a
second streaming transport for this is not worth it.

### 2. Speech rate

The rate is what makes the replay honest. Resolve it in this order:

1. The `acoustic` profile on the most recent **user** turn before this coach
   turn in the same session — pass it to `tts_service.rate_for(profile)`, the
   same call the knowledge socket makes.
2. If that turn has no profile (typed input), `settings.tts_speed_default`.

Do not invent a new rate policy and do not store a rate column. Deriving it from
the neighbouring turn means a replay matches what live playback did, and it
keeps working for rows written before this feature existed.

### 3. Caching

Synthesis costs a few hundred milliseconds and the button invites repeat
presses. Add a small in-process LRU keyed by `(turn_id, rounded rate)` holding
the encoded WAV bytes, capped by total size (something like 32 entries or 64 MB,
whichever comes first) and cleared when the process restarts. No disk cache, no
new table. Put it in the service layer, not the route, and give it a unit test
that a second call does not re-enter Kokoro.

### 4. Schema and types

If anything about the response shape changes in OpenAPI, run `make types` so
`frontend/src/lib/api-types.gen.ts` regenerates. CI fails on drift. A binary
response adds no schema, but check `docs/openapi.json` is still current.

---

## Frontend

### 5. Carry the turn id into the UI

`Message` in `frontend/src/lib/types.ts` is a UI type, not a generated one, so
it can gain a field. Add:

```ts
/** The persisted row. Absent while a turn is still streaming. */
turnId?: number;
/** The session the turn belongs to — replay needs both halves of the path. */
sessionId?: string;
```

Fill it from the two places the backend already reports it:

- `ChatResponse.turn_id` on the HTTP path (`POST /api/chat`, `POST /api/chat/audio`).
- The `done` frame's `data.turn_id` on the knowledge socket
  (`DoneFrame` in `lib/protocol.ts`).
- `TurnOut.id` when a past session is loaded through `api.getSession`.

The live socket may close a coach turn without a `done` frame. Where no id
arrives, the message simply has no replay button — see the states below. Do not
fabricate an id and do not block rendering on one.

### 6. Playback module

New file `frontend/src/audio/replay.ts`. Do not extend `StreamPlayer` — that
class exists to schedule live PCM chunks sample-continuously, and a replay is a
single finished buffer with different needs (stop, and knowing when it ended).

The module owns:

- One `AudioContext`, lazily created on the first user gesture.
- `play(url, { signal })` — fetch with credentials and the auth header (route it
  through the same request helper in `lib/api.ts` so the 401-refresh-retry path
  applies), `decodeAudioData`, play, resolve when playback ends.
- `stop()` — immediate, no fade tail longer than ~20 ms.
- A module-level rule that **only one replay plays at a time**. Starting a
  second replay stops the first.

### 7. The control

Add a `ReplayButton` used inside `MessageBubble`'s `turn-head` in
`components/Conversation.tsx`, for coach turns only.

States and their accessible names:

| State | Trigger | Icon | Accessible name |
|---|---|---|---|
| idle | default | speaker | `Play this reply` |
| loading | request in flight | speaker with pulse | `Loading audio` |
| playing | audio playing | speaker with stop bar | `Stop playback` |
| error | 503 or network failure | muted speaker, non-interactive | `Voice unavailable` |

- `aria-pressed` reflects playing. Announce state changes through the button's
  own accessible name, not a new live region — `Conversation` already has
  `aria-live="polite"` and a second announcer talks over it.
- Hidden entirely (not disabled) when the message has no `turnId`. A control
  that can never work is noise.
- Keyboard reachable, visible focus ring using the existing focus token.
- The icon is inline SVG with `currentColor`, sized in `em`, `aria-hidden`.

### 8. Interaction rules

- **Never fight the live coach.** While `speaking` is true (the coach is
  streaming audio right now), replay buttons are disabled with a title
  explaining why. Two voices at once is the worst possible outcome for a speech
  tool.
- **Barge-in stops replay.** If the microphone capture starts, or the user sends
  a turn, call `stop()`. Reuse whatever signal `useCoachSession` already exposes
  for this rather than adding a second listener on the capture module.
- **Unmount stops replay.** Switching sessions in the sidebar must not leave a
  voice playing over the new one.
- Pressing the button on a different message while one is playing switches to
  the new one immediately.

### 9. Styling

Follow the Night Studio direction in `design/UI_REBUILD_PROMPT.md`. The button
is a quiet affordance in the turn header, not a primary action: sage
(`--path-grounded` family) at rest, amber only while playing, since amber is
rationed for the live path and this is the one moment a replay is genuinely the
active voice. Radius, spacing and transition come from the existing tokens in
`styles/tokens.css` — no new hard-coded values.

Motion: the loading pulse and the playing indicator must both be inert under
`prefers-reduced-motion`. Use the existing `useReducedMotionSafe` hook and stop
the animation, do not merely shorten it.

---

## Edge cases to handle explicitly

- Reply text is empty or whitespace: no button.
- Very long reply (a few thousand characters): the endpoint still returns one
  file; make sure the request timeout on the client is generous enough and the
  loading state does not look stuck. Show the loading state from the first
  press, not after a delay.
- Two rapid presses: the second is a stop, not a second fetch.
- The tab loses focus mid-playback: let it keep playing. Browsers handle this.
- Kokoro unavailable at boot but loaded later: the error state is per-press, not
  sticky across the session. Do not cache a failure.
- History replay of a session whose turns predate this feature: works, because
  nothing new is persisted per turn.

---

## Constraints

- The existing 30 frontend tests query by role, accessible name and text.
  Restyling is safe; asserted strings must survive verbatim. Run the suite
  before and after.
- Do not change `lib/api.ts`'s error handling, `lib/types.ts`'s generated block,
  `audio/capture.ts`, or the shape of anything in `hooks/useCoachSession.ts`
  beyond adding the two id fields to messages it produces.
- `docs/ETHICS.md` still governs: no score, no severity, no assessment language
  anywhere in the new UI copy. "Play this reply" is a playback control, nothing
  more.
- Follow the repository's existing comment style — comments explain the decision
  behind non-obvious code, they do not narrate what the line does.

## Tests to add

Backend (`backend/tests/`):

1. A coach turn returns WAV bytes with the right content type.
2. Another user's session returns 404, not 403 and not audio.
3. A user turn returns 422.
4. Rate comes from the preceding user turn's acoustic profile — assert the value
   passed to the TTS service, with the service faked.
5. Kokoro unavailable returns 503 with the standard error envelope.
6. Second identical request hits the cache and does not call synthesis again.

Frontend:

7. The button renders on coach turns and not on user turns.
8. It is absent when `turnId` is missing.
9. It is disabled while the coach is speaking.
10. Pressing it calls playback with the right URL; pressing again stops.
11. Accessible name changes between idle and playing.

## Definition of done

- A typed conversation can be heard, one reply at a time.
- Reopening a past session and pressing the speaker plays that reply at the rate
  it was, or would have been, delivered.
- Nothing overlaps: live audio and replay never sound together.
- `make test` (or the equivalent for both halves) passes, including the existing
  suites, and `make types` produces no diff.

## Out of scope

- Persisting synthesized audio to disk or the database.
- A "play whole conversation" control.
- Voice selection, per-user speed settings, or a playback speed slider.
- Downloading the audio.
- Anything touching the live socket's streaming path.
