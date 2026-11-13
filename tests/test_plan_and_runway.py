import json

import pytest

from storyhouse.budget import BudgetError, SpendGuard
from storyhouse.plan import LAST_FRAME_MODELS, build_plan
from storyhouse.runway import RunwayClient, RunwayError, resolve_placeholders


# ---------------------------------------------------------------- Plan
def test_plan_structure(episode, pricing):
    plan = build_plan(episode, pricing, profile="final")
    ids = [j.id for j in plan.jobs]
    assert ids[:2] == ["REF_maya", "REF_bathroom"]
    assert sum(1 for i in ids if i.startswith("KF_")) == 8
    assert sum(1 for i in ids if i.startswith("VID_")) == 8
    assert sum(1 for i in ids if i.startswith("TTS_")) == len(episode.dialogue)
    # Abhängigkeiten stehen immer vor dem Job
    seen = set()
    for j in plan.jobs:
        assert set(j.depends) <= seen, j.id
        seen.add(j.id)


def test_video_jobs_use_vertical_ratio_and_render_duration(episode, pricing):
    plan = build_plan(episode, pricing, profile="test")
    for j in plan.jobs:
        if j.kind == "video":
            assert j.payload["ratio"] == "720:1280"
            assert j.payload["duration"] == episode.scene(j.scene)["render_duration"]
            assert j.credits == 5 * j.payload["duration"]


def test_loop_scene_gets_last_frame_of_opening(episode, pricing):
    plan = build_plan(episode, pricing, profile="final")
    vid = next(j for j in plan.jobs if j.id == "VID_S08")
    assert "gen4.5" in LAST_FRAME_MODELS
    assert vid.payload["promptImage"][1] == {"uri": "{{out:KF_S01}}", "position": "last"}


def test_subset_plan_only_contains_needed_jobs(episode, pricing):
    plan = build_plan(episode, pricing, profile="test", scenes="S06")
    assert {j.kind for j in plan.jobs} == {"image", "video", "tts", "sfx"} - {"tts"}
    assert all(j.scene in (None, "S06") for j in plan.jobs)


def test_no_audio_flag(episode, pricing):
    plan = build_plan(episode, pricing, scenes="S01", include_audio=False)
    assert {j.kind for j in plan.jobs} == {"image", "video"}


# ---------------------------------------------------------------- Client
class FakeTransport:
    def __init__(self, task_states=("RUNNING", "SUCCEEDED")):
        self.calls = []
        self.states = list(task_states)

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, json.loads(body) if body else None))
        if method == "POST":
            return 200, {"id": "task-1"}, {}
        if url.endswith("/v1/organization"):
            return 200, {"creditBalance": 1000}, {}
        state = self.states.pop(0)
        out = {"status": state}
        if state == "SUCCEEDED":
            out["output"] = ["https://cdn.example/out.mp4"]
        if state == "FAILED":
            out["failure"] = "moderation"
        return 200, out, {}


def _client(pricing, cfg, transport, limit=5.0):
    guard = SpendGuard(limit_eur=limit, pricing=pricing, cfg=cfg)
    return RunwayClient("key_test_secret", guard, transport=transport, sleep=lambda s: None), guard


def test_client_sends_auth_and_version_headers(episode, pricing, cfg):
    plan = build_plan(episode, pricing, scenes="S01", include_audio=False)
    t = FakeTransport()
    client, guard = _client(pricing, cfg, t)
    job = plan.jobs[0]
    assert client.run_job(job, {}) == "https://cdn.example/out.mp4"
    method, url, headers, body = t.calls[0]
    assert method == "POST" and url.endswith("/v1/text_to_image")
    assert headers["Authorization"] == "Bearer key_test_secret"
    assert headers["X-Runway-Version"] == "2024-11-06"
    assert guard.spent_credits == job.credits


def test_guard_blocks_before_any_request(episode, pricing, cfg):
    plan = build_plan(episode, pricing, profile="final", scenes="S05", include_audio=False)
    vid = next(j for j in plan.jobs if j.kind == "video")
    t = FakeTransport()
    client, _ = _client(pricing, cfg, t, limit=0.10)
    with pytest.raises(BudgetError):
        client.run_job(vid, {"KF_S05": "https://x/kf.png"})
    assert t.calls == [], "Bei Budgetüberschreitung darf kein Request rausgehen"


def test_failed_task_is_not_resubmitted(episode, pricing, cfg):
    plan = build_plan(episode, pricing, scenes="S01", include_audio=False)
    t = FakeTransport(task_states=("FAILED",))
    client, _ = _client(pricing, cfg, t)
    with pytest.raises(RunwayError, match="NICHT automatisch"):
        client.run_job(plan.jobs[0], {})
    assert sum(1 for c in t.calls if c[0] == "POST") == 1


def test_secret_not_in_repr(pricing, cfg):
    client, _ = _client(pricing, cfg, FakeTransport())
    assert "key_test_secret" not in repr(client)


def test_client_requires_secret_and_guard(pricing, cfg):
    with pytest.raises(RunwayError):
        RunwayClient("", SpendGuard(limit_eur=1, pricing=pricing, cfg=cfg))
    with pytest.raises(BudgetError):
        RunwayClient("key", guard=None)  # type: ignore[arg-type]


def test_placeholder_resolution():
    payload = {"a": "{{out:KF_S01}}", "b": [{"uri": "{{out:REF_maya}}"}]}
    out = resolve_placeholders(payload, {"KF_S01": "https://k", "REF_maya": "https://m"})
    assert out == {"a": "https://k", "b": [{"uri": "https://m"}]}
    with pytest.raises(RunwayError):
        resolve_placeholders("{{out:MISSING}}", {})
