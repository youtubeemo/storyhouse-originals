import copy
import json
import socket
import urllib.error

import pytest

from storyhouse.budget import BudgetError, PriceDriftError, SpendGuard
from storyhouse.episode import Episode
from storyhouse.plan import build_plan
from storyhouse.runway import RunwayClient, RunwayError, UncertainSubmissionError, resolve_placeholders
from storyhouse.runway_spec import (
    SpecError, snap_duration, supports_last_frame, validate_payload,
)


# ---------------------------------------------------------------- Spezifikation
def test_gen4_models_do_not_support_last_frame():
    """Offizielle SDK-Spezifikation: gen4.5/gen4_turbo nur position 'first'."""
    assert not supports_last_frame("gen4.5")
    assert not supports_last_frame("gen4_turbo")
    assert supports_last_frame("veo3.1_fast")


def test_last_frame_payload_rejected_for_gen45():
    payload = {"model": "gen4.5", "promptText": "x", "ratio": "720:1280", "duration": 5,
               "promptImage": [{"uri": "data:a", "position": "first"}, {"uri": "data:b", "position": "last"}]}
    with pytest.raises(SpecError, match="Positionen"):
        validate_payload("video", payload)


def test_duration_snapping():
    assert snap_duration("gen4.5", 4.5) == 5
    assert snap_duration("veo3.1_fast", 4.5) == 6
    with pytest.raises(SpecError):
        snap_duration("veo3.1_fast", 9)


def test_veo_requires_explicit_audio_false():
    p = {"model": "veo3.1_fast", "promptImage": "data:x", "ratio": "720:1280", "duration": 6}
    with pytest.raises(SpecError, match="audio"):
        validate_payload("video", p)
    validate_payload("video", {**p, "audio": False})


def test_image_ratio_and_tags():
    base = {"model": "gen4_image", "promptText": "x", "ratio": "1080:1920"}
    validate_payload("image", {**base, "referenceImages": [{"uri": "u", "tag": "maya"}]})
    with pytest.raises(SpecError):
        validate_payload("image", {**base, "referenceImages": [{"uri": "u", "tag": "my-tag"}]})
    with pytest.raises(SpecError):
        validate_payload("image", {**base, "ratio": "9:16"})
    with pytest.raises(SpecError, match="benötigt"):
        validate_payload("image", {**base, "model": "gen4_image_turbo"})


def test_tts_preset_and_tags():
    ok = {"model": "eleven_v3", "promptText": "[whispers] hi", "voice": {"type": "runway-preset", "presetId": "Maya"}}
    validate_payload("tts", ok)
    with pytest.raises(SpecError):
        validate_payload("tts", {**ok, "voice": {"type": "runway-preset", "presetId": "Nobody"}})
    with pytest.raises(SpecError, match="eleven_v3"):
        validate_payload("tts", {**ok, "model": "eleven_multilingual_v2"})


def test_sfx_requires_duration():
    with pytest.raises(SpecError):
        validate_payload("sfx", {"model": "eleven_text_to_sound_v2", "promptText": "x"})


# ---------------------------------------------------------------- Plan
def test_plan_structure_and_every_payload_valid(episode, pricing):
    for profile in ("test", "final"):
        plan = build_plan(episode, pricing, profile=profile)
        ids = [j.id for j in plan.jobs]
        assert ids[:2] == ["REF_maya", "REF_bathroom"]
        assert sum(1 for i in ids if i.startswith("VID_")) == 8
        seen = set()
        for j in plan.jobs:
            validate_payload(j.kind, j.payload)
            assert set(j.depends) <= seen, j.id
            seen.add(j.id)


def test_default_loop_uses_ffmpeg_seam_not_unsupported_keyframe(episode, pricing):
    plan = build_plan(episode, pricing, profile="final")
    vid = next(j for j in plan.jobs if j.id == "VID_S08")
    assert plan.loop_strategy == "ffmpeg_seam"
    assert isinstance(vid.payload["promptImage"], str)
    assert vid.model == "gen4.5"


def test_keyframe_last_strategy_switches_to_capable_model(episode, pricing):
    data = copy.deepcopy(episode.data)
    data["scenes"][-1]["loop"]["strategy"] = "keyframe_last"
    ep = Episode(path=episode.path, data=data)
    plan = build_plan(ep, pricing, profile="final")
    vid = next(j for j in plan.jobs if j.id == "VID_S08")
    assert vid.model == "veo3.1_fast" and vid.payload["audio"] is False
    assert vid.payload["duration"] == 6
    assert vid.payload["promptImage"][1] == {"uri": "{{out:KF_S01}}", "position": "last"}
    assert vid.credits == 60


def test_costs_match_official_price_table(episode, pricing):
    plan = build_plan(episode, pricing, profile="final")
    for j in plan.jobs:
        if j.kind == "video":
            assert j.credits == 12 * j.payload["duration"]
        if j.kind == "image":
            assert j.credits == 8
        if j.kind == "tts":
            assert j.credits == max(1, -(-len(j.payload["promptText"]) // 50))
        if j.kind == "sfx":
            assert j.credits == -(-j.payload["duration"] // 1)


def test_subset_and_no_audio(episode, pricing):
    plan = build_plan(episode, pricing, profile="test", scenes="S06")
    assert all(j.scene in (None, "S06") for j in plan.jobs)
    plan = build_plan(episode, pricing, scenes="S01", include_audio=False)
    assert {j.kind for j in plan.jobs} == {"image", "video"}


# ---------------------------------------------------------------- Client
class FakeTransport:
    """Simuliert Runway. states: Liste von Task-Zuständen pro GET."""

    def __init__(self, task_states=("RUNNING", "SUCCEEDED"), create_status=200, api_estimate=None,
                 final_cost=None, create_exc=None, get_errors=0):
        self.calls = []
        self.states = list(task_states)
        self.create_status = create_status
        self.api_estimate = api_estimate
        self.final_cost = final_cost
        self.create_exc = create_exc
        self.get_errors = get_errors

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, json.loads(body) if body else None))
        if method == "POST":
            if self.create_exc:
                raise self.create_exc
            if self.create_status != 200:
                return self.create_status, {"error": "x"}, {"Retry-After": "0"}
            data = {"id": "task-1"}
            if self.api_estimate is not None:
                data["estimatedCost"] = {"credits": self.api_estimate}
            return 200, data, {}
        if method == "DELETE":
            return 204, None, {}
        if url.endswith("/v1/organization"):
            return 200, {"creditBalance": 1000}, {}
        if self.get_errors:
            self.get_errors -= 1
            raise urllib.error.URLError("flaky")
        state = self.states.pop(0)
        out = {"id": "task-1", "status": state}
        if state in ("SUCCEEDED", "FAILED", "CANCELLED") and self.final_cost is not None:
            out["cost"] = {"credits": self.final_cost}
        if state == "SUCCEEDED":
            out["output"] = ["https://cdn.example/out.mp4"]
        if state == "FAILED":
            out["failure"] = "moderation"
        return 200, out, {}


def _client(pricing, cfg, transport, limit=5.0):
    guard = SpendGuard(limit_eur=limit, pricing=pricing, cfg=cfg)
    return RunwayClient("key_test_secret", guard, transport=transport, sleep=lambda s: None), guard


def _image_job(episode, pricing):
    return build_plan(episode, pricing, scenes="S01", include_audio=False).jobs[0]


def test_client_headers_and_final_cost_from_api(episode, pricing, cfg):
    t = FakeTransport(api_estimate=8, final_cost=7)
    client, guard = _client(pricing, cfg, t)
    assert client.run_job(_image_job(episode, pricing), {}) == "https://cdn.example/out.mp4"
    method, url, headers, _ = t.calls[0]
    assert method == "POST" and url.endswith("/v1/text_to_image")
    assert headers["Authorization"] == "Bearer key_test_secret"
    assert headers["X-Runway-Version"] == "2024-11-06"
    assert guard.spent_credits == 7, "Endgültige Kosten laut Runway werden übernommen"


def test_guard_blocks_before_any_request(episode, pricing, cfg):
    plan = build_plan(episode, pricing, profile="final", scenes="S05", include_audio=False)
    vid = next(j for j in plan.jobs if j.kind == "video")
    t = FakeTransport()
    client, _ = _client(pricing, cfg, t, limit=0.10)
    with pytest.raises(BudgetError):
        client.run_job(vid, {"KF_S05": "data:image/jpeg;base64,xx"})
    assert t.calls == [], "Bei Budgetüberschreitung darf kein Request rausgehen"


def test_price_drift_cancels_task_and_stops_run(episode, pricing, cfg):
    t = FakeTransport(api_estimate=80)  # Tabelle sagt 8
    client, guard = _client(pricing, cfg, t)
    with pytest.raises(PriceDriftError):
        client.run_job(_image_job(episode, pricing), {})
    assert any(c[0] == "DELETE" for c in t.calls), "Task muss storniert werden"
    assert guard.stopped == "price_drift"
    with pytest.raises(BudgetError, match="gestoppt"):
        guard.authorize("next", 1)


def test_network_abort_on_create_is_not_retried(episode, pricing, cfg):
    t = FakeTransport(create_exc=socket.timeout("timed out"))
    client, guard = _client(pricing, cfg, t)
    job = _image_job(episode, pricing)
    with pytest.raises(UncertainSubmissionError):
        client.run_job(job, {})
    assert sum(1 for c in t.calls if c[0] == "POST") == 1
    assert guard.spent_credits == job.credits, "Unklare Anlage bleibt als Kosten stehen"
    assert guard.stopped == "uncertain"


def test_server_error_on_create_is_uncertain(episode, pricing, cfg):
    t = FakeTransport(create_status=502)
    client, guard = _client(pricing, cfg, t)
    with pytest.raises(UncertainSubmissionError):
        client.run_job(_image_job(episode, pricing), {})
    assert sum(1 for c in t.calls if c[0] == "POST") == 1


def test_validation_error_costs_nothing(episode, pricing, cfg):
    t = FakeTransport(create_status=400)
    client, guard = _client(pricing, cfg, t)
    with pytest.raises(RunwayError, match="abgelehnt"):
        client.run_job(_image_job(episode, pricing), {})
    assert guard.spent_credits == 0


def test_rate_limit_retried_without_double_charge(episode, pricing, cfg):
    class T(FakeTransport):
        def __init__(self):
            super().__init__()
            self.n = 0

        def __call__(self, method, url, headers, body):
            if method == "POST":
                self.n += 1
                if self.n == 1:
                    self.calls.append((method, url, headers, None))
                    return 429, {}, {"Retry-After": "0"}
            return super().__call__(method, url, headers, body)

    t = T()
    client, guard = _client(pricing, cfg, t)
    job = _image_job(episode, pricing)
    client.run_job(job, {})
    assert guard.spent_credits == job.credits


def test_polling_survives_transient_errors(episode, pricing, cfg):
    t = FakeTransport(get_errors=3)
    client, _ = _client(pricing, cfg, t)
    assert client.run_job(_image_job(episode, pricing), {}).startswith("https://")


def test_failed_task_not_resubmitted_and_refund_respected(episode, pricing, cfg):
    t = FakeTransport(task_states=("FAILED",), final_cost=0)
    client, guard = _client(pricing, cfg, t)
    with pytest.raises(RunwayError, match="NICHT automatisch"):
        client.run_job(_image_job(episode, pricing), {})
    assert sum(1 for c in t.calls if c[0] == "POST") == 1
    assert guard.spent_credits == 0


def test_timeout_cancels_task(episode, pricing, cfg):
    t = FakeTransport(task_states=["RUNNING"] * 50)
    guard = SpendGuard(limit_eur=5, pricing=pricing, cfg=cfg)
    client = RunwayClient("k", guard, transport=t, sleep=lambda s: None, poll_interval=1, timeout_s=3)
    with pytest.raises(RunwayError, match="Zeitüberschreitung"):
        client.run_job(_image_job(episode, pricing), {})
    assert any(c[0] == "DELETE" for c in t.calls)


def test_same_job_cannot_be_submitted_twice(episode, pricing, cfg):
    client, _ = _client(pricing, cfg, FakeTransport(task_states=("SUCCEEDED", "SUCCEEDED")))
    job = _image_job(episode, pricing)
    client.run_job(job, {})
    with pytest.raises(BudgetError, match="bereits eingereicht"):
        client.run_job(job, {})


def test_invalid_payload_never_sent(episode, pricing, cfg):
    job = _image_job(episode, pricing)
    job.payload["ratio"] = "9:16"
    t = FakeTransport()
    client, guard = _client(pricing, cfg, t)
    with pytest.raises(SpecError):
        client.run_job(job, {})
    assert t.calls == [] and guard.spent_credits == 0


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
