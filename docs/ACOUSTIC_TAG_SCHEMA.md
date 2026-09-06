# Acoustic Tag Schema (M5)

The Track M (dysfluency analyzer) ↔ Track A (prompt engineering / A12) contract.

**Status:** Frozen 2026-09-06, as the de facto schema — matches M4's trained
classifier output exactly, since both tracks are built by the same person
on the same day ahead of the presentation deadline. No separate negotiation
round was needed; this document exists so the contract is written down
rather than implicit.

## Shape

The dysfluency analyzer (`ml/dysfluency/inference.py`, `DysfluencyClassifier.predict()`)
returns a flat dict of five class names to sigmoid probabilities in `[0, 1]`:

```json
{
  "Block": 0.12,
  "Prolongation": 0.87,
  "SoundRep": 0.03,
  "WordRep": 0.05,
  "Interjection": 0.41
}
```

- Keys are always present, in this order, spelled exactly as shown (matches
  `ml.dysfluency.inference.LABEL_COLUMNS` and `ml.dysfluency.scripts.sep28k_manifest.LABEL_COLUMNS`).
- Values are independent probabilities (multi-label, not mutually exclusive
  — a clip can be both `Block` and `Interjection` at once).
- No aggregation across clips is defined here — the analyzer scores one
  ~3-second audio clip at a time. Any turn-level aggregation (e.g. "did
  this utterance contain a block anywhere") is Track A's responsibility at
  the prompt-assembly stage, not the analyzer's.

## How Track A (A12) consumes this

Prompt engineering injects acoustic tags as a short natural-language or
structured summary alongside the transcript. A minimal injection convention:

- Threshold each probability at `0.5` to decide whether to mention a tag at
  all (avoids cluttering the prompt with near-zero scores).
- Above threshold, surface the tag by its plain name (`Block`,
  `Prolongation`, `SoundRep`, `WordRep`, `Interjection`) — these names are
  stable across both tracks and should not be renamed or aliased in prompt
  text without updating this file.
- The exact prompt template/wording is A12's concern, not specified here.

## Non-goals

This schema does not cover: severity/intensity scoring (only presence
probability), speaker diarization, or any acoustic feature beyond the five
SEP-28k-derived dysfluency types M4 was trained on.
