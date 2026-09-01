# Build Brief — Portfolio Hardening (Track A)

Eight work packages that turn a good graduation project into one a stranger can run,
verify, and be impressed by in under a minute. Paste the whole file into Claude Code at the
repo root, or paste a single `## WP-n` section to do one package at a time — each is written
to stand alone.

**Everything here is Track A.** Nothing in this document touches training, quantization, or
Track M's benchmarks. Where a package needs a number from Track M, it consumes it; it never
produces it.

---

## Framing — read this first, it changes what "done" means

This repo already contains something rare, and the work below exists to make it visible
rather than to add to it.

`backend/app/core/config.py` justifies choosing Whisper `base` over `small` with measured
word-timestamp error, and records that `tiny` transcribed `"I-I-I want … water"` as
`"I want water please"` — silently fluent-izing the exact signal the project exists to
preserve. `backend/scripts/README.md` documents a corpus screen that was *wrong on its first
version* and says so. `docs/PROJECT_PLAN.md` replaces its own latency estimate with a
measurement three times worse, and argues that this strengthens the thesis.

That is engineering judgment on display, and it is the thing worth amplifying. So:

- **Never delete an inconvenient number to make a table look better.** If a benchmark
  regresses, report the regression.
- **Every claim in a README or report gets a command that reproduces it.**
- Prefer one measured, honest result over three unmeasured features.
- Do not add product surface. Six of these eight packages add *evidence*, not features.

---

## Shared constraints

Apply to every package.

1. `cd backend && pytest` (35) and `cd frontend && npm test` (30) stay green. A package that
   needs a test changed changes it in the same commit and says why.
2. `docs/ETHICS.md` outranks this document. Nothing added may score, rank, or pathologise
   anyone's speech.
3. Visual work follows `design/UI_REBUILD_PROMPT.md` — Night Studio tokens, the reduced-motion
   contract, no new palette.
4. One commit per package, in the style already in the log ("Run the full cascade for real;
   replace two estimates with measurements"). The commit history is read by reviewers; it is
   currently good, keep it that way.
5. Each package leaves the app runnable. No half-migrated states across a commit boundary.
6. Load the **`dataviz`** skill before any chart, the **`artifact-diagramming`** skill before
   any diagram, and the **`humanizer`** skill before finalising any prose that ships.

---

## WP-1 — One-command reproducibility

**Why:** seven verification scripts exist, each producing citable numbers, and none of them is
reachable from the repo root or discoverable from the README. Right now the measurements are
real but take a reader's word for it. After this, a stranger regenerates them.

**Build**

- A `Makefile` at the repo root with at least: `make setup`, `make test`, `make lint`,
  `make bench`, `make bench-fast`, `make docs`. Windows-friendly equivalents in
  `scripts/make.ps1`, since the dev box is Windows.
- `backend/scripts/run_all.py` — runs every verification script in dependency order, tolerates
  a missing model by **skipping and recording the skip** rather than failing the run, and
  writes one machine-readable result file per script to `docs/benchmarks/*.json`.
- A renderer that turns those JSON files into `docs/BENCHMARKS.md`, with a provenance header
  on every table:

  > Generated `2026-09-01T18:22Z` · commit `1db45d9` · Python 3.11.9 · CPU `<model>` ·
  > GPU `<model or none>` · Whisper `base` int8 · LLM `qwen2.5-3b-q4_k_m` ·
  > analyzer backend `heuristic`

  The analyzer-backend line matters: several numbers change when Track M's trained classifier
  lands, and a table that does not say which produced it is not citable.
- Mark every number that appears in `Readme.md`, `docs/REPORT.md` or `docs/PROJECT_PLAN.md`
  with the command that regenerates it, e.g. `<!-- source: make bench -->`. Add
  `scripts/check_claims.py` that greps for bare numeric tables lacking a source marker and
  fails on them.

**Be honest about the boundary:** `make bench` needs downloaded models and cannot run in CI.
Say so in the Makefile comment and in `docs/BENCHMARKS.md`. `make bench-fast` is the
model-free subset that CI *can* run — it proves the harness still executes, not the numbers.

**Done when:** deleting `docs/BENCHMARKS.md` and running `make bench` on a machine with models
present reproduces it, and the diff is only timings and the timestamp.

---

## WP-2 — Per-turn latency waterfall

**Why:** this is the highest-value item in the document. `Turn.timings` is already persisted
per turn per stage in SQLite. `StageTiming` is already defined in `frontend/src/lib/types.ts`.
`Message.timings` is already on the frontend type. **Nothing in the UI reads any of it.** The
thesis argument — that the cascade costs seconds where native S2S costs milliseconds — is
sitting in the database with no way to see it.

**Backend (small but required)**

`TurnOut` in `backend/app/schemas/chat.py` returns `total_ms` but **not** `timings`. Add it,
and populate it in `_to_turn()` in `sessions.py`, or a resumed conversation shows no
waterfalls. Freeze the stage vocabulary as an enum (`stt`, `analyze`, `retrieve`, `prompt`,
`llm_ttft`, `llm_total`, `tts_first`) so the UI can order and colour stages deterministically
instead of guessing from free-text labels.

**Frontend**

Read the `dataviz` skill first. Then build `components/TurnTimings.tsx`:

- **Collapsed by default.** Each coach turn carries a small mono chip — `1.9 s` — in
  `--muted`. Clicking it expands the breakdown. The waterfall must not shout on every turn.
- Expanded: a horizontal stacked bar, one segment per stage, widths proportional to duration,
  with a **minimum 3 px segment width** so the 3 ms analyzer stage stays visible next to a
  600 ms STT stage. Do not use a log scale — it flatters the slow stages and misrepresents the
  result. Label each segment with its name and `tabular-nums` milliseconds.
- Colour: one sequential ramp derived from the Night Studio tokens (sage → amber), *not* a
  categorical rainbow. Stages are a sequence, and the palette should say so. Amber is reserved
  for the dominant stage in each turn — which will almost always be STT, and that is the point.
- **Accessible, not just visual:** the same data renders as a real `<table>` behind a
  "Show as table" toggle, every segment has an `aria-label` with stage and duration, and no
  information is carried by colour alone.
- **Comparison strip:** when a session contains both a live turn and a knowledge turn, show a
  two-row comparison at the top of the expanded view — the same axis, both paths. That single
  image is the thesis. Make it screenshot-worthy at 1200 px wide.
- **Session aggregate** in `ProgressPanel`: p50 and p95 per stage across the current session,
  computed client-side from the loaded turns. Framed as system performance, never as anything
  about the person — this is the one place a chart in this app is allowed to be about speed.

**Done when:** a screenshot of the comparison strip could be pasted into the report as a figure
with no editing, and the timeline still renders correctly for a turn with no timings at all
(live-path turns may have none).

---

## WP-3 — Retrieval evaluation

**Why:** A9–A11 are yours; your partner evaluates the generator, you evaluate the retriever.
`calibrate_gate.py` already derived the 0.65 threshold from 18 questions — real work, but it
measures the gate, not retrieval quality. Almost no student RAG project ever reports recall@k.
Reporting it, with ablations, is the cheapest available separation from the field.

**Build**

- `data/eval/retrieval_golden.jsonl` — **50 questions minimum**, each:
  ```json
  {"id": "q07", "question": "...", "in_corpus": true,
   "relevant": ["distinctive substring from the answering passage", "..."],
   "kind": "direct|paraphrase|multi_hop|negation"}
  ```
  Build it by sampling chunks and writing the question each one answers — **write the question
  from the book text before ever running retrieval on it**, or you will unconsciously write
  questions your current retriever already wins. Include 15 out-of-corpus negatives, and
  include paraphrases and negations, because "what should I *not* do when I speed up" is where
  embedding retrieval usually fails.
- `backend/scripts/eval_retrieval.py` reporting **recall@1 / @4 / @8, MRR@10, nDCG@10**, plus
  gate precision and recall with a precision–recall curve and the operating point at 0.65
  marked on it.
- Ablations, each a row in a table and a small chart:
  | Knob | Sweep |
  |---|---|
  | MMR λ | 0.0, 0.25, 0.5 (current), 0.75, 1.0 |
  | k | 2, 4 (current), 8 |
  | chunk size / overlap | 256/32, 512/64 (current), 1024/128 |
  | embedding model | bge-small (current), bge-base, all-MiniLM-L6 |
- Output `docs/RETRIEVAL_EVAL.md` with the tables, the charts as committed PNGs, and a
  **"where the current defaults lose"** section. If λ=0.75 beats λ=0.5, say so and either
  change the default or explain why you kept it. A sweep where the current setting wins every
  row is a sweep nobody believes.
- State the self-labelling bias as a limitation in the document, in one honest paragraph.

**Done when:** `make eval-retrieval` regenerates the whole document, and the report can cite
recall@4 with a number rather than an adjective.

---

## WP-4 — Accessibility, actually done

**Why:** this is the only package that is also an obligation. An accessibility tool with an
inaccessible interface reads as not having meant it, and a reviewer in this domain will check.
Two of the items below fix real exclusions, not checkboxes.

**Build**

- **Live captions of the coach's speech.** A speech tool whose output is audio-only excludes
  deaf and hard-of-hearing users entirely. Coach text already streams as deltas; render it
  synchronised with playback (`StreamPlayer` knows each chunk's scheduled start, so emit cue
  offsets alongside the text), with a persistent on/off toggle, on by default. The full
  transcript stays visible regardless.
- **A first-class no-microphone path.** Typing is currently a fallback with placeholder copy
  ("…or type instead"). Make it an equal mode, chosen deliberately, that never implies the
  user failed to use the real one. Some users cannot or will not speak to a machine, and some
  demo rooms have no working mic.
- **Coach speech-rate control** surfaced in the UI, bounded by the existing
  `tts_speed_min`/`tts_speed_max` config. This is a genuine accommodation, not a setting.
- **WCAG 2.2 AA pass**: contrast (already handled by the design tokens — verify, don't assume),
  visible focus everywhere, full keyboard operation, target sizes ≥ 24 px, no
  colour-only meaning, `aria-live="polite"` announcing coach turns **on completion, not per
  token** (a per-token live region is unusable with a screen reader).
- **Automated:** `jest-axe` (or `vitest-axe`) assertions on every top-level component, wired
  into `npm test` and CI. Zero violations is the gate.
- **Manual, documented:** `docs/ACCESSIBILITY.md` recording a keyboard-only walkthrough, a
  screen-reader pass (NVDA on Windows is fine), the reduced-motion behaviour, and — this is
  the part that earns trust — **a section on what is still not accessible**, such as the
  waterfall being fundamentally visual, with the table fallback named as the mitigation.

**Done when:** the app is fully operable with the mouse unplugged, and `npm test` fails if a
new component introduces an axe violation.

---

## WP-5 — `docker compose up`, no GPU, no downloads

**Why:** the app was already designed to boot with zero models and report what is missing via
`/api/status` — a deliberate decision the README already explains. That design makes a
clone-and-run demo almost free, and clone-and-run is the difference between a repo people
scroll past and one they actually open.

**Build**

- Multi-stage `backend/Dockerfile` (CPU wheels only) and `frontend/Dockerfile` (build → static
  serve), plus root `docker-compose.yml` with healthchecks and a named volume for `data/`.
- Default environment is **degraded mode**: `SCC_MOSHI_ENABLED=false`, no model downloads, no
  GPU. The UI must come up, `/api/status` must clearly report what is unavailable, and the
  degraded state must look designed rather than broken.
- A `demo` compose profile that seeds three sample conversations with realistic acoustic
  profiles so the sidebar, the timeline and the waterfall are populated on first open.
  **Label the seed data as synthetic in the UI** — sample transcripts must never be mistakable
  for a real person's practice.
- `docs/DEPLOY.md`: the two-minute CPU path first, the full GPU path second.

**Done when:** on a clean machine with only Docker, `docker compose --profile demo up` reaches
a populated UI in under two minutes with no model downloads.

---

## WP-6 — CI, and the claim that makes it worth having

**Why:** badges are cheap. The reason to do this package is the egress test at the bottom.

**Build**

- `.github/workflows/ci.yml`: ruff, mypy on `backend/app` (start non-strict, ratchet), backend
  pytest with a coverage floor, `tsc --noEmit`, vitest including the axe assertions,
  `npm run build`, `make bench-fast`, and a Docker build. Cache pip and npm.
- Badges in the README: CI status, backend coverage, frontend tests, licence.
- **The network egress test.** Assert that a full Knowledge Mode turn makes **zero outbound
  connections to anything but localhost**: monkeypatch `socket.socket.connect` to raise on any
  non-loopback address, run a turn end to end against a mocked LLM server, assert it completes.
  Be precise about the boundary in the test's docstring — first-run model downloads *do* hit
  the network; the claim is that **inference makes no external call**, and that is what the
  test proves.

  That turns "your speech never leaves your machine" from marketing into a property with a
  test protecting it — which, for a tool that records how someone stammers, is the strongest
  single sentence in the whole project.

**Done when:** CI is green on `main`, and deliberately adding an `httpx.get("https://example.com")`
into the turn path makes the egress test fail.

---

## WP-7 — Failure gallery

**Why:** every project shows its best case. Showing the failure modes, deliberately, is what
makes the good results believable. It is an afternoon's work and it is the section an examiner
remembers.

**Build** `docs/FAILURE_GALLERY.md`, six to ten documented cases. Each entry: the input, what
the system did, what it should have done, the diagnosed cause, and whether it is fixable or
inherent. Reproduce each from a committed fixture where possible.

Candidates you can almost certainly produce today:

- The heuristic analyzer firing on a naturally slow speaker with long pauses (false positive)
- A short block under ~300 ms missed entirely, bounded by Whisper's timestamp resolution
- The groundedness gate refusing a legitimately in-scope question phrased unusually
- Retrieval returning a century-old framing where the corpus has no modern equivalent
- The LLM ignoring an injected acoustic tag and answering generically
- Kokoro mispronouncing a technique term
- The live→knowledge handoff firing on a turn that was not actually knowledge-seeking

Close with one paragraph separating **fixable** (better analyzer once M4 lands, more corpus)
from **inherent** (transcript-timestamp resolution, English-only, no clinical validity). Do not
pad it with failures you invented — only cases you actually observed.

---

## WP-8 — Packaging: the forty seconds that decide everything

**Why:** a reviewer gives the repo forty seconds. Currently they land on a long text README with
no image, no video, and no way to run anything. Every package above is invisible until this one
ships, which is why it goes last and matters most.

**Build**

- **README rewrite.** Above the fold, in this order: project name and one sentence; an animated
  GIF of the dysfluency timeline with the waterfall expanded; the two headline measurements
  (1400 ms block recovered as 1480 ms, located within 105 ms, absent from the transcript
  entirely — and ~1.9 s cascade vs ~200 ms native); a `docker compose up` quickstart; CI badges.
  *Then* the depth that is already written. Move the current opening prose down; it is good, it
  is just not what the first screen is for.
- **An architecture diagram** as committed SVG — dual path, the Inner Monologue hinge, the
  degradation route. Use the `artifact-diagramming` skill; it must be legible on both light and
  dark backgrounds and readable at README width without zooming.
- **A 90-second demo video** and a 10-second GIF cut from it. The video follows `docs/DEMO.md`
  and opens on the dysfluency timeline, not on a login screen. Nobody watches past 20 seconds
  without seeing the novel thing.
- **`docs/CASE_STUDY.md`** — the portfolio-facing narrative, 800–1200 words, structured as
  problem → constraint → decision → measurement → result. Its spine is the Whisper decision:
  the obvious choice was the bigger model, the measurement said otherwise, and the reason
  (`tiny` fluent-izes disfluent speech) *is* the project's thesis in miniature. Run it through
  the `humanizer` skill. No "leveraging", no "seamlessly", no "in today's fast-paced world".
- **`docs/screenshots/`** — every major screen, both themes, committed at 2× and referenced
  from the README.

**Done when:** someone who reads only the first screen of the README can state what the project
does, what was measured, and how to run it.

---

## Order, effort, and what to cut

| # | Package | Effort | Depends on |
|---|---|---|---|
| WP-2 | Latency waterfall | 1–2 days | — |
| WP-3 | Retrieval evaluation | 2–3 days | — |
| WP-4 | Accessibility | 2 days | design rebuild |
| WP-1 | Reproducibility harness | 0.5–1 day | — |
| WP-6 | CI + egress test | 1 day | WP-1, WP-4 |
| WP-5 | Docker degraded mode | 1 day | — |
| WP-7 | Failure gallery | 0.5 day | WP-3 helps |
| WP-8 | Packaging | 1–2 days | all of the above |

Recommended order: **WP-2 → WP-3 → WP-1 → WP-4 → WP-6 → WP-5 → WP-7 → WP-8.**

If the weeks run out, cut from the bottom of this list, not the top — but **never cut WP-8**.
An unpackaged project with eight completed packages scores lower with a reviewer than a
packaged project with three. If only three survive, make them WP-2, WP-3 and WP-8.

---

## Out of scope — do not do these

Not because they are bad ideas, but because each one adds surface without adding evidence, and
several belong to your partner:

- Anything that trains, quantizes, fine-tunes, or benchmarks a model (Track M — M1–M12)
- Multi-language support (Moshi is English-only; it is a documented limitation, not a gap)
- A mobile app, a browser extension, or a desktop wrapper
- More LLM providers or a model-picker UI
- Agent frameworks, tool calling, or an MCP server
- A marketing landing page
- More chart types in the progress dashboard — one honest chart beats four
- Cross-conversation memory, unless you have already decided it earns its ethical cost
