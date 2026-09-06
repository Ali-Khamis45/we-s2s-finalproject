# M3 — SEP-28k / FluencyBank dataset acquisition harness

## Context

Track M task M3 (`docs/PROJECT_PLAN.md`): acquire the SEP-28k acoustic dataset
for the dysfluency classifier (M4). Labels are public and already vendored at
`ml/dysfluency/sep28k-src/` (a shallow clone of
`https://github.com/apple/ml-stuttering-events-dataset`, git-ignore this
directory going forward — it's a third-party clone, not project source. Add
`ml/dysfluency/sep28k-src/` to `.gitignore`). Audio must be fetched from the
original podcast source URLs listed in `SEP-28k_episodes.csv` and
`fluencybank_episodes.csv` inside that clone.

Apple's own `download_audio.py`/`extract_clips.py` in that clone are a
reference implementation only (raw `wget`+`ffmpeg` subprocess calls, no
retry, no resumability, aborts-unfriendly). Do not just run them as-is — this
task is to write a project-owned harness in `ml/dysfluency/scripts/` that
does the same job but production-quality for a slow, flaky, multi-hour
fetch: resumable, retrying, and tolerant of individual dead links without
aborting the whole run.

## Verified facts from investigation (use these, don't re-derive)

- `SEP-28k_episodes.csv`: 385 rows, no header, comma+space delimited
  (`", "`). Columns: `show_name, episode_slug, url, show_abbrev, ep_idx`
  (0-indexed; url is column index 2, show_abbrev is second-to-last, ep_idx is
  last — matches Apple's `download_audio.py` logic exactly).
- `fluencybank_episodes.csv`: same format, 33 rows.
- `SEP-28k_labels.csv` / `fluencybank_labels.csv`: headered CSVs consumed
  later by `extract_clips.py` (not this task's concern — this task only
  fetches and converts full-episode audio to 16kHz mono WAV, matching
  Apple's `download_audio.py` output contract: `{wavs_dir}/{show_abbrev}/{ep_idx}.wav`).
- Link health, spot-checked (Sep 2026): most SEP-28k source URLs
  (`stutterrockstar.wordpress.com`, `media.blubrry.com` for StutterTalk,
  `traffic.libsyn.com`) are alive and return 200.
- **Known structural dead-link case**: all 85 episodes of the
  `Stuttering_is_Cool` show (show_abbrev `StutteringIsCool` in the labels
  CSV — note this differs from the episodes CSV's `show_abbrev` column,
  verify which the harness actually needs) use
  `http://feedproxy.google.com/~r/StutteringIsCool/~5/<token>/cool<N>.mp3`
  URLs. Google FeedBurner/feedproxy has been shut down entirely — these
  requests time out, they are not merely redirects to follow. This affects
  ~4,013 of 28,177 labeled clips (~14% of SEP-28k).
- **Verified recovery path for that show**: the podcast is still live at
  `stutteringiscool.com`, hosted on Blubrry, and episode files kept the same
  `cool<N>.mp3` naming. The dead feedproxy URL's trailing filename
  (`cool159.mp3`) can be reconstructed into a live URL:
  `https://media.blubrry.com/stutteringiscool/www.stutteringiscool.com/sound/cool<N>.mp3`.
  Verified working via `curl -L` (returns HTTP 200, correct-size mp3) for
  `cool159` and `cool227`, which were both confirmed dead via the original
  feedproxy URL. The needed episode-number range (54–244ish) is well within
  what the live feed currently serves (up to at least cool284).
  **Important:** `wget` failed to follow this specific redirect chain
  (exit code 4 / network failure partway through) even though `curl -L`
  succeeded cleanly — use a redirect-following HTTP client (Python
  `requests`, `allow_redirects=True`) for the actual downloads, not `wget`.
  `ffmpeg` is confirmed installed and on PATH for the wav conversion step.

## What to build

`ml/dysfluency/scripts/download_sep28k.py` (or split into a small module +
CLI if that reads better — implementer's call, keep it simple):

1. **CLI**: `--episodes <csv>` (path to an episodes CSV, e.g.
   `ml/dysfluency/sep28k-src/SEP-28k_episodes.csv`), `--wavs <dir>` (output
   dir, e.g. `data/sep28k/audio/wavs`), `--retries` (default 3),
   `--timeout` (seconds, default 30).
2. **Per-episode processing**, matching Apple's existing output layout
   (`{wavs}/{show_abbrev}/{ep_idx}.wav`) so `extract_clips.py` keeps working
   unmodified against this harness's output:
   - Skip episodes whose target `.wav` already exists (resumability across
     interrupted runs — this is the main reason not to just re-run Apple's
     script, which does the same check but dies non-gracefully on the first
     bad URL).
   - Download via `requests.get(url, stream=True, allow_redirects=True,
     timeout=...)`, writing to a temp file first, moving into place only on
     full success (avoid corrupt partial files masquerading as "already
     downloaded" on next run).
   - Retry transient failures (connection errors, timeouts, 5xx) up to
     `--retries` times with a short backoff. Do NOT retry on 404/dead-domain
     — fail fast to that specific episode and move on.
   - **For any episode whose URL host is `feedproxy.google.com` AND whose
     path matches `StutteringIsCool`**: before attempting the raw URL at
     all, rewrite it to the reconstructed Blubrry URL (extract the
     `cool<N>.mp3` filename from the end of the original URL, build
     `https://media.blubrry.com/stutteringiscool/www.stutteringiscool.com/sound/cool<N>.mp3`).
     Try that instead. If it also fails, log as failed (see below) — do not
     silently fall back to something else.
   - Convert the downloaded file to 16kHz mono WAV via `ffmpeg` (subprocess,
     matching Apple's `-ac 1 -ar 16000` flags), then delete the original
     compressed file.
   - On any unrecoverable failure for an episode, log it (show, ep_idx, url
     tried, error) to a run-level failure log (e.g.
     `{wavs}/_download_failures.csv` or similar — implementer's call on
     exact format, but it must be a structured file, not just stdout, since
     this run takes hours and the log needs to survive the process),
     and continue to the next episode. Never let one dead episode abort the
     run.
3. **Progress reporting**: use `tqdm` (already a listed dependency in
   `ml/requirements.txt`) for a progress bar, plus periodic stdout summary
   (e.g. every N episodes: "X done, Y failed, Z remaining").
4. **Idempotent re-run**: running the script twice in a row (second run
   right after the first completes) should do no re-downloading and produce
   an unchanged failure log — write a quick manual check or lightweight test
   for this if practical.

## Environment notes

- Python env: `ml/.venv` (already created, has `requests`, `tqdm`, `yt-dlp`
  installed — yt-dlp is NOT needed for this task, ignore it, it was a
  speculative install that didn't pan out).
- `ffmpeg` and `wget` are both on PATH via winget installs, but per above,
  **use `requests` in Python for HTTP, not `wget` subprocess calls** — this
  is a correction to Apple's original approach, not an optional style
  choice.
- Do not attempt to actually run the full 385-episode download as part of
  this task (it's ~32GB and multi-hour) — validate against a small subset
  (e.g. first 3-5 rows of the episodes CSV, plus at least one row you
  construct/borrow that exercises the `StutteringIsCool` feedproxy fallback
  path) written to a scratch output directory, and report the validation
  results. The full run is a separate follow-up the user will kick off
  themselves once this harness is reviewed.

## Global constraints

- Do not modify `ml/dysfluency/sep28k-src/` (third-party clone) except to
  add it to `.gitignore`.
- Do not commit any downloaded audio (`data/sep28k/audio/` is already
  git-ignored — verify this stays true, don't touch that ignore rule).
- Follow the repo's existing Python style (see `backend/app/services/` or
  `ml/moshi/bridge.py` for conventions — type hints, no unnecessary
  abstraction, docstrings only where genuinely non-obvious).
- No comments explaining WHAT the code does; only WHY where a decision is
  non-obvious (e.g. why feedproxy needs special-casing, why wget was
  rejected in favor of requests).
