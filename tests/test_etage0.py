"""ETAGE 0 – Der verbotene Knopf: Episode, Kostenrahmen (≤ 10 €) und Freigabeumfang."""
from dataclasses import replace
from pathlib import Path

import pytest

from storyhouse.budget import (ApprovalError, BudgetConfig, BudgetError, Ledger, Pricing, require_paid_scope,
                               require_scope_budget, with_margin)
from storyhouse.episode import EpisodeError, load_episode, validate_episode
from storyhouse.plan import build_plan

ROOT = Path(__file__).resolve().parent.parent
EP = ROOT / "episodes" / "etage-0" / "ep01-der-verbotene-knopf"


@pytest.fixture
def e0():
    return load_episode(EP)


def test_episode_ist_30_sekunden_deutsch_hochkant(e0):
    assert e0.total_duration == pytest.approx(30.0)
    assert (e0.fmt["width"], e0.fmt["height"]) == (1080, 1920)
    assert [d["text"] for d in e0.dialogue] == ["Drück sie nicht, Leon.", "Du bist schon wieder hier."]
    assert e0.outro["title"] == "ETAGE 0"
    assert [s["start"] for s in e0.scenes] == [0.0, 5.0, 12.0, 20.0, 26.5]


def test_plan_bleibt_unter_zehn_euro_und_ohne_musikjob(e0):
    pricing, cfg = Pricing.load(), BudgetConfig.load()
    plan = build_plan(e0, pricing, profile="test", scenes="all", include_music=cfg.el_music_license_confirmed)
    assert {j.model for j in plan.jobs if j.kind == "video"} == {"gen4.5"}
    assert not [j for j in plan.jobs if j.kind == "music"]
    assert all(len(j.payload.get("referenceImages", [])) <= 3 for j in plan.jobs)
    eur = plan.total_eur(pricing, cfg)
    assert with_margin(eur, cfg) <= 8.0 < cfg.scope_budget_eur
    pricing.assert_paid_ready(plan.jobs)
    # ElevenLabs-Free-Kontingent (10.000 Credits/Monat, 816 bereits verbraucht) reicht
    assert sum(j.credits for j in plan.jobs if j.provider == "elevenlabs") < 5000


def test_nur_etage0_ist_freigegeben_und_gedeckelt(e0, tmp_path):
    pricing, cfg = Pricing.load(), BudgetConfig.load()
    plan = build_plan(e0, pricing, profile="test", scenes="all")
    require_paid_scope(cfg, plan)
    with pytest.raises(ApprovalError, match="Profil 'final'"):
        require_paid_scope(cfg, build_plan(e0, pricing, profile="final", scenes="all"))
    eur = plan.total_eur(pricing, cfg)
    ledger = Ledger.load(tmp_path / "l.json")
    require_scope_budget(cfg, ledger, eur)                      # erster Lauf passt
    ledger.entries.append({"run_id": "r1", "status": "finalized", "spent_eur": 5.5, "reserved_eur": 7.3,
                           "episode": e0.slug})
    with pytest.raises(BudgetError, match="10.00 €"):          # zweiter voller Lauf würde 10 € sprengen
        require_scope_budget(cfg, ledger, eur)
    require_scope_budget(cfg, ledger, 3.0)                      # kleiner Nachdreh (≤ Rest) wäre möglich
    # fremde Ledger-Einträge (The Reflection) zählen nicht mit
    ledger.entries[0]["episode"] = "tos-ep01-the-reflection"
    require_scope_budget(cfg, ledger, eur)


def test_konfigurationsgrenzen():
    cfg = BudgetConfig.load()
    with pytest.raises(BudgetError):
        replace(cfg, scope_budget_eur=30.0).validate()          # nie über dem Lauf-Limit
    with pytest.raises(BudgetError):
        replace(cfg, scope_episodes=None).validate()            # Deckel braucht Episode


def test_modellwahl_nur_fuer_bild_und_video(e0):
    e0.data["models"] = {"tts_model": "x"}
    with pytest.raises(EpisodeError, match="models.tts_model"):
        validate_episode(e0)
