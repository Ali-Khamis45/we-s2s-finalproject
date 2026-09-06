# Acoustic Tag Schema (M5)

**Superseded 2026-09-06.** The real, already-implemented M5 schema is
`backend/app/schemas/acoustic.py` (`DysfluencyKind`, `DysfluencyEvent`,
`ProsodyMetrics`, `AcousticProfile`) — richer than what this file originally
proposed (timed events with confidence/duration, prosody metrics, and a
`COACHING_HINT` mapping per event kind, not just a flat probability dict),
and already wired into `backend/app/services/dysfluency.py`'s
`Wav2VecBackend`.

This file was written from scratch while a real M5 contract already existed
in the codebase — a duplication error, not a deliberate second schema. Read
`backend/app/schemas/acoustic.py`'s module docstring and `DysfluencyKind`
for the actual, binding contract between Track M and Track A.

**One real gap this duplication surfaced and fixed:** M4's training code
(`train_classifier.py`) never set `id2label`/`label2id` to match
`DysfluencyKind`'s values (`block`, `prolongation`, `sound_repetition`,
`word_repetition`, `interjection`), so the trained checkpoint's config had
generic `LABEL_0`..`LABEL_4` placeholders — `Wav2VecBackend` would have
silently filtered out every prediction and fallen back to the heuristic
analyzer. Fixed in the training code (now sets `id2label`/`label2id`
correctly via `sep28k_manifest.DYSFLUENCY_KIND_BY_LABEL_COLUMN`) and patched
directly into the existing checkpoint's `config.json`.
