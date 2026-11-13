import copy
import math

import pytest

from storyhouse.episode import Episode, EpisodeError, validate_episode


def test_episode_is_valid_and_40_seconds(episode):
    assert episode.duration == 40.0
    assert sum(s["duration"] for s in episode.scenes) == pytest.approx(40.0)
    assert (episode.fmt["width"], episode.fmt["height"]) == (1080, 1920)


def test_scenes_are_contiguous(episode):
    cursor = 0.0
    for s in episode.scenes:
        assert s["start"] == pytest.approx(cursor)
        cursor += s["duration"]


def test_render_durations_cover_scenes_and_runway_limits(episode):
    for s in episode.scenes:
        assert 2 <= s["render_duration"] <= 10
        assert s["render_duration"] >= math.ceil(s["duration"])


def test_hook_within_first_three_seconds(episode):
    first = episode.scenes[0]
    assert first["beat"] == "hook" and first["duration"] <= 3.0
    assert episode.dialogue[0]["at"] < 1.0, "Erste Zeile muss sofort kommen"


def test_twist_and_loop_present(episode):
    beats = [s["beat"] for s in episode.scenes]
    assert "twist" in beats and beats[-1] == "loop"
    last = episode.scenes[-1]
    assert last["loop"]["match_frame_with"] == episode.scenes[0]["id"]
    assert last["loop"]["no_fade_out"] is True
    bridge = [d for d in episode.dialogue if d.get("loop_bridge")]
    assert len(bridge) == 1 and bridge[0]["scene"] == last["id"]


def test_dialogue_is_english_and_in_scene(episode):
    for d in episode.dialogue:
        assert d["text"].isascii() or "—" in d["text"]
        sc = episode.scene(d["scene"])
        assert sc["start"] <= d["at"] < sc["start"] + sc["duration"]


def test_prompts_fit_runway_limit(episode):
    for s in episode.scenes:
        assert len(f"{s['keyframe_prompt']} {episode.global_style}") <= 1000
        assert len(s["motion_prompt"]) <= 1000


def test_validation_catches_gap(episode):
    data = copy.deepcopy(episode.data)
    data["scenes"][2]["start"] += 0.5
    with pytest.raises(EpisodeError, match="Lücke"):
        validate_episode(Episode(path=episode.path, data=data))


def test_validation_catches_unknown_sfx(episode):
    data = copy.deepcopy(episode.data)
    data["scenes"][0]["sfx"].append("does_not_exist")
    with pytest.raises(EpisodeError, match="does_not_exist"):
        validate_episode(Episode(path=episode.path, data=data))


def test_subset_retimes_scenes(episode):
    sub = episode.subset("S05-S06")
    assert [s["id"] for s in sub.scenes] == ["S05", "S06"]
    assert sub.scenes[0]["start"] == 0.0
    assert sub.duration == pytest.approx(11.5)
    assert sub.dialogue[0]["at"] == pytest.approx(0.4)
    assert sub.sound["silence_windows"][0]["from"] == pytest.approx(25.6 - 24.0 + 6.0)


def test_scene_selector_errors(episode):
    with pytest.raises(EpisodeError):
        episode.select_scenes("S99")
