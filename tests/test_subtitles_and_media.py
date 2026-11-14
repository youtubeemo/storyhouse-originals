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
def test_dry_run_preview_end_to_end(episode, pricing, cfg, tmp_path, monkeypatch):
    import socket

    from storyhouse.pipeline import dry_run

    def no_network(*a, **k):
        raise AssertionError("Dry-Run darf keine Netzwerkverbindung öffnen")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.delenv("RUNWAYML_API_SECRET", raising=False)

    m = dry_run(episode, tmp_path, pricing, cfg, scenes="S05-S06", width=180, height=320, fps=12)
    assert m["paid_api_calls"] == 0 and m["spent_eur"] == 0
    assert m["probe"]["width"] == 180 and m["probe"]["height"] == 320
    outro = episode.outro_duration
    assert outro > 0 and m["outro"] is True
    assert m["probe"]["duration"] == pytest.approx(11.5 + outro, abs=0.1), "Film + StoryHouse-Outro"
    assert m["loop_probe"]["duration"] == pytest.approx(11.5, abs=0.1), "Loop-Fassung ohne Outro"
    assert Path(m["loop_video"]).exists()
    assert m["av_drift_s"] is not None and m["av_drift_s"] <= 0.1, "Bild und Ton gleich lang"
    assert m["music_source"] == "generated", "Musikpfad wird im Dry-Run geprobt"
    assert [x["id"] for x in m["sync"]] == ["D03", "D04"]
    assert all(x["fitted_s"] <= x["slot_s"] + 0.02 for x in m["sync"])
    assert m["probe"]["audio_codec"] == "aac" and m["probe"]["video_codec"] == "h264"
    assert Path(m["srt"]).exists()
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["mode"] == "dry-run"
    assert (tmp_path / "estimate.md").read_text().startswith("Kostenschätzung")


def test_cues_follow_actual_speech_length(episode):
    """Spricht die Stimme länger als geschätzt, bleibt der Untertitel entsprechend länger stehen."""
    base = build_cues(episode)
    longer = build_cues(episode, {"D05": 3.0})
    d05 = next(i for i, l in enumerate(episode.dialogue) if l["id"] == "D05")
    assert longer[d05].end - longer[d05].start >= 3.2
    assert longer[d05].end > base[d05].end
    for a, b in zip(longer, longer[1:]):
        assert a.end <= b.start


@requires_ffmpeg
def test_dialogue_is_trimmed_and_fitted_into_slot(tmp_path):
    """Stille wird abgeschnitten; zu lange Zeilen werden beschleunigt und notfalls gekürzt."""
    src = tmp_path / "line.wav"
    ff.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=0.5",
            "-f", "lavfi", "-i", "sine=frequency=300:duration=2:sample_rate=48000",
            "-filter_complex", "[0][1]concat=n=2:v=0:a=1,apad=pad_dur=0.5", str(src)])
    ok = ff.fit_dialogue(src, tmp_path / "ok.wav", slot_s=2.5)
    assert ok["raw_s"] == pytest.approx(3.0, abs=0.05)
    assert ok["spoken_s"] == pytest.approx(2.0, abs=0.1), "Stille vorn/hinten entfernt"
    assert ok["tempo"] == 1.0 and not ok["cut"]
    fast = ff.fit_dialogue(src, tmp_path / "fast.wav", slot_s=1.8)
    assert 1.0 < fast["tempo"] <= ff.MAX_TEMPO and not fast["cut"]
    assert fast["fitted_s"] <= 1.85
    cut = ff.fit_dialogue(src, tmp_path / "cut.wav", slot_s=1.0)
    assert cut["tempo"] == ff.MAX_TEMPO and cut["cut"]
    assert cut["fitted_s"] == pytest.approx(1.0, abs=0.03)


@requires_ffmpeg
def test_outro_card_and_audio_have_exact_length(episode, tmp_path):
    card = ff.outro_clip(episode.outro, tmp_path / "outro.mp4", 180, 320, 12)
    info = ff.probe(card)
    assert (info["width"], info["height"]) == (180, 320)
    assert info["duration"] == pytest.approx(episode.outro_duration, abs=0.1)
    tail = ff.outro_audio(None, tmp_path / "outro.wav", episode.outro_duration)
    assert ff.media_duration(tail) == pytest.approx(episode.outro_duration, abs=0.02)


def test_music_track_fades_only_itself(tmp_path):
    tracks = [ff.Track(Path("drone.wav"), 0.0, -18, "bed", fill=True),
              ff.Track(Path("score.mp3"), 0.0, -16, "bed", edge_fade=0.4)]
    cmd = ff.build_mix_command(tracks, 40.0, tmp_path / "m.wav")
    graph = cmd[cmd.index("-filter_complex") + 1]
    drone, score = graph.split(";")[:2]
    assert "afade" not in drone, "Drone bleibt an der Loop-Naht durchgehend"
    assert "afade=t=in:d=0.400" in score and "afade=t=out:st=39.600" in score
