import json
from pathlib import Path

import pytest

from storyhouse import ffmpeg as ff
from storyhouse.subtitles import build_cues, to_ass, to_srt
from tests.conftest import requires_ffmpeg


def test_cues_do_not_overlap_and_are_readable(episode):
    cues = build_cues(episode)
    style = episode.subtitle_style
    for a, b in zip(cues, cues[1:]):
        assert a.end <= b.start
    for c in cues:
        assert c.end - c.start >= style["min_display_s"] - 1e-6
        assert len(c.text) / (c.end - c.start) <= style["chars_per_second"]
    assert cues[-1].end == pytest.approx(40.0), "Loop-Satz läuft bis zum letzten Frame"


def test_srt_format(episode):
    srt = to_srt(build_cues(episode))
    assert srt.startswith("1\n00:00:00,350 --> ")
    assert "Don't turn around." in srt


def test_ass_respects_tiktok_safe_zone(episode):
    ass = to_ass(build_cues(episode), episode)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    style_line = next(l for l in ass.splitlines() if l.startswith("Style: Default"))
    margin_v = int(style_line.split(",")[-2])
    assert margin_v >= 400


def test_mix_command_contains_ducking_silence_and_loudnorm(tmp_path):
    tracks = [
        ff.Track(Path("d.wav"), 0.35, 0, "dialogue", "highpass=f=180"),
        ff.Track(Path("drone.wav"), 0.0, -18, "bed", fill=True),
        ff.Track(Path("sfx.wav"), 9.0, -16, "bed"),
    ]
    cmd = ff.build_mix_command(tracks, 40.0, tmp_path / "m.wav", [(25.6, 27.4)])
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "sidechaincompress" in graph
    assert "adelay=350|350" in graph and "adelay=9000|9000" in graph
    assert "between(t,25.600,27.400)" in graph
    assert "loudnorm=I=-14.0:TP=-1.0" in graph
    assert "-stream_loop" in cmd
    assert "afade" not in graph, "Keine Fades – sonst hört man die Loop-Naht"


@requires_ffmpeg
@pytest.mark.ffmpeg
def test_dry_run_preview_end_to_end(episode, pricing, cfg, tmp_path):
    from storyhouse.pipeline import dry_run

    m = dry_run(episode, tmp_path, pricing, cfg, scenes="S05-S06", width=180, height=320, fps=12)
    assert m["paid_api_calls"] == 0 and m["spent_eur"] == 0
    assert m["probe"]["width"] == 180 and m["probe"]["height"] == 320
    assert m["probe"]["duration"] == pytest.approx(11.5, abs=0.1)
    assert m["probe"]["audio_codec"] == "aac" and m["probe"]["video_codec"] == "h264"
    assert Path(m["srt"]).exists()
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["mode"] == "dry-run"
    assert (tmp_path / "estimate.md").read_text().startswith("Kostenschätzung")
