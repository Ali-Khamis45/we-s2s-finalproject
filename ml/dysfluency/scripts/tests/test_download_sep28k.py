from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from download_sep28k import (
    Episode,
    parse_episodes_csv,
    rewrite_feedproxy_url,
    download_episode,
    append_failure,
    run_downloads,
)


def test_parse_episodes_csv_reads_comma_space_delimited_rows(tmp_path: Path) -> None:
    csv_path = tmp_path / "episodes.csv"
    csv_path.write_text(
        "He_Stutters_Podcast, ep-slug, https://example.com/a.mp3, HeStutters, 0\n"
        "Some_Show, ep-slug-2, https://example.com/b.mp3, SomeShow, 1\n",
        encoding="utf-8",
    )

    episodes = parse_episodes_csv(csv_path)

    assert episodes == [
        Episode(show_name="He_Stutters_Podcast", url="https://example.com/a.mp3",
                show_abbrev="HeStutters", ep_idx="0"),
        Episode(show_name="Some_Show", url="https://example.com/b.mp3",
                show_abbrev="SomeShow", ep_idx="1"),
    ]


def test_rewrite_feedproxy_url_reconstructs_blubrry_url_from_cool_filename() -> None:
    dead_url = "http://feedproxy.google.com/~r/StutteringIsCool/~5/tTYb1Z7G9Ss/cool104.mp3"

    rewritten = rewrite_feedproxy_url(dead_url)

    assert rewritten == (
        "https://media.blubrry.com/stutteringiscool/"
        "www.stutteringiscool.com/sound/cool104.mp3"
    )


def test_rewrite_feedproxy_url_returns_none_for_unrelated_feedproxy_show() -> None:
    other_show_url = "http://feedproxy.google.com/~r/SomeOtherShow/~5/abc123/episode9.mp3"

    assert rewrite_feedproxy_url(other_show_url) is None


def test_rewrite_feedproxy_url_returns_none_for_non_feedproxy_host() -> None:
    normal_url = "https://media.blubrry.com/stuttertalk/traffic.libsyn.com/foo.mp3"

    assert rewrite_feedproxy_url(normal_url) is None


def _fake_response(status_code: int = 200, chunks: list[bytes] | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.iter_content.return_value = chunks if chunks is not None else [b"fake-mp3-bytes"]
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        err = requests.HTTPError(f"{status_code} error")
        err.response = resp
        resp.raise_for_status.side_effect = err
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _episode(show_abbrev: str = "HeStutters", ep_idx: str = "0",
             url: str = "https://example.com/a.mp3") -> Episode:
    return Episode(show_name="Show", url=url, show_abbrev=show_abbrev, ep_idx=ep_idx)


def _fake_ffmpeg_run(*args, **kwargs) -> MagicMock:
    """subprocess.run mock that actually creates ffmpeg's output file,
    matching real ffmpeg's contract closely enough for download_episode's
    file-move step to have something to move."""
    cmd = args[0]
    out_path = Path(cmd[-1])
    out_path.write_bytes(b"fake-wav-bytes")
    return MagicMock(returncode=0)


@patch("download_sep28k.subprocess.run")
@patch("download_sep28k.requests.get")
def test_download_episode_passes_explicit_wav_format_to_ffmpeg(mock_get, mock_run, tmp_path: Path) -> None:
    # ffmpeg infers its output muxer from the filename extension, and the
    # tmp wav path ends in ".tmp" (the ".wav" is mid-filename, deliberately,
    # so it isn't mistaken for a finished file on a killed run) -- ffmpeg
    # then refuses with "Unable to choose an output format" unless told
    # explicitly. Caught by real-network validation, not guessed upfront.
    mock_get.return_value = _fake_response(200)
    mock_run.side_effect = _fake_ffmpeg_run

    result = download_episode(_episode(), tmp_path, retries=3, timeout=30)

    assert result.success
    ffmpeg_args = mock_run.call_args.args[0]
    assert "-f" in ffmpeg_args
    assert ffmpeg_args[ffmpeg_args.index("-f") + 1] == "wav"


@patch("download_sep28k.time.sleep")
@patch("download_sep28k.subprocess.run")
@patch("download_sep28k.requests.get")
def test_download_episode_retries_timeout_then_succeeds(mock_get, mock_run, mock_sleep, tmp_path: Path) -> None:
    mock_get.side_effect = [requests.exceptions.Timeout(), _fake_response(200)]
    mock_run.side_effect = _fake_ffmpeg_run

    result = download_episode(_episode(), tmp_path, retries=3, timeout=30)

    assert result.success
    assert mock_get.call_count == 2
    wav_path = tmp_path / "HeStutters" / "0.wav"
    assert wav_path.exists()
    assert mock_run.call_count == 1


@patch("download_sep28k.subprocess.run")
@patch("download_sep28k.requests.get")
def test_download_episode_does_not_retry_on_404(mock_get, mock_run, tmp_path: Path) -> None:
    mock_get.return_value = _fake_response(404)

    result = download_episode(_episode(), tmp_path, retries=3, timeout=30)

    assert not result.success
    assert mock_get.call_count == 1
    assert mock_run.call_count == 0
    assert "404" in result.error or "HTTPError" in result.error


@patch("download_sep28k.subprocess.run")
@patch("download_sep28k.requests.get")
def test_download_episode_uses_blubrry_rewrite_for_stuttering_is_cool_feedproxy(
    mock_get, mock_run, tmp_path: Path
) -> None:
    dead_url = "http://feedproxy.google.com/~r/StutteringIsCool/~5/tTYb1Z7G9Ss/cool104.mp3"
    mock_get.return_value = _fake_response(200)
    mock_run.side_effect = _fake_ffmpeg_run

    result = download_episode(
        _episode(show_abbrev="StutteringIsCool", ep_idx="4", url=dead_url),
        tmp_path, retries=3, timeout=30,
    )

    assert result.success
    called_url = mock_get.call_args.args[0]
    assert called_url == (
        "https://media.blubrry.com/stutteringiscool/"
        "www.stutteringiscool.com/sound/cool104.mp3"
    )


@patch("download_sep28k.subprocess.run")
@patch("download_sep28k.requests.get")
def test_download_episode_logs_failure_when_blubrry_rewrite_also_fails(
    mock_get, mock_run, tmp_path: Path
) -> None:
    dead_url = "http://feedproxy.google.com/~r/StutteringIsCool/~5/tTYb1Z7G9Ss/cool104.mp3"
    mock_get.return_value = _fake_response(404)

    result = download_episode(
        _episode(show_abbrev="StutteringIsCool", ep_idx="4", url=dead_url),
        tmp_path, retries=3, timeout=30,
    )

    assert not result.success
    assert mock_run.call_count == 0


@patch("download_sep28k.subprocess.run")
@patch("download_sep28k.requests.get")
def test_download_episode_skips_when_wav_already_exists(mock_get, mock_run, tmp_path: Path) -> None:
    existing = tmp_path / "HeStutters" / "0.wav"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"already here")

    result = download_episode(_episode(), tmp_path, retries=3, timeout=30)

    assert result.success
    assert result.skipped
    mock_get.assert_not_called()
    mock_run.assert_not_called()


@patch("download_sep28k.time.sleep")
@patch("download_sep28k.subprocess.run")
@patch("download_sep28k.requests.get")
def test_download_episode_exhausts_retries_on_persistent_timeout(mock_get, mock_run, mock_sleep, tmp_path: Path) -> None:
    mock_get.side_effect = requests.exceptions.Timeout()

    result = download_episode(_episode(), tmp_path, retries=3, timeout=30)

    assert not result.success
    assert mock_get.call_count == 3
    assert mock_run.call_count == 0


def test_append_failure_writes_header_once_then_appends_rows(tmp_path: Path) -> None:
    log_path = tmp_path / "_download_failures.csv"

    append_failure(log_path, _episode(show_abbrev="ShowA", ep_idx="1"), "https://x/a.mp3", "TimeoutError: boom")
    append_failure(log_path, _episode(show_abbrev="ShowB", ep_idx="2"), "https://x/b.mp3", "HTTPError: 404")

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "show_abbrev,ep_idx,url_tried,error"
    assert len(lines) == 3
    assert lines[1].startswith("ShowA,1,")
    assert lines[2].startswith("ShowB,2,")


@patch("download_sep28k.time.sleep")
@patch("download_sep28k.subprocess.run")
@patch("download_sep28k.requests.get")
def test_run_downloads_second_run_is_a_noop_and_failure_log_unchanged(
    mock_get, mock_run, mock_sleep, tmp_path: Path
) -> None:
    episodes = [
        _episode(show_abbrev="ShowA", ep_idx="0", url="https://x/a.mp3"),
        _episode(show_abbrev="ShowB", ep_idx="0", url="https://x/dead.mp3"),
    ]

    def get_side_effect(url, **kwargs):
        if url == "https://x/dead.mp3":
            return _fake_response(404)
        return _fake_response(200)

    mock_get.side_effect = get_side_effect
    mock_run.side_effect = _fake_ffmpeg_run

    wavs_dir = tmp_path / "wavs"
    stats1 = run_downloads(episodes, wavs_dir, retries=3, timeout=30)
    failure_log = wavs_dir / "_download_failures.csv"
    first_log_contents = failure_log.read_text(encoding="utf-8")

    assert stats1.done == 1
    assert stats1.failed == 1
    assert mock_get.call_count == 2

    mock_get.reset_mock()
    mock_run.reset_mock()
    stats2 = run_downloads(episodes, wavs_dir, retries=3, timeout=30)

    assert stats2.done == 0
    assert stats2.skipped == 1
    assert stats2.failed == 1
    mock_run.assert_not_called()
    assert failure_log.read_text(encoding="utf-8") == first_log_contents


@patch("download_sep28k.subprocess.run")
@patch("download_sep28k.requests.get")
def test_download_episode_survives_mkdir_failure_and_logs_it(mock_get, mock_run, tmp_path: Path) -> None:
    # episode_dir.mkdir used to run outside the per-episode try/except, so an
    # OSError here (disk full, permissions -- realistic over a multi-hour
    # run) propagated out of download_episode entirely and would abort
    # run_downloads for every remaining episode instead of being isolated to
    # this one.
    episode = _episode(show_abbrev="HeStutters", ep_idx="0")

    with patch("download_sep28k.Path.mkdir", side_effect=OSError("disk full")):
        result = download_episode(episode, tmp_path, retries=3, timeout=30)

    assert not result.success
    assert "disk full" in result.error
    mock_get.assert_not_called()
    mock_run.assert_not_called()


@patch("download_sep28k.subprocess.run")
@patch("download_sep28k.requests.get")
def test_run_downloads_continues_past_mkdir_failure_for_remaining_episodes(
    mock_get, mock_run, tmp_path: Path
) -> None:
    episodes = [
        _episode(show_abbrev="BadShow", ep_idx="0", url="https://x/a.mp3"),
        _episode(show_abbrev="GoodShow", ep_idx="0", url="https://x/b.mp3"),
    ]
    mock_get.return_value = _fake_response(200)
    mock_run.side_effect = _fake_ffmpeg_run

    real_mkdir = Path.mkdir

    def flaky_mkdir(self, *args, **kwargs):
        if self.name == "BadShow":
            raise OSError("disk full")
        return real_mkdir(self, *args, **kwargs)

    wavs_dir = tmp_path / "wavs"
    with patch("download_sep28k.Path.mkdir", flaky_mkdir):
        stats = run_downloads(episodes, wavs_dir, retries=3, timeout=30)

    assert stats.failed == 1
    assert stats.done == 1
    failure_log = (wavs_dir / "_download_failures.csv").read_text(encoding="utf-8")
    assert "BadShow" in failure_log
    assert "disk full" in failure_log


@patch("download_sep28k.subprocess.run")
@patch("download_sep28k.requests.get")
def test_download_episode_fast_fails_feedproxy_url_with_no_blubrry_match(
    mock_get, mock_run, tmp_path: Path
) -> None:
    # 3 of the 85 StutteringIsCool feedproxy episodes have non-numeric
    # filenames (e.g. Stuttering-is-Cool-1st-episode.mp3) that don't match
    # the cool<N>.mp3 rewrite pattern. feedproxy.google.com is permanently
    # shut down -- there is no point burning a full retry/timeout cycle
    # against it when no rewrite is available.
    dead_url = (
        "http://feedproxy.google.com/~r/StutteringIsCool/~5/6ctZYoFLT1o/"
        "Stuttering-is-Cool-1st-episode.mp3"
    )
    assert rewrite_feedproxy_url(dead_url) is None  # sanity: no rewrite exists

    result = download_episode(
        _episode(show_abbrev="StutteringIsCool", ep_idx="0", url=dead_url),
        tmp_path, retries=3, timeout=30,
    )

    assert not result.success
    assert "feedproxy.google.com" in result.error
    assert "permanently shut down" in result.error
    mock_get.assert_not_called()
    mock_run.assert_not_called()


@patch("download_sep28k.time.sleep")
@patch("download_sep28k.subprocess.run")
@patch("download_sep28k.requests.get")
def test_download_episode_retries_chunked_encoding_error_then_succeeds(
    mock_get, mock_run, mock_sleep, tmp_path: Path
) -> None:
    mock_get.side_effect = [requests.exceptions.ChunkedEncodingError(), _fake_response(200)]
    mock_run.side_effect = _fake_ffmpeg_run

    result = download_episode(_episode(), tmp_path, retries=3, timeout=30)

    assert result.success
    assert mock_get.call_count == 2


def test_run_downloads_removes_stale_temp_files_from_a_killed_run(tmp_path: Path) -> None:
    # A process killed between a successful raw download and a successful
    # ffmpeg conversion leaves an orphaned temp file behind (only cleaned up
    # on success or on an exception within the same attempt). Over many
    # hours-long resumed runs these should not just accumulate forever.
    wavs_dir = tmp_path / "wavs"
    show_dir = wavs_dir / "HeStutters"
    show_dir.mkdir(parents=True)
    stale_download = show_dir / "tmpABC123.mp3"
    stale_download.write_bytes(b"orphaned")
    stale_wav = show_dir / ".0.wav.tmp"
    stale_wav.write_bytes(b"orphaned")

    run_downloads([], wavs_dir, retries=3, timeout=30)

    assert not stale_download.exists()
    assert not stale_wav.exists()
