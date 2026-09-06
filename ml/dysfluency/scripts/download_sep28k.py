"""Fetch SEP-28k / FluencyBank source podcast audio and convert to 16kHz mono WAV.

Replaces running Apple's ml-stuttering-events-dataset `download_audio.py`
directly: that script uses raw `wget` subprocess calls with no retry, no
resumability, and aborts the whole run on the first dead link. This harness
does the same job (same CSV format, same output layout, so
`ml/dysfluency/sep28k-src/extract_clips.py` keeps working against its output
unmodified) but tolerates a slow, flaky, multi-hour fetch over 385 episodes.

See docs/superpowers/specs/2026-09-06-m3-sep28k-download-spec.md.
"""
from __future__ import annotations

import argparse
import csv
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests
from tqdm import tqdm

AUDIO_EXTENSIONS = (".mp3", ".m4a", ".mp4")
RETRY_BACKOFF_SECONDS = 2.0
FAILURE_LOG_NAME = "_download_failures.csv"
FAILURE_LOG_HEADER = ["show_abbrev", "ep_idx", "url_tried", "error"]
PROGRESS_REPORT_EVERY = 25

# feedproxy.google.com (Google FeedBurner) has been shut down entirely --
# these requests time out rather than redirect. Stuttering_is_Cool's live
# feed kept the same cool<N>.mp3 naming on Blubrry, so the dead URL's
# trailing filename can be reconstructed into a working one. Verified
# working via curl -L for cool159 and cool227 (see spec).
_STUTTERING_IS_COOL_PATH = re.compile(r"/StutteringIsCool/")
_COOL_FILENAME = re.compile(r"(cool\d+\.mp3)$")


@dataclass(frozen=True)
class Episode:
    show_name: str
    url: str
    show_abbrev: str
    ep_idx: str


def parse_episodes_csv(path: Path) -> list[Episode]:
    episodes = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            fields = line.split(", ")
            episodes.append(
                Episode(
                    show_name=fields[0],
                    url=fields[2],
                    show_abbrev=fields[-2],
                    ep_idx=fields[-1],
                )
            )
    return episodes


def rewrite_feedproxy_url(url: str) -> str | None:
    """Return the reconstructed Blubrry URL for a dead StutteringIsCool
    feedproxy link, or None if `url` isn't one of those."""
    host = urlparse(url).netloc
    if host != "feedproxy.google.com" or not _STUTTERING_IS_COOL_PATH.search(url):
        return None
    match = _COOL_FILENAME.search(url)
    if not match:
        return None
    return (
        "https://media.blubrry.com/stutteringiscool/"
        f"www.stutteringiscool.com/sound/{match.group(1)}"
    )


@dataclass
class DownloadResult:
    success: bool
    skipped: bool = False
    error: str = ""
    url_tried: str = ""


def _audio_extension(url: str) -> str:
    for ext in AUDIO_EXTENSIONS:
        if ext in url:
            return ext
    return ".mp3"


def _fetch_to_file(url: str, dest: Path, timeout: float) -> None:
    """Raises on any failure; caller decides retryability."""
    with requests.get(url, stream=True, allow_redirects=True, timeout=timeout) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 64):
                if chunk:
                    f.write(chunk)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(
        exc,
        (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ),
    ):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        return status is not None and status >= 500
    return False


class _PermanentlyDeadHost(Exception):
    """Raised to fail fast on a host known to be permanently shut down,
    skipping the retry loop entirely instead of burning a full
    retry/timeout cycle against a domain that will never answer."""


def download_episode(
    episode: Episode,
    wavs_dir: Path,
    *,
    retries: int = 3,
    timeout: float = 30,
) -> DownloadResult:
    """Fetch one episode's source audio and convert it to 16kHz mono WAV.

    Matches Apple's download_audio.py output layout exactly
    ({wavs_dir}/{show_abbrev}/{ep_idx}.wav) so extract_clips.py keeps
    working unmodified. Downloads to a temp file first and only moves the
    final WAV into place on full success, so a killed run never leaves a
    corrupt file that a resumed run mistakes for "already done".
    """
    episode_dir = wavs_dir / episode.show_abbrev
    wav_path = episode_dir / f"{episode.ep_idx}.wav"
    if wav_path.exists():
        return DownloadResult(success=True, skipped=True)

    url = episode.url
    rewritten = rewrite_feedproxy_url(url)
    if rewritten is not None:
        url = rewritten

    ext = _audio_extension(url)
    last_error: Exception | None = None
    for attempt in range(retries):
        tmp_download = None
        try:
            # mkdir belongs inside the per-attempt try/except, not before the
            # loop: an OSError here (disk full, permissions) is just as
            # realistic over a multi-hour run as a download failure, and
            # must not propagate out and abort every remaining episode.
            episode_dir.mkdir(parents=True, exist_ok=True)

            if rewritten is None and urlparse(episode.url).netloc == "feedproxy.google.com":
                raise _PermanentlyDeadHost(
                    "feedproxy.google.com is permanently shut down and no "
                    "Blubrry rewrite pattern matched this filename"
                )

            with tempfile.NamedTemporaryFile(
                dir=episode_dir, suffix=ext, delete=False
            ) as tmp:
                tmp_download = Path(tmp.name)
            _fetch_to_file(url, tmp_download, timeout)

            # ffmpeg picks its output muxer from the filename extension, and
            # ".wav.tmp" ends in ".tmp" -- confirmed by real-network
            # validation to fail with "Unable to choose an output format"
            # even though the exact same command against a plain ".wav"
            # path succeeds. -f wav makes the format explicit instead of
            # relying on a trailing extension we deliberately don't want to
            # be ".wav" yet (that's the resumability marker).
            tmp_wav = episode_dir / f".{episode.ep_idx}.wav.tmp"
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(tmp_download), "-ac", "1", "-ar", "16000",
                 "-f", "wav", str(tmp_wav)],
                check=True,
                capture_output=True,
            )
            tmp_download.unlink(missing_ok=True)
            tmp_wav.replace(wav_path)
            return DownloadResult(success=True, url_tried=url)
        except Exception as exc:  # noqa: BLE001 -- classified below via _is_retryable
            last_error = exc
            if tmp_download is not None:
                tmp_download.unlink(missing_ok=True)
            if not _is_retryable(exc):
                break
            if attempt < retries - 1:
                time.sleep(RETRY_BACKOFF_SECONDS)

    return DownloadResult(
        success=False,
        error=f"{type(last_error).__name__}: {last_error}",
        url_tried=url,
    )


def append_failure(log_path: Path, episode: Episode, url_tried: str, error: str) -> None:
    is_new = not log_path.exists()
    with open(log_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(FAILURE_LOG_HEADER)
        writer.writerow([episode.show_abbrev, episode.ep_idx, url_tried, error])


@dataclass
class RunStats:
    done: int = 0
    skipped: int = 0
    failed: int = 0


def _clean_stale_temp_files(wavs_dir: Path) -> None:
    """Remove leftover download/conversion temp files from a prior run that
    was killed between steps (e.g. after a successful raw download but
    before ffmpeg finished) -- otherwise they just accumulate as garbage
    across many hours-long resumed runs. Simple disk hygiene, not
    correctness-critical: a live run's own in-flight temp files are only
    ever present between calls to run_downloads, never while this runs."""
    if not wavs_dir.exists():
        return
    for pattern in ("tmp*", ".*.wav.tmp"):
        for stale in wavs_dir.glob(f"*/{pattern}"):
            stale.unlink(missing_ok=True)


def run_downloads(
    episodes: list[Episode],
    wavs_dir: Path,
    *,
    retries: int = 3,
    timeout: float = 30,
) -> RunStats:
    wavs_dir.mkdir(parents=True, exist_ok=True)
    _clean_stale_temp_files(wavs_dir)
    failure_log = wavs_dir / FAILURE_LOG_NAME
    # Truncate at the start of each run rather than appending across runs --
    # otherwise a still-dead episode logs a fresh duplicate row every re-run,
    # breaking the "second run produces an unchanged failure log" contract.
    failure_log.unlink(missing_ok=True)
    stats = RunStats()

    for episode in tqdm(episodes, desc="episodes"):
        result = download_episode(episode, wavs_dir, retries=retries, timeout=timeout)
        if result.skipped:
            stats.skipped += 1
        elif result.success:
            stats.done += 1
        else:
            stats.failed += 1
            append_failure(failure_log, episode, result.url_tried, result.error)

        completed = stats.done + stats.skipped + stats.failed
        if completed % PROGRESS_REPORT_EVERY == 0:
            remaining = len(episodes) - completed
            tqdm.write(
                f"{stats.done + stats.skipped} done, {stats.failed} failed, "
                f"{remaining} remaining"
            )

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download SEP-28k/FluencyBank source audio and convert to 16kHz mono WAV."
    )
    parser.add_argument("--episodes", required=True, help="Path to an episodes CSV")
    parser.add_argument("--wavs", required=True, help="Output directory for WAV files")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()

    episodes = parse_episodes_csv(Path(args.episodes))
    stats = run_downloads(
        episodes, Path(args.wavs), retries=args.retries, timeout=args.timeout
    )
    print(f"Finished: {stats.done} downloaded, {stats.skipped} skipped, {stats.failed} failed")


if __name__ == "__main__":
    main()
