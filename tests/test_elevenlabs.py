"""ElevenLabs-Integration: Spezifikation, Plan, Client, Kontoprüfung, Budget, Workflow.

Kein Test kontaktiert die echte API – der Transport ist immer simuliert.
"""
import json
import re
import socket
from dataclasses import replace

import pytest
import yaml

from storyhouse import elevenlabs_spec as spec
from storyhouse.budget import (
    ApprovalError, BudgetError, PriceDriftError, SpendGuard, finalize, require_paid_approval, reserve,
)
from storyhouse.cli import main
from storyhouse.elevenlabs import (
    ElevenLabsClient, ElevenLabsError, UncertainGenerationError, check_subscription,
)
from storyhouse.plan import build_plan
from storyhouse.runway_spec import SpecError
from tests.conftest import ROOT

KEY = "sk_test_0123456789abcdef0123456789abcdef"
VOICE = "EXAVITQu4vr4xnSDxMaL"
TTS = {"text": "[whispers] Don't... turn around.", "model_id": "eleven_v3", "language_code": "en",
       "voice_settings": {"stability": 0.5}, "seed": 7}
SFX = {"text": "door creak", "model_id": "eleven_text_to_sound_v2", "duration_seconds": 3.0, "loop": False}
MUSIC = {"prompt": "dark underscore", "model_id": "music_v1", "music_length_ms": 40000, "force_instrumental": True}


# ---------------------------------------------------------------- Spezifikation
def test_tts_request_spec():
    path = spec.tts_path(VOICE)
    assert path == f"/v1/text-to-speech/{VOICE}"
    spec.validate_request("tts", path, TTS)
    with pytest.raises(SpecError, match="voice_id"):
        spec.tts_path("Maya")
    with pytest.raises(SpecError, match="voice_id"):
        spec.validate_request("tts", "/v1/text-to-speech/", TTS)
    with pytest.raises(SpecError, match="eleven_v3"):
        spec.validate_request("tts", path, {**TTS, "model_id": "eleven_multilingual_v2", "language_code": None})
    with pytest.raises(SpecError, match="stability"):
        spec.validate_request("tts", path, {**TTS, "voice_settings": {"stability": 0.3}})
    with pytest.raises(SpecError, match="5000"):
        spec.validate_request("tts", path, {**TTS, "text": "x" * 5001})
    with pytest.raises(SpecError, match="nicht freigegeben"):
        spec.validate_request("tts", path, {**TTS, "model_id": "eleven_v9"})
    with pytest.raises(SpecError, match="unbekannte Felder"):
        spec.validate_request("tts", path, {**TTS, "auto_purchase": True})


def test_sfx_request_requires_fixed_duration():
    spec.validate_request("sfx", spec.SFX_PATH, SFX)
    for bad in ({**SFX, "duration_seconds": None}, {**SFX, "duration_seconds": 31}, {**SFX, "duration_seconds": 0.2}):
        with pytest.raises(SpecError, match="duration_seconds"):
            spec.validate_request("sfx", spec.SFX_PATH, bad)
    with pytest.raises(SpecError, match="Pfad"):
        spec.validate_request("sfx", "/v1/music", SFX)


def test_music_request_spec():
    spec.validate_request("music", spec.MUSIC_PATH, MUSIC)
    with pytest.raises(SpecError, match="music_length_ms"):
        spec.validate_request("music", spec.MUSIC_PATH, {**MUSIC, "music_length_ms": None})
    with pytest.raises(SpecError, match="force_instrumental"):
        spec.validate_request("music", spec.MUSIC_PATH, {**MUSIC, "force_instrumental": False})
    with pytest.raises(SpecError, match="seed"):
        spec.validate_request("music", spec.MUSIC_PATH, {**MUSIC, "seed": 1})


# ---------------------------------------------------------------- Plan & Preise
def test_default_plan_uses_elevenlabs_for_all_audio(episode, pricing):
    plan = build_plan(episode, pricing, profile="final")
    assert plan.audio_provider == "elevenlabs" and plan.providers == ["runway", "elevenlabs"]
    assert plan.required_secrets() == ("RUNWAYML_API_SECRET", "ELEVENLABS_API_KEY")
    tts = [j for j in plan.jobs if j.kind == "tts"]
    sfx = [j for j in plan.jobs if j.kind == "sfx"]
    assert len(tts) == 6 and len(sfx) == 19, "6 Dialogzeilen, 17 SFX + Drone + Outro-Sting"
    assert all(j.provider == "elevenlabs" for j in tts + sfx)
    assert all(j.provider == "runway" for j in plan.jobs if j.kind in ("image", "video"))
    for j in tts:
        assert j.path == f"/v1/text-to-speech/{VOICE}"
        assert j.payload["model_id"] == "eleven_v3" and j.payload["language_code"] == "en"
        assert j.credits == len(j.payload["text"]), "1 Credit pro Zeichen inkl. Regie-Tags"
    for j in sfx:
        assert j.credits == -(-j.payload["duration_seconds"] * 40 // 1), "40 Credits pro Sekunde"
    assert "SFX_outro_sting" in [j.id for j in sfx]
    assert tts[0].payload["text"] == "[whispers] Don't... turn around."


def test_dialogue_is_english(episode, pricing):
    plan = build_plan(episode, pricing)
    texts = [j.payload["text"] for j in plan.jobs if j.kind == "tts"]
    assert "[softly, warmly] Thank you for turning around." in texts
    assert not any(re.search(r"[äöüß]", t) for t in texts)


def test_music_only_after_license_confirmation(episode, pricing, cfg):
    assert cfg.el_music_license_confirmed is False, "Musik ist im Repo standardmäßig gesperrt"
    plan = build_plan(episode, pricing)
    assert not [j for j in plan.jobs if j.kind == "music"]
    assert any("Lizenzprüfung" in n for n in plan.notes)
    plan = build_plan(episode, pricing, include_music=True)
    music = [j for j in plan.jobs if j.kind == "music"]
    assert len(music) == 1 and music[0].id == "MUSIC_score"
    assert music[0].payload == {**music[0].payload, "music_length_ms": 40000, "force_instrumental": True,
                                "model_id": "music_v1"}
    assert music[0].credits == 600
    sub = build_plan(episode, pricing, scenes="S01", include_music=True)
    assert next(j for j in sub.jobs if j.kind == "music").payload["music_length_ms"] == 3000


def test_cost_estimate_is_conservative_and_split_by_provider(episode, pricing, cfg):
    """Pipeline-Preis >= offizieller API-Listenpreis (0,08 $/1k Zeichen, 0,12 $/min SFX, 0,15 $/min Musik)."""
    usd = pricing.usd_per_credit_of("elevenlabs")
    assert usd == pytest.approx(0.0002)
    assert pricing.el_tts_credits("eleven_v3", "x" * 1000) * usd >= 0.08
    assert pricing.el_sfx_credits("eleven_text_to_sound_v2", 60) * usd >= 0.12
    assert pricing.el_music_credits("music_v1", 60) * usd >= 0.15
    plan = build_plan(episode, pricing, profile="final", include_music=True)
    el_eur = pricing.credits_to_eur(plan.credits("elevenlabs"), cfg, "elevenlabs")
    rw_eur = pricing.credits_to_eur(plan.credits("runway"), cfg, "runway")
    assert plan.total_eur(pricing, cfg) == pytest.approx(el_eur + rw_eur, abs=0.001)
    assert 0.5 < el_eur < 2.0
    assert plan.total_eur(pricing, cfg) * (1 + cfg.safety_margin) <= cfg.max_per_run_eur
    pricing.assert_paid_ready(plan.jobs)


def test_unknown_or_unverified_elevenlabs_price_blocks(episode, pricing):
    import copy

    from storyhouse.budget import Pricing

    raw = copy.deepcopy(pricing.raw)
    raw["elevenlabs"]["sound_effect"]["eleven_text_to_sound_v2"]["verified"] = False
    p2 = Pricing(raw=raw)
    with pytest.raises(BudgetError, match="elevenlabs/sfx"):
        p2.assert_paid_ready(build_plan(episode, p2, scenes="S01").jobs)
    with pytest.raises(BudgetError, match="Kein Preis"):
        pricing.el_tts_credits("eleven_v9", "hi")


def test_plan_json_contains_no_key(episode, pricing, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    text = json.dumps(build_plan(episode, pricing, include_music=True).to_dict())
    assert KEY not in text and "xi-api-key" not in text


# ---------------------------------------------------------------- Client
class FakeTransport:
    def __init__(self, status=200, cost="33", exc=None, body=b"ID3" + b"\x00" * 600, fail_first=None):
        self.calls, self.status, self.cost, self.exc, self.body = [], status, cost, exc, body
        self.fail_first = fail_first

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, dict(headers), json.loads(body) if body else None))
        if method == "GET":
            return 200, json.dumps({"tier": "starter", "character_count": 100, "character_limit": 30000}).encode(), {}
        if self.exc:
            raise self.exc
        if self.fail_first is not None and len([c for c in self.calls if c[0] == "POST"]) == 1:
            return self.fail_first, b"{}", {"Retry-After": "0"}
        if self.status != 200:
            return self.status, json.dumps({"detail": {"status": "quota_exceeded", "message": "nope"}}).encode(), {}
        return 200, self.body, ({"Character-Cost": self.cost} if self.cost is not None else {})


def _client(pricing, cfg, transport, limit=5.0, tmp_path=None):
    guard = SpendGuard(limit_eur=limit, pricing=pricing, cfg=cfg, provider="elevenlabs",
                       log_path=(tmp_path / "spend_elevenlabs.json") if tmp_path else None)
    return ElevenLabsClient(KEY, guard, transport=transport, sleep=lambda s: None), guard


def _tts_job(episode, pricing):
    return next(j for j in build_plan(episode, pricing, scenes="S01").jobs if j.kind == "tts")


def test_client_request_shape_and_reported_cost(episode, pricing, cfg, tmp_path):
    t = FakeTransport(cost="30")
    client, guard = _client(pricing, cfg, t)
    job = _tts_job(episode, pricing)
    out = client.run_job(job, tmp_path / "raw" / "TTS_D01.mp3")
    assert out.read_bytes().startswith(b"ID3")
    method, url, headers, body = t.calls[0]
    assert method == "POST"
    assert url == f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}?output_format=mp3_44100_128"
    assert headers["xi-api-key"] == KEY and "Authorization" not in headers
    assert body["model_id"] == "eleven_v3" and body["text"].startswith("[whispers]")
    assert guard.spent_credits == 30, "von ElevenLabs gemeldete Kosten (character-cost) werden übernommen"


def test_client_keeps_estimate_without_cost_header(episode, pricing, cfg, tmp_path):
    client, guard = _client(pricing, cfg, FakeTransport(cost=None))
    job = _tts_job(episode, pricing)
    client.run_job(job, tmp_path / "a.mp3")
    assert guard.spent_credits == job.credits


def test_guard_blocks_before_any_request(episode, pricing, cfg, tmp_path):
    t = FakeTransport()
    client, _ = _client(pricing, cfg, t, limit=0.001)
    with pytest.raises(BudgetError):
        client.run_job(_tts_job(episode, pricing), tmp_path / "a.mp3")
    assert t.calls == [], "Bei Budgetüberschreitung darf kein Request rausgehen"


@pytest.mark.parametrize("transport", [FakeTransport(exc=socket.timeout("timed out")), FakeTransport(status=502)])
def test_unclear_generation_is_not_retried(episode, pricing, cfg, tmp_path, transport):
    transport.calls.clear()
    client, guard = _client(pricing, cfg, transport)
    job = _tts_job(episode, pricing)
    with pytest.raises(UncertainGenerationError):
        client.run_job(job, tmp_path / "a.mp3")
    assert len(transport.calls) == 1, "keine Wiederholung"
    assert guard.spent_credits == job.credits and guard.stopped == "uncertain"
    with pytest.raises(BudgetError, match="gestoppt"):
        guard.authorize("next", 1)


def test_rejected_request_costs_nothing(episode, pricing, cfg, tmp_path):
    client, guard = _client(pricing, cfg, FakeTransport(status=401))
    with pytest.raises(ElevenLabsError, match="quota_exceeded"):
        client.run_job(_tts_job(episode, pricing), tmp_path / "a.mp3")
    assert guard.spent_credits == 0 and not (tmp_path / "a.mp3").exists()


def test_rate_limit_retried_without_double_charge(episode, pricing, cfg, tmp_path):
    t = FakeTransport(fail_first=429, cost=None)
    client, guard = _client(pricing, cfg, t)
    job = _tts_job(episode, pricing)
    client.run_job(job, tmp_path / "a.mp3")
    assert len(t.calls) == 2 and guard.spent_credits == job.credits


def test_price_drift_stops_run(episode, pricing, cfg, tmp_path):
    client, guard = _client(pricing, cfg, FakeTransport(cost="5000"))
    with pytest.raises(PriceDriftError):
        client.run_job(_tts_job(episode, pricing), tmp_path / "a.mp3")
    assert guard.stopped == "price_drift" and guard.spent_credits == 5000


def test_invalid_payload_never_sent(episode, pricing, cfg, tmp_path):
    job = _tts_job(episode, pricing)
    job.payload["model_id"] = "eleven_turbo_v2_5"
    t = FakeTransport()
    client, guard = _client(pricing, cfg, t)
    with pytest.raises(SpecError):
        client.run_job(job, tmp_path / "a.mp3")
    assert t.calls == [] and guard.spent_credits == 0


def test_key_never_leaks(episode, pricing, cfg, tmp_path, capsys):
    t = FakeTransport(status=403)
    client, guard = _client(pricing, cfg, t, tmp_path=tmp_path)
    assert KEY not in repr(client) and KEY not in repr(guard)
    with pytest.raises(ElevenLabsError) as err:
        client.run_job(_tts_job(episode, pricing), tmp_path / "a.mp3")
    assert KEY not in str(err.value)
    assert KEY not in (tmp_path / "spend_elevenlabs.json").read_text()
    out = capsys.readouterr()
    assert KEY not in out.out + out.err


def test_client_requires_key_and_elevenlabs_guard(pricing, cfg):
    with pytest.raises(ElevenLabsError):
        ElevenLabsClient("", SpendGuard(limit_eur=1, pricing=pricing, cfg=cfg, provider="elevenlabs"))
    with pytest.raises(BudgetError):
        ElevenLabsClient(KEY, SpendGuard(limit_eur=1, pricing=pricing, cfg=cfg))  # Runway-Wächter
    with pytest.raises(ElevenLabsError, match="HTTPS"):
        ElevenLabsClient(KEY, SpendGuard(limit_eur=1, pricing=pricing, cfg=cfg, provider="elevenlabs"),
                         base_url="http://api.elevenlabs.io")


def test_free_account_queries(pricing, cfg):
    client, guard = _client(pricing, cfg, FakeTransport())
    assert client.used_credits() == 100
    assert guard.spent_credits == 0


# ---------------------------------------------------------------- Kontoprüfung
SUB = {"tier": "starter", "status": "active", "character_count": 1000, "character_limit": 30000,
       "max_credit_limit_extension": 0, "current_overage": {"amount": "0", "currency": "usd"},
       "has_open_invoices": False}


def test_subscription_ok(cfg):
    info = check_subscription(SUB, 3800, cfg, needs_music=True)
    assert info["remaining"] == 29000 and info["commercial_license"] is True


@pytest.mark.parametrize("ext", [10000, "unlimited"])
def test_usage_based_billing_blocks_run(cfg, ext):
    """Keine automatischen Guthabenkäufe: Overage an -> kein bezahlter Lauf."""
    with pytest.raises(ApprovalError, match="Overage"):
        check_subscription({**SUB, "max_credit_limit_extension": ext}, 100, cfg)
    with pytest.raises(ApprovalError, match="Overage"):
        check_subscription({**SUB, "current_overage": {"amount": "1.20", "currency": "usd"}}, 100, cfg)


def test_free_tier_blocked_for_commercial_use(cfg):
    free = {**SUB, "tier": "free", "status": "free", "character_limit": 10000}
    commercial = replace(cfg, el_require_commercial_license=True)
    with pytest.raises(ApprovalError, match="kommerzielle Lizenz"):
        check_subscription(free, 100, commercial)
    private = replace(cfg, el_require_commercial_license=False)
    assert check_subscription(free, 100, private)["commercial_license"] is False
    with pytest.raises(ApprovalError, match="Eleven Music"):
        check_subscription(free, 100, private, needs_music=True)


def test_quota_must_cover_plan_with_margin(cfg):
    with pytest.raises(BudgetError, match="NICHT automatisch nachgekauft"):
        check_subscription({**SUB, "character_count": 27000}, 2500, cfg)
    check_subscription({**SUB, "character_count": 26000}, 2500, cfg)


@pytest.mark.parametrize("sub", [None, {}, {"tier": "starter"}, {**SUB, "status": "past_due"}])
def test_unreadable_or_unhealthy_account_fails_closed(cfg, sub):
    with pytest.raises(BudgetError):
        check_subscription(sub, 100, cfg)


# ---------------------------------------------------------------- Budget
def test_linked_guards_share_one_limit(pricing, cfg):
    rw = SpendGuard(limit_eur=1.00, pricing=pricing, cfg=cfg)
    el = SpendGuard(limit_eur=1.00, pricing=pricing, cfg=cfg, provider="elevenlabs")
    rw.link(el)
    rw.authorize("VID", 60)            # 60 Runway-Credits = 0,714 €
    el.authorize("SFX_a", 1000)        # 1000 EL-Credits   = 0,238 €
    with pytest.raises(BudgetError, match="STOP"):
        el.authorize("SFX_b", 400)     # +0,095 € -> über 1,00 €
    assert rw.run_spent_eur == el.run_spent_eur == pytest.approx(0.952, abs=0.001)
    el.mark_uncertain("SFX_a", "x")
    with pytest.raises(BudgetError, match="gestoppt"):
        rw.authorize("VID2", 1)


def test_ledger_books_both_providers(pricing, enabled_cfg, ledger):
    reserve("r1", 5.0, enabled_cfg, ledger)
    e = finalize("r1", 100, pricing, enabled_cfg, ledger, elevenlabs_credits=3000)
    assert e["spent_eur"] == pytest.approx(100 * 0.01 * 1.19 + 3000 * 0.0002 * 1.19, abs=0.001)
    assert e["elevenlabs_eur"] == pytest.approx(0.714, abs=0.001)


def test_cli_finalize_uses_highest_elevenlabs_evidence(pricing, enabled_cfg, tmp_path):
    from storyhouse.budget import Ledger

    ledger_path = tmp_path / "ledger.json"
    reserve("r2", 5.0, enabled_cfg, Ledger.load(ledger_path))
    out = tmp_path / "out"
    out.mkdir()
    (out / "spend.json").write_text(json.dumps({"spent_credits": 100, "stopped": None}))
    (out / "balance.json").write_text(json.dumps({"before": 1000, "after": 900}))
    (out / "spend_elevenlabs.json").write_text(json.dumps({"spent_credits": 3000, "stopped": None}))
    (out / "elevenlabs_usage.json").write_text(json.dumps({"before": 500, "after": 4500}))
    assert main(["finalize", "--ledger", str(ledger_path), "--run-id", "r2", "--out", str(out)]) == 0
    e = Ledger.load(ledger_path).entry("r2")
    assert e["elevenlabs_credits"] == 4000, "Konto-Differenz schlägt das eigene Protokoll, wenn sie höher ist"
    assert e["spent_credits"] == 100


def test_paid_approval_needs_elevenlabs_key(enabled_cfg):
    env = {"RUNWAYML_API_SECRET": "x", "ARTIFACT_PASSPHRASE": "p" * 20}
    needs = ("RUNWAYML_API_SECRET", "ELEVENLABS_API_KEY")
    with pytest.raises(ApprovalError, match="ELEVENLABS_API_KEY"):
        require_paid_approval(enabled_cfg, enabled_cfg.approval_phrase, env=env, secrets=needs)
    require_paid_approval(enabled_cfg, enabled_cfg.approval_phrase, env={**env, "ELEVENLABS_API_KEY": "y"},
                          secrets=needs)


@pytest.mark.parametrize("cmd", ["preflight", "reserve", "render"])
@pytest.mark.parametrize("variant", ["switch_off", "out_of_scope_all", "out_of_scope_final", "no_phrase"])
def test_cli_paid_commands_refuse_without_network(cmd, variant, tmp_path, monkeypatch, locked_budget_file):
    """Hauptschalter aus, Umfang überschritten oder Phrase fehlt: Abbruch ohne eine einzige Verbindung."""
    def no_network(*a, **k):
        raise AssertionError("Gesperrter Lauf darf keine Netzwerkverbindung öffnen")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setenv("STORYHOUSE_APPROVAL", "ICH GEBE DIESEN RENDER FREI")
    monkeypatch.setenv("RUNWAYML_API_SECRET", "dummy")
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    monkeypatch.setenv("ARTIFACT_PASSPHRASE", "x" * 20)
    args = {"switch_off": ["--budget-config", str(locked_budget_file), "--scenes", "S01"],
            "out_of_scope_all": ["--scenes", "all"],
            "out_of_scope_final": ["--profile", "final", "--scenes", "S01"],
            "no_phrase": ["--scenes", "S01"]}[variant]
    if variant == "no_phrase":
        monkeypatch.setenv("STORYHOUSE_APPROVAL", "")
    assert main([cmd, *args, "--ledger", str(tmp_path / "l.json"), "--out", str(tmp_path / "o")]) == 2


def test_paid_scope_only_allows_test_s01(cfg, episode, pricing):
    from storyhouse.budget import require_paid_scope

    require_paid_scope(cfg, build_plan(episode, pricing, profile="test", scenes="S01"))
    for profile, scenes, match in (("test", "all", "Szenen S02"), ("final", "S01", "Profil 'final'"),
                                   ("test", "S01,S02", "Szenen S02")):
        with pytest.raises(ApprovalError, match=match):
            require_paid_scope(cfg, build_plan(episode, pricing, profile=profile, scenes=scenes))
    with pytest.raises(ApprovalError, match="Tonquelle 'runway'"):
        require_paid_scope(cfg, build_plan(episode, pricing, scenes="S01", audio_provider="runway"))


def test_first_render_fits_budget(cfg, episode, pricing):
    plan = build_plan(episode, pricing, profile="test", scenes="S01",
                      include_music=cfg.el_music_license_confirmed)
    assert not [j for j in plan.jobs if j.kind == "music"]
    eur = plan.total_eur(pricing, cfg)
    assert eur * (1 + cfg.safety_margin) <= 1.10 <= cfg.max_per_run_eur


@pytest.mark.parametrize("status", [401, 403])
def test_permission_error_stops_whole_run(episode, pricing, cfg, tmp_path, status):
    """Fehlende ElevenLabs-Berechtigung: Lauf stoppt auch für Runway (verbundene Wächter)."""
    from storyhouse.elevenlabs import ElevenLabsPermissionError

    rw = SpendGuard(limit_eur=5, pricing=pricing, cfg=cfg)
    client, el = _client(pricing, cfg, FakeTransport(status=status))
    rw.link(el)
    with pytest.raises(ElevenLabsPermissionError, match="keine weiteren"):
        client.run_job(_tts_job(episode, pricing), tmp_path / "a.mp3")
    assert el.stopped == "permission" and el.spent_credits == 0
    with pytest.raises(BudgetError, match="gestoppt"):
        rw.authorize("VID_S01", 25)
    with pytest.raises(BudgetError, match="gestoppt"):
        el.authorize("SFX_x", 40)


# ---------------------------------------------------------------- Workflow & Code-Leitplanken
def _wf(name):
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())


def test_elevenlabs_key_only_in_gated_paid_job():
    wf = _wf("test-video.yml")
    paid, dry = wf["jobs"]["paid-render"], wf["jobs"]["dry-run"]
    assert paid["env"]["ELEVENLABS_API_KEY"] == "${{ secrets.ELEVENLABS_API_KEY }}"
    assert paid["environment"]["name"] == "paid-render"
    assert "ELEVENLABS" not in yaml.safe_dump(dry)
    assert "ELEVENLABS" not in (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    for job in wf["jobs"].values():
        for step in job["steps"]:
            assert "ELEVENLABS_API_KEY" not in str(step.get("run", "")), "Schlüssel nie in Shell-Befehlen"


def test_code_never_calls_purchase_or_subscription_changing_endpoints():
    """Kein Abo, kein Guthabenkauf: Der Code kennt nur Generierung + zwei lesende Abfragen."""
    src = (ROOT / "storyhouse" / "elevenlabs.py").read_text() + (ROOT / "storyhouse" / "elevenlabs_spec.py").read_text()
    paths = {p for p in re.findall(r'"(/v1/[^"]+)"', src) if "(" not in p}  # ohne Prüf-Regex
    assert paths == {"/v1/text-to-speech/{voice_id}", "/v1/sound-generation", "/v1/music",
                     "/v1/user/subscription", "/v1/voices/{voice_id}"}
    assert not re.search(r"(?i)checkout|top[-_ ]?up|payment|/billing|upgrade|purchase", src)
    methods = set(re.findall(r'transport\(\s*"([A-Z]+)"', src))
    assert methods == {"GET", "POST"}
    assert 'self.transport("POST"' in src and src.count('self.transport("POST"') == 1


def test_secrets_are_never_printed():
    for f in (ROOT / "storyhouse").rglob("*.py"):
        text = f.read_text()
        assert not re.search(r"print\([^)]*(_key|_secret|ELEVENLABS_API_KEY|RUNWAYML_API_SECRET)", text), f
    for f in (ROOT / ".github" / "workflows").glob("*.yml"):
        assert not re.search(r"echo[^\n]*secrets\.", f.read_text()), f
