# M4 — Dysfluency Classifier Design

**Status:** Approved 2026-09-06. Branch: `m4-dysfluency-classifier`.

## Goal

Train the acoustic dysfluency classifier called for by the project plan (M4):
`wav2vec2-base` + multi-label head over five dysfluency types — block,
prolongation, sound repetition, word repetition, interjection — reporting
per-class F1. This produces the "Dysfluency analyzer" component in the
Grounded Knowledge Mode cascade (`Whisper → Dysfluency analyzer → Prompt
Engineering + acoustic tags → RAG → LLM`).

M5 (the shared acoustic-tag schema, jointly defined with Track A/A12) has not
been frozen yet. Per user decision, M4 proceeds now anyway: the five target
label names already match SEP-28k's native columns 1:1, so there is little
schema risk, and Track A hasn't started A12. If M5 later renames or
reshapes the tag set, this component's *label names* may need a rename pass,
but the model and training pipeline are unaffected.

## Downstream use

Per user decision, this is not just a benchmarked report artifact — it is
built to be called per-clip from the future backend cascade (a 3-second
audio clip in, five dysfluency probabilities out). This shapes the design
to include a clean, minimal inference wrapper in addition to the training
code, even though wiring it into the actual backend request path is a
separate, later task and out of scope here.

## Data

Source: `ml/dysfluency/sep28k-src/SEP-28k_labels.subset4.csv` (20,250 rows,
already covering the 4-show subset acquired in M3), cross-referenced against
clips physically present under `data/sep28k/audio/clips/{Show}/{EpId}/`
(20,170 wav files, 16kHz mono, 3s each).

**Quality filtering** (per user decision, matches standard SEP-28k practice):
drop any clip where `PoorAudioQuality`, `NoSpeech`, `Music`, or `Unsure` > 0
in the label CSV, before splitting. Measured on the current label file this
removes ~15% of rows (some flags overlap on the same clip).

**Labels:** the five target columns (`Block`, `Prolongation`, `SoundRep`,
`WordRep`, `Interjection`) are annotator agreement counts (0-3, three
annotators), not binary. Binarize as positive if count > 0. This is a
genuine multi-label problem, not single-label/mutually-exclusive — measured
positive rates per class on the current data: Block 42%, Interjection 36%,
Prolongation 30%, SoundRep 21%, WordRep 18%. ~20% of clips have none of the
five positive (fluent speech or other non-target categories).

**Split** (per user decision): by episode, stratified by show, roughly
70/15/15 train/val/test. No episode's clips appear in more than one split —
this avoids the speaker/recording-condition leakage a naive random
clip-level split would introduce, since clips from the same episode share
speaker voice, microphone, and background noise.

Split assignment is deterministic (fixed seed) so re-running the prep script
reproduces the same split.

## Model

`facebook/wav2vec2-base` via `transformers`, configured for multi-label
sequence classification (5 sigmoid outputs, `problem_type =
"multi_label_classification"`).

**Fine-tuning strategy** (per user decision): freeze the CNN feature
extractor (the standard, well-established low-level audio front end),
fine-tune all 12 transformer encoder layers plus the classification head.
This is expected to fit within the RTX 5050's 8GB VRAM at a batch size in
the 8-16 range for 3-second clips, training locally rather than in the
cloud, since M4 does not need QLoRA/bitsandbytes (unlike M7's LLM
fine-tuning, which the existing `ml/requirements.txt` comment about
Colab/Kaggle applies to, not this task).

**Loss** (per user decision): `BCEWithLogitsLoss` with per-class `pos_weight`
set to each class's inverse positive frequency in the training split, to
counter the moderate class imbalance without needing more exotic
loss functions.

**Training environment risk:** `ml/requirements.txt` currently pins
`torch==2.5.1`, which predates PyTorch's Blackwell (sm_120) GPU support.
This design assumes a newer, sm_120-compatible torch build (2.7+ / cu128 or
newer) will be installed into `ml/.venv` and verified
(`torch.cuda.is_available()`) as an implementation prerequisite, alongside
recreating that venv under Python 3.12 (the existing `ml/.venv` was
previously built against Python 3.14, for which no current torch wheel
exists). If local GPU training turns out not to be viable after honest
troubleshooting, fall back to CPU training locally before considering a
cloud notebook — flag this to the user rather than silently switching.

## Evaluation

Per-class F1, precision, and recall at a 0.5 sigmoid threshold on the held
out test split — the M4 deliverable is explicitly "per-class F1." Also
record macro-F1 as a single summary number, and PR-AUC per class as a
threshold-independent secondary metric.

## Deliverables / file layout

- `ml/dysfluency/scripts/prepare_splits.py` — reads the label CSV, applies
  quality filtering, binarizes labels, performs the episode-stratified
  split, writes `data/sep28k/audio/splits/{train,val,test}.csv` manifests
  (clip path + 5 binary labels each).
- `ml/dysfluency/scripts/train_classifier.py` — loads the manifests, trains
  the model, saves the best checkpoint (by val macro-F1) and a training log.
- `ml/dysfluency/scripts/evaluate_classifier.py` — runs the saved checkpoint
  against the test manifest, writes a metrics report (JSON + a markdown
  table) to `ml/dysfluency/reports/`.
- `ml/dysfluency/inference.py` — a minimal, dependency-light wrapper:
  load a checkpoint once, expose a function taking a clip path or a raw
  16kHz mono float array and returning a dict of the five class
  probabilities. This is what the future backend integration will import;
  it has no backend-specific code in it.
- Model checkpoint itself is git-ignored (add to `.gitignore` under
  `data/` or a new `ml/dysfluency/checkpoints/` pattern) — too large to
  commit, and reproducible from the training script.

## Testing

Unit tests (`ml/dysfluency/scripts/tests/`) for the non-training-loop parts:
label binarization, quality filter, episode-stratified split (no leakage
across splits, every show represented in every split), and the inference
wrapper's output shape/range. The actual training run itself is verified by
running it and inspecting the resulting metrics report, not by an automated
test — this matches the M2/M3 precedent of validating real runs by execution
rather than by mocking GPU/audio behavior in unit tests.
