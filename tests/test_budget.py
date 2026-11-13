from dataclasses import replace

import pytest

from storyhouse.budget import (
    HARD_CEILING_EUR, ApprovalError, BudgetError, Ledger, SpendGuard, check_affordable, finalize,
    remaining_eur, require_paid_approval, reserve,
)
from storyhouse.plan import build_plan


def test_repository_config_is_safe_by_default(cfg):
    assert cfg.paid_rendering_enabled is False, "Bezahltes Rendern darf im Repo nicht freigeschaltet sein"
    assert cfg.total_budget_eur == 200.0
    assert cfg.total_budget_eur <= HARD_CEILING_EUR
    assert cfg.max_per_run_eur <= cfg.total_budget_eur


def test_budget_above_hard_ceiling_rejected(cfg):
    with pytest.raises(BudgetError):
        replace(cfg, total_budget_eur=250.0).validate()


def test_weak_safety_margin_rejected(cfg):
    with pytest.raises(BudgetError):
        replace(cfg, safety_margin=0.0).validate()


def test_full_final_render_fits_run_limit(episode, pricing, cfg):
    plan = build_plan(episode, pricing, profile="final")
    eur = plan.total_eur(pricing, cfg)
    assert 0 < eur * (1 + cfg.safety_margin) <= cfg.max_per_run_eur


def test_unknown_model_price_is_error(pricing):
    with pytest.raises(BudgetError):
        pricing.video_credits("unknown_model", 5)


def test_reservation_and_finalize_roundtrip(pricing, cfg, ledger):
    entry = reserve("run-1", 10.0, cfg, ledger)
    assert entry["reserved_eur"] == pytest.approx(12.5)
    assert remaining_eur(cfg, ledger) == pytest.approx(187.5)
    reloaded = Ledger.load(ledger.path)
    assert reloaded.entry("run-1")["status"] == "reserved"
    finalize("run-1", 800, pricing, cfg, reloaded)  # 800 Credits = 8 €
    assert remaining_eur(cfg, reloaded) == pytest.approx(192.0)


def test_open_reservations_count_fully(cfg, ledger):
    reserve("a", 10.0, cfg, ledger)
    reserve("b", 10.0, cfg, ledger)
    assert ledger.committed_eur() == pytest.approx(25.0)


def test_per_run_limit_enforced(cfg, ledger):
    with pytest.raises(BudgetError, match="Lauf-Limit"):
        check_affordable(cfg.max_per_run_eur, cfg, ledger)  # +Puffer > Limit


def test_total_budget_enforced(pricing, cfg, ledger):
    for i in range(9):
        reserve(f"r{i}", 18.0, cfg, ledger)  # je 22.50 € → 202.50 € wäre zu viel
        if remaining_eur(cfg, ledger) < 22.5:
            break
    with pytest.raises(BudgetError, match="verbleibendes Gesamtbudget"):
        reserve("too-much", 18.0, cfg, ledger)
    assert ledger.committed_eur() <= cfg.total_budget_eur


def test_duplicate_run_id_rejected(cfg, ledger):
    reserve("x", 1.0, cfg, ledger)
    with pytest.raises(BudgetError):
        reserve("x", 1.0, cfg, ledger)


def test_corrupt_ledger_fails_closed(tmp_path):
    p = tmp_path / "ledger.json"
    p.write_text('{"version": 99}')
    with pytest.raises(BudgetError):
        Ledger.load(p)


def test_balance_delta_wins_if_higher(pricing, cfg, ledger):
    reserve("r", 5.0, cfg, ledger)
    e = finalize("r", 100, pricing, cfg, ledger, balance_delta_credits=300)
    assert e["spent_eur"] == pytest.approx(3.0)


def test_spend_guard_stops_before_limit(pricing, cfg, tmp_path):
    guard = SpendGuard(limit_eur=1.00, pricing=pricing, cfg=cfg, log_path=tmp_path / "spend.json")
    assert (tmp_path / "spend.json").exists()
    guard.authorize("a", 60)
    with pytest.raises(BudgetError, match="STOP"):
        guard.authorize("b", 60)
    assert guard.spent_credits == 60


def test_spend_guard_rejects_zero_cost(pricing, cfg):
    guard = SpendGuard(limit_eur=10, pricing=pricing, cfg=cfg)
    with pytest.raises(BudgetError):
        guard.authorize("free?", 0)


def test_paid_approval_blocked_by_default(cfg):
    with pytest.raises(ApprovalError, match="gesperrt"):
        require_paid_approval(cfg, cfg.approval_phrase, env={"RUNWAYML_API_SECRET": "x"})


def test_paid_approval_needs_phrase_and_secret(enabled_cfg):
    with pytest.raises(ApprovalError, match="Bestätigungsphrase"):
        require_paid_approval(enabled_cfg, "ja", env={"RUNWAYML_API_SECRET": "x"})
    with pytest.raises(ApprovalError, match="RUNWAYML_API_SECRET"):
        require_paid_approval(enabled_cfg, enabled_cfg.approval_phrase, env={})
    require_paid_approval(enabled_cfg, enabled_cfg.approval_phrase, env={"RUNWAYML_API_SECRET": "x"})
