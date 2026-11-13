"""Kommandozeile: python -m storyhouse <befehl> ...

Kostenlose Befehle:  validate, estimate, budget-status, dry-run
Bezahlter Ablauf:    preflight -> reserve -> render -> finalize
(Der bezahlte Ablauf ist nur im GitHub-Workflow mit Freigabe vorgesehen.)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from storyhouse.budget import (
    ApprovalError, BudgetConfig, BudgetError, Ledger, Pricing, SpendGuard, check_affordable,
    finalize, remaining_eur, require_paid_approval, reserve,
)
from storyhouse.episode import EpisodeError, load_episode
from storyhouse.plan import build_plan, format_estimate
from storyhouse.runway_spec import SpecError

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EPISODE = REPO_ROOT / "episodes" / "the-other-side" / "ep01-the-reflection"


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--episode", default=str(DEFAULT_EPISODE))
    p.add_argument("--budget-config", default=str(REPO_ROOT / "config" / "budget.yaml"))
    p.add_argument("--pricing", default=str(REPO_ROOT / "config" / "pricing.yaml"))


def _plan_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--profile", default="test", choices=["test", "final"])
    p.add_argument("--scenes", default="all", help="all | S01,S03 | S02-S04")
    p.add_argument("--no-audio", action="store_true", help="Stimmen/SFX nicht generieren")


def _summary(text: str) -> None:
    """Schreibt zusätzlich in die GitHub-Step-Summary, falls vorhanden."""
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n\n")


def _balance_check(plan, pricing, cfg, ledger) -> float:
    """Kostenlose Abfrage des Runway-Guthabens (GET /v1/organization)."""
    from storyhouse.budget import SpendGuard
    from storyhouse.runway import RunwayClient

    client = RunwayClient(os.environ["RUNWAYML_API_SECRET"], SpendGuard(limit_eur=0.0, pricing=pricing, cfg=cfg))
    bal = client.credit_balance()
    if bal is None:
        raise BudgetError("Runway-Guthaben nicht abrufbar (Schlüssel/Netz prüfen) – Abbruch")
    if bal < plan.total_credits:
        raise BudgetError(f"Runway-Guthaben {bal:.0f} Credits < benötigt {plan.total_credits:.0f} Credits")
    if pricing.credits_to_eur(bal, cfg) > remaining_eur(cfg, ledger) + cfg.max_per_run_eur:
        _summary(f"⚠️ Runway-Guthaben ({bal:.0f} Credits) liegt deutlich über dem Restbudget. "
                 "Empfehlung: nur so viel aufladen, wie das Budget erlaubt (externe Obergrenze).")
    return bal


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="storyhouse", description="STORYHOUSE ORIGINALS Pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("validate", help="Episode prüfen"); _common(p)
    p = sub.add_parser("estimate", help="Kosten schätzen (kostenlos)"); _common(p); _plan_args(p)
    p = sub.add_parser("budget-status", help="Budgetstand anzeigen"); _common(p)
    p.add_argument("--ledger", default=".ledger/ledger.json")

    p = sub.add_parser("dry-run", help="Kostenloses Platzhalter-Testvideo"); _common(p); _plan_args(p)
    p.add_argument("--out", default="output/dry-run")
    p.add_argument("--preview", action="store_true", help="kleine Auflösung (360x640, 12 fps) für schnelle Checks")

    for name in ("preflight", "reserve", "render", "finalize"):
        p = sub.add_parser(name); _common(p); _plan_args(p)
        p.add_argument("--ledger", default=".ledger/ledger.json")
        p.add_argument("--run-id", default=os.environ.get("STORYHOUSE_RUN_ID", "local"))
        p.add_argument("--out", default="output/render")

    p = sub.add_parser("close-run", help="Hängende Reservierung manuell abschließen (z. B. nach Runner-Absturz)")
    _common(p)
    p.add_argument("--ledger", default=".ledger/ledger.json")
    p.add_argument("--run-id", required=True)
    p.add_argument("--spent-credits", type=float, required=True,
                   help="tatsächlich verbrauchte Credits laut Runway Developer Portal (Usage)")
    p.add_argument("--reason", required=True)

    args = ap.parse_args(argv)
    try:
        return _dispatch(args)
    except (BudgetError, ApprovalError, EpisodeError, SpecError) as exc:
        _summary(f"❌ **Abbruch:** {exc}")
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    ep = load_episode(args.episode)
    cfg = BudgetConfig.load(args.budget_config)
    pricing = Pricing.load(args.pricing)

    if args.cmd == "validate":
        print(f"✅ {ep.slug}: {len(ep.scenes)} Szenen, {ep.duration:.1f} s, {len(ep.dialogue)} Dialogzeilen – gültig")
        return 0

    if args.cmd == "budget-status":
        ledger = Ledger.load(args.ledger)
        rem = remaining_eur(cfg, ledger)
        _summary(
            f"### Budgetstand\n\nGesamtbudget: {cfg.total_budget_eur:.2f} € · "
            f"verbraucht/reserviert: {ledger.committed_eur():.2f} € · **verbleibend: {rem:.2f} €** · "
            f"Läufe: {len(ledger.entries)} · bezahltes Rendern: "
            f"{'FREIGESCHALTET' if cfg.paid_rendering_enabled else 'GESPERRT'}"
        )
        if ledger.committed_eur() >= cfg.total_budget_eur * cfg.warn_at_fraction:
            _summary(f"⚠️ Warnschwelle {cfg.warn_at_fraction:.0%} des Budgets erreicht.")
        return 0

    if args.cmd == "close-run":
        ledger = Ledger.load(args.ledger)
        e = finalize(args.run_id, args.spent_credits, pricing, cfg, ledger, note=f"manuell: {args.reason}")
        _summary(f"🧾 Lauf `{args.run_id}` manuell abgeschlossen: {e['spent_eur']:.2f} € verbucht")
        return 0

    plan = build_plan(ep, pricing, profile=args.profile, scenes=args.scenes, include_audio=not args.no_audio)
    estimate_eur = plan.total_eur(pricing, cfg)

    if args.cmd == "estimate":
        _summary(format_estimate(plan, pricing, cfg))
        return 0

    if args.cmd == "dry-run":
        from storyhouse.pipeline import dry_run

        dims = (360, 640, 12) if args.preview else (None, None, None)
        manifest = dry_run(ep, Path(args.out), pricing, cfg, scenes=args.scenes, profile=args.profile,
                           width=dims[0], height=dims[1], fps=dims[2])
        pr = manifest["probe"]
        _summary(
            f"### 🎬 Dry-Run fertig (0 € ausgegeben)\n\n`{manifest['video']}` · {pr['width']}×{pr['height']} · "
            f"{pr['fps']} fps · {pr['duration']:.2f} s\n\n" + format_estimate(plan, pricing, cfg)
        )
        return 0

    ledger = Ledger.load(args.ledger)
    approval = os.environ.get("STORYHOUSE_APPROVAL", "")

    if args.cmd == "preflight":
        require_paid_approval(cfg, approval)
        pricing.assert_paid_ready(plan.jobs)
        needed = check_affordable(estimate_eur, cfg, ledger)
        balance = _balance_check(plan, pricing, cfg, ledger)
        _summary(format_estimate(plan, pricing, cfg) +
                 f"\n\n✅ Preflight bestanden – Reservierung von {needed:.2f} € möglich "
                 f"(verbleibend {remaining_eur(cfg, ledger):.2f} €). Runway-Guthaben: {balance:.0f} Credits.")
        return 0

    if args.cmd == "reserve":
        require_paid_approval(cfg, approval)
        pricing.assert_paid_ready(plan.jobs)
        entry = reserve(args.run_id, estimate_eur, cfg, ledger,
                        meta={"profile": args.profile, "scenes": plan.scenes, "jobs": len(plan.jobs)})
        _summary(f"🔒 Reserviert: {entry['reserved_eur']:.2f} € für Lauf `{args.run_id}`")
        return 0

    if args.cmd == "render":
        require_paid_approval(cfg, approval)
        entry = ledger.entry(args.run_id)
        if not entry or entry["status"] != "reserved":
            raise BudgetError(f"Keine offene Reservierung für Lauf {args.run_id} – kein Render")
        if estimate_eur > float(entry["reserved_eur"]):
            raise BudgetError("Plan ist teurer als die Reservierung – Abbruch")
        pricing.assert_paid_ready(plan.jobs)
        from storyhouse.pipeline import paid_render
        from storyhouse.runway import RunwayClient

        out = Path(args.out)
        guard = SpendGuard(limit_eur=float(entry["reserved_eur"]), pricing=pricing, cfg=cfg,
                           log_path=out / "spend.json")
        client = RunwayClient(os.environ["RUNWAYML_API_SECRET"], guard)
        balance = {"before": client.credit_balance()}
        if balance["before"] is None:
            raise BudgetError("Runway-Guthaben nicht abrufbar – ohne Kontrollwert kein Render (fail-closed)")
        if balance["before"] < plan.total_credits:
            raise BudgetError(f"Runway-Guthaben {balance['before']:.0f} < benötigt {plan.total_credits:.0f} Credits")
        out.mkdir(parents=True, exist_ok=True)
        try:
            manifest = paid_render(ep, plan, client, out, scenes=args.scenes)
        finally:
            balance["after"] = client.credit_balance()
            (out / "balance.json").write_text(json.dumps(balance), encoding="utf-8")
        _summary(f"### 🎬 Render fertig\n\n`{manifest['video']}` · ausgegeben (geschätzt): {guard.spent_eur:.2f} €")
        return 0

    if args.cmd == "finalize":
        out = Path(args.out)
        spend = json.loads((out / "spend.json").read_text()) if (out / "spend.json").exists() else {}
        bal = json.loads((out / "balance.json").read_text()) if (out / "balance.json").exists() else {}
        delta = None
        if bal.get("before") is not None and bal.get("after") is not None:
            delta = max(0.0, float(bal["before"]) - float(bal["after"]))
        entry = ledger.entry(args.run_id)
        if not entry:
            print(f"Keine Reservierung für {args.run_id} – nichts abzuschließen")
            return 0
        if not spend and delta is None:
            # Ohne Nachweis bleibt die volle Reservierung als verbraucht stehen (fail-closed).
            spent_credits = pricing.eur_to_credits(float(entry["reserved_eur"]), cfg)
            note = "kein Ausgabenprotokoll – volle Reservierung verbucht"
        else:
            spent_credits = float(spend.get("spent_credits", 0.0))
            note = f"Abbruchgrund: {spend['stopped']}" if spend.get("stopped") else ""
            if delta is None and spend.get("stopped") == "uncertain":
                # Unklarer Task + kein Kontostand: volle Reservierung verbuchen.
                spent_credits = max(spent_credits, pricing.eur_to_credits(float(entry["reserved_eur"]), cfg))
                note += " | kein Kontostand-Nachweis – volle Reservierung verbucht"
        e = finalize(args.run_id, spent_credits, pricing, cfg, ledger, note=note, balance_delta_credits=delta)
        _summary(f"🧾 Abgeschlossen: Lauf `{args.run_id}` – {e['spent_eur']:.2f} € verbucht · "
                 f"verbleibend {remaining_eur(cfg, ledger):.2f} € von {cfg.total_budget_eur:.2f} €")
        return 0

    raise SystemExit(f"Unbekannter Befehl {args.cmd}")


if __name__ == "__main__":
    sys.exit(main())
