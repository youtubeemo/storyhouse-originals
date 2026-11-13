import datetime as dt
from dataclasses import replace

import pytest

from storyhouse.budget import (
    HARD_CEILING_EUR, ApprovalError, BudgetError, Ledger, Pricing, SpendGuard, check_affordable,
    finalize, remaining_eur, require_paid_approval, reserve,
)
from storyhouse.plan import build_plan

ENV_OK = {"RUNWAYML_API_SECRET": "x", "ARTIFACT_PASSPHRASE": "a-very-long-passphrase"}


# ---------------------------------------------------------------- Konfiguration
def test_repository_config_is_safe_by_default(cfg):
    assert cfg.paid_rendering_enabled is False, "Bezahltes Rendern darf im Repo nicht freigeschaltet sein"
    assert cfg.total_budget_eur == 200.0 <= HARD_CEILING_EUR
    assert cfg.max_per_run_eur <= cfg.total_budget_eur
    assert cfg.vat_rate >= 0.19


@pytest.mark.parametrize("field,value", [
    ("total_budget_eur", 250.0), ("safety_margin", 0.0), ("eur_per_usd", 0.5), ("vat_rate", 0.0),
])
def test_unsafe_config_rejected(cfg, field, value):
    with pytest.raises(BudgetError):
        replace(cfg, **{field: value}).validate()


def test_eur_conversion_includes_vat(pricing, cfg):
    assert pricing.credits_to_eur(100, cfg) == pytest.approx(1.19)


# ---------------------------------------------------------------- Preise
def test_prices_match_official_table(pricing):
    assert pricing.video_credits("gen4.5", 5) == 60
    assert pricing.video_credits("gen4_turbo", 5) == 25
    assert pricing.video_credits("veo3.1_fast", 6) == 60
    assert pricing.image_credits("gen4_image") == 8
    assert pricing.image_credits("gen4_image_turbo") == 2
    assert pricing.tts_credits("eleven_v3", "x" * 51) == 2
    assert pricing.tts_credits("eleven_v3", "x") == 1
    assert pricing.sfx_credits("eleven_text_to_sound_v2", 1.5) == 2


def test_unknown_model_price_is_error(pricing):
    with pytest.raises(BudgetError):
        pricing.video_credits("unknown_model", 5)


def test_stale_prices_block_paid_run(episode, pricing):
    plan = build_plan(episode, pricing, profile="final")
    pricing.assert_paid_ready(plan.jobs, today=pricing.verified_on())
    with pytest.raises(BudgetError, match="Tage alt"):
        pricing.assert_paid_ready(plan.jobs, today=pricing.verified_on() + dt.timedelta(days=60))


def test_unverified_model_blocks_paid_run(episode, pricing):
    raw = {**pricing.raw, "image": {**pricing.raw["image"], "gen4_image": {"credits_per_image": 8, "verified": False}}}
    p2 = Pricing(raw=raw)
    plan = build_plan(episode, p2, profile="final", scenes="S01")
    with pytest.raises(BudgetError, match="Ungeprüfte"):
        p2.assert_paid_ready(plan.jobs, today=p2.verified_on())


def test_full_final_render_fits_run_limit(episode, pricing, cfg):
    eur = build_plan(episode, pricing, profile="final").total_eur(pricing, cfg)
    assert 0 < eur * (1 + cfg.safety_margin) <= cfg.max_per_run_eur


# ---------------------------------------------------------------- Ledger
def test_reservation_and_finalize_roundtrip(pricing, cfg, ledger):
    entry = reserve("run-1", 10.0, cfg, ledger)
    assert entry["reserved_eur"] == pytest.approx(12.5)
    assert remaining_eur(cfg, ledger) == pytest.approx(187.5)
    reloaded = Ledger.load(ledger.path)
    finalize("run-1", 800, pricing, cfg, reloaded)  # 800 Credits = 8 USD = 9.52 € inkl. USt.
    assert remaining_eur(cfg, reloaded) == pytest.approx(200 - 9.52)


def test_open_reservation_blocks_second_run(cfg, ledger):
    reserve("a", 10.0, cfg, ledger)
    with pytest.raises(BudgetError, match="Offene Reservierung"):
        reserve("b", 1.0, cfg, ledger)
    assert ledger.committed_eur() == pytest.approx(12.5)


def test_per_run_limit_enforced(cfg, ledger):
    with pytest.raises(BudgetError, match="Lauf-Limit"):
        check_affordable(cfg.max_per_run_eur, cfg, ledger)


def test_total_budget_enforced(pricing, cfg, ledger):
    i = 0
    while remaining_eur(cfg, ledger) >= 22.5:
        reserve(f"r{i}", 18.0, cfg, ledger)
        finalize(f"r{i}", pricing.eur_to_credits(22.5, cfg), pricing, cfg, ledger)
        i += 1
    with pytest.raises(BudgetError, match="verbleibendes Gesamtbudget"):
        reserve("too-much", 18.0, cfg, ledger)
    assert ledger.committed_eur() <= cfg.total_budget_eur


def test_zero_estimate_rejected(cfg, ledger):
    with pytest.raises(BudgetError):
        check_affordable(0.0, cfg, ledger)


def test_duplicate_run_id_rejected(pricing, cfg, ledger):
    reserve("x", 1.0, cfg, ledger)
    finalize("x", 10, pricing, cfg, ledger)
    with pytest.raises(BudgetError, match="existiert bereits"):
        reserve("x", 1.0, cfg, ledger)


@pytest.mark.parametrize("content", ['{"version": 99}', "{not json", '{"version":1,"entries":[{"x":1}]}'])
def test_corrupt_ledger_fails_closed(tmp_path, content):
    p = tmp_path / "ledger.json"
    p.write_text(content)
    with pytest.raises(BudgetError):
        Ledger.load(p)


def test_balance_delta_wins_if_higher(pricing, cfg, ledger):
    reserve("r", 5.0, cfg, ledger)
    e = finalize("r", 100, pricing, cfg, ledger, balance_delta_credits=300)
    assert e["spent_credits"] == 300


def test_overrun_is_flagged(pricing, cfg, ledger):
    reserve("r", 1.0, cfg, ledger)
    e = finalize("r", 500, pricing, cfg, ledger)
    assert e.get("overrun") is True


# ---------------------------------------------------------------- Wächter
def test_spend_guard_stops_before_limit(pricing, cfg, tmp_path):
    guard = SpendGuard(limit_eur=1.00, pricing=pricing, cfg=cfg, log_path=tmp_path / "spend.json")
    assert (tmp_path / "spend.json").exists()
    guard.authorize("a", 60)
    with pytest.raises(BudgetError, match="STOP"):
        guard.authorize("b", 60)
    assert guard.spent_credits == 60


def test_spend_guard_settles_to_runway_cost(pricing, cfg):
    guard = SpendGuard(limit_eur=10, pricing=pricing, cfg=cfg)
    guard.authorize("a", 60)
    guard.observe_estimate("a", 60)
    guard.settle("a", 0)  # z. B. erstattet
    assert guard.spent_credits == 0


def test_spend_guard_rejects_zero_cost(pricing, cfg):
    with pytest.raises(BudgetError):
        SpendGuard(limit_eur=10, pricing=pricing, cfg=cfg).authorize("free?", 0)


# ---------------------------------------------------------------- Freigabe
def test_paid_approval_blocked_by_default(cfg):
    with pytest.raises(ApprovalError, match="gesperrt"):
        require_paid_approval(cfg, cfg.approval_phrase, env=ENV_OK)


def test_paid_approval_needs_phrase_secret_and_passphrase(enabled_cfg):
    with pytest.raises(ApprovalError, match="Bestätigungsphrase"):
        require_paid_approval(enabled_cfg, "ja", env=ENV_OK)
    with pytest.raises(ApprovalError, match="RUNWAYML_API_SECRET"):
        require_paid_approval(enabled_cfg, enabled_cfg.approval_phrase, env={"ARTIFACT_PASSPHRASE": "x" * 20})
    with pytest.raises(ApprovalError, match="ARTIFACT_PASSPHRASE"):
        require_paid_approval(enabled_cfg, enabled_cfg.approval_phrase, env={"RUNWAYML_API_SECRET": "x"})
    require_paid_approval(enabled_cfg, enabled_cfg.approval_phrase, env=ENV_OK)
