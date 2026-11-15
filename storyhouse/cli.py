"""Kommandozeile: python -m storyhouse <befehl> ...

Kostenlose Befehle:  validate, estimate, budget-status, dry-run
Bezahlter Ablauf:    preflight -> reserve -> render -> finalize
Bild: Runway (RUNWAYML_API_SECRET) · Ton: ElevenLabs (ELEVENLABS_API_KEY)
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
    finalize, remaining_eur, require_paid_approval, require_paid_scope, require_scope_budget, reserve,
)
from storyhouse.episode import EpisodeError, load_episode
from storyhouse.plan import build_plan, format_estimate
from storyhouse.runway_spec import SpecError


def _errors() -> tuple:
    from storyhouse.elevenlabs import ElevenLabsError
    from storyhouse.ffmpeg import FFmpegError
    from storyhouse.runway import RunwayError

    return (BudgetError, ApprovalError, EpisodeError, SpecError, ElevenLabsError, RunwayError, FFmpegError)

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
    p.add_argument("--audio-provider", default=None, choices=["elevenlabs", "runway"],
                   help="Tonquelle (Standard: audio.provider aus episode.yaml)")
    p.add_argument("--no-music", action="store_true", help="Keine Musik generieren (Drone als Musikbett)")


def _summary(text: str) -> None:
    """Schreibt zusätzlich in die GitHub-Step-Summary, falls vorhanden."""
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n\n")


def _elevenlabs_check(plan, pricing, cfg, ep) -> dict:
    """Kostenlose Kontoprüfung bei ElevenLabs: Tarif/Lizenz, Kontingent, KEIN Overage, Stimmen vorhanden."""
    from storyhouse.elevenlabs import ElevenLabsClient, check_subscription

    guard = SpendGuard(limit_eur=0.0, pricing=pricing, cfg=cfg, provider="elevenlabs")
    client = ElevenLabsClient(os.environ["ELEVENLABS_API_KEY"], guard)
    sub = client.subscription()
    if sub is None and client.last_status in (401, 403):
        raise ApprovalError(
            f"ElevenLabs verweigert die Kontoabfrage (HTTP {client.last_status}): Schlüssel ungültig oder ohne "
            "Berechtigung 'User: Read'. Kein bezahlter Lauf – nichts wurde generiert."
        )
    info = check_subscription(sub, plan.credits("elevenlabs"), cfg,
                              needs_music=any(j.kind == "music" for j in plan.jobs))
    voices = sorted({j.path.rsplit("/", 1)[-1] for j in plan.jobs if j.provider == "elevenlabs" and j.kind == "tts"})
    for voice_id in voices:
        ok = client.voice_available(voice_id)
        if ok is None and client.last_status in (401, 403):
            raise ApprovalError(f"ElevenLabs verweigert die Stimmenabfrage (HTTP {client.last_status}): "
                                "Berechtigung 'Voices: Read' fehlt. Kein bezahlter Lauf.")
        if ok is None:
            raise BudgetError(f"ElevenLabs-Stimme {voice_id} nicht prüfbar (Netz/Berechtigung 'voices_read') – Abbruch")
        if not ok:
            raise BudgetError(f"ElevenLabs-Stimme {voice_id} ist in diesem Konto nicht verfügbar – "
                              "voice_id in episode.yaml anpassen")
    if info["has_open_invoices"]:
        _summary("⚠️ ElevenLabs meldet offene Rechnungen – bitte im Konto prüfen.")
    return info


def _balance_check(plan, pricing, cfg, ledger) -> float:
    """Kostenlose Abfrage des Runway-Guthabens (GET /v1/organization)."""
    from storyhouse.runway import RunwayClient

    client = RunwayClient(os.environ["RUNWAYML_API_SECRET"], SpendGuard(limit_eur=0.0, pricing=pricing, cfg=cfg))
    bal = client.credit_balance()
    if bal is None:
        raise BudgetError("Runway-Guthaben nicht abrufbar (Schlüssel/Netz prüfen) – Abbruch")
    if bal < plan.credits("runway"):
        raise BudgetError(f"Runway-Guthaben {bal:.0f} Credits < benötigt {plan.credits("runway"):.0f} Credits")
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
        if name == "render":
            p.add_argument("--cache", default=None,
                           help="Ordner mit Zwischenergebnissen früherer Läufe: unveränderte Aufträge kosten 0 €")

    p = sub.add_parser("kosten", help="Kosten für längere Filme hochrechnen (keine API)"); _common(p)
    p.add_argument("--minuten", nargs="+", type=float, default=[1, 5, 10])
    p.add_argument("--nachdrehquote", type=float, default=1.5)

    p = sub.add_parser("tools", help="Werkzeug-Katalog (config/providers.yaml) anzeigen"); _common(p)

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
    except _errors() as exc:
        _summary(f"❌ **Abbruch:** {exc}")
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    ep = load_episode(args.episode)
    cfg = BudgetConfig.load(args.budget_config)
    pricing = Pricing.load(args.pricing)

    if args.cmd == "validate":
        print(f"✅ {ep.slug}: {len(ep.scenes)} Szenen, {ep.duration:.1f} s, {len(ep.dialogue)} Dialogzeilen – gültig")
        return 0

    if args.cmd == "kosten":
        from storyhouse.projection import table
        _summary("### Kostenhochrechnung\n\n" + table(ep, pricing, cfg, args.minuten, args.nachdrehquote))
        return 0

    if args.cmd == "tools":
        from storyhouse.providers import load as load_tools
        for t in load_tools(REPO_ROOT / "config" / "providers.yaml"):
            print(f"- {t['name']}: {t['status']} · {t['kosten']} · {t['naechster_schritt']}")
        return 0

    if args.cmd == "budget-status":
        ledger = Ledger.load(args.ledger)
        rem = remaining_eur(cfg, ledger)
        _summary(
            f"### Budgetstand\n\nGesamtbudget: {cfg.total_budget_eur:.2f} € · "
            f"verbraucht/reserviert: {ledger.committed_eur():.2f} € · **verbleibend: {rem:.2f} €** · "
            f"Läufe: {len(ledger.entries)} · bezahltes Rendern: "
            f"{'FREIGESCHALTET' if cfg.paid_rendering_enabled else 'GESPERRT'}"
            + (f" (Umfang: Profile {', '.join(cfg.scope_profiles or ['alle'])}, "
               f"Szenen {', '.join(cfg.scope_scenes or ['alle'])})" if cfg.paid_rendering_enabled else "")
        )
        if ledger.committed_eur() >= cfg.total_budget_eur * cfg.warn_at_fraction:
            _summary(f"⚠️ Warnschwelle {cfg.warn_at_fraction:.0%} des Budgets erreicht.")
        return 0

    if args.cmd == "close-run":
        ledger = Ledger.load(args.ledger)
        e = finalize(args.run_id, args.spent_credits, pricing, cfg, ledger, note=f"manuell: {args.reason}")
        _summary(f"🧾 Lauf `{args.run_id}` manuell abgeschlossen: {e['spent_eur']:.2f} € verbucht")
        return 0

    plan = build_plan(ep, pricing, profile=args.profile, scenes=args.scenes, include_audio=not args.no_audio,
                      audio_provider=args.audio_provider,
                      include_music=cfg.el_music_license_confirmed and not args.no_music)
    estimate_eur = plan.total_eur(pricing, cfg)
    uses_el = "elevenlabs" in plan.providers

    if args.cmd == "estimate":
        _summary(format_estimate(plan, pricing, cfg))
        return 0

    if args.cmd == "dry-run":
        from storyhouse.pipeline import dry_run

        dims = (360, 640, 12) if args.preview else (None, None, None)
        manifest = dry_run(ep, Path(args.out), pricing, cfg, scenes=args.scenes, profile=args.profile,
                           width=dims[0], height=dims[1], fps=dims[2], plan=plan)
        pr = manifest["probe"]
        extra = f" (Film {manifest['film_duration_s']:.1f} s + Outro)" if manifest.get("outro") else ""
        loop = f"\nLoop-Fassung ohne Outro: `{manifest['loop_video']}`" if manifest.get("loop_video") else ""
        _summary(
            f"### 🎬 Dry-Run fertig (0 € ausgegeben, kein API-Aufruf)\n\n`{manifest['video']}` · "
            f"{pr['width']}×{pr['height']} · {pr['fps']} fps · {pr['duration']:.2f} s{extra} · "
            f"Bild/Ton-Abweichung {manifest['av_drift_s']} s{loop}\n\n" + format_estimate(plan, pricing, cfg)
        )
        return 0

    ledger = Ledger.load(args.ledger)
    approval = os.environ.get("STORYHOUSE_APPROVAL", "")

    if args.cmd in ("preflight", "reserve", "render"):
        require_paid_scope(cfg, plan)
    if args.cmd in ("preflight", "reserve"):
        require_scope_budget(cfg, ledger, estimate_eur)

    if args.cmd == "preflight":
        require_paid_approval(cfg, approval, secrets=plan.required_secrets())
        pricing.assert_paid_ready(plan.jobs)
        needed = check_affordable(estimate_eur, cfg, ledger)
        balance = _balance_check(plan, pricing, cfg, ledger)
        text = (f"\n\n✅ Preflight bestanden – Reservierung von {needed:.2f} € möglich "
                f"(verbleibend {remaining_eur(cfg, ledger):.2f} €). Runway-Guthaben: {balance:.0f} Credits.")
        if uses_el:
            el = _elevenlabs_check(plan, pricing, cfg, ep)
            text += (f" ElevenLabs: Tarif `{el['tier']}`, {el['remaining']:.0f} Credits frei, "
                     f"benötigt {plan.credits('elevenlabs'):.0f}, Overage aus.")
        _summary(format_estimate(plan, pricing, cfg) + text)
        return 0

    if args.cmd == "reserve":
        require_paid_approval(cfg, approval, secrets=plan.required_secrets())
        pricing.assert_paid_ready(plan.jobs)
        entry = reserve(args.run_id, estimate_eur, cfg, ledger,
                        meta={"profile": args.profile, "scenes": plan.scenes, "jobs": len(plan.jobs),
                              "episode": plan.episode_slug})
        _summary(f"🔒 Reserviert: {entry['reserved_eur']:.2f} € für Lauf `{args.run_id}`")
        return 0

    if args.cmd == "render":
        require_paid_approval(cfg, approval, secrets=plan.required_secrets())
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
        clients: dict = {"runway": client}
        private_only = False
        el_client, el_usage = None, {}
        if uses_el:
            from storyhouse.elevenlabs import ElevenLabsClient

            el_guard = SpendGuard(limit_eur=float(entry["reserved_eur"]), pricing=pricing, cfg=cfg,
                                  log_path=out / "spend_elevenlabs.json", provider="elevenlabs")
            guard.link(el_guard)  # ein gemeinsames Euro-Limit für beide Anbieter
            el_client = ElevenLabsClient(os.environ["ELEVENLABS_API_KEY"], el_guard)
            clients["elevenlabs"] = el_client
            el_info = _elevenlabs_check(plan, pricing, cfg, ep)  # Konto unmittelbar vor dem Start erneut prüfen
            private_only = not el_info["commercial_license"]
            if private_only:
                _summary("🔒 ElevenLabs Free-Tarif: Ergebnis ist ein **privater, nicht kommerzieller Test** – "
                         "nicht veröffentlichen, nicht monetarisieren, nicht verbreiten.")
            el_usage["before"] = el_client.used_credits()
        balance = {"before": client.credit_balance()}
        if balance["before"] is None:
            raise BudgetError("Runway-Guthaben nicht abrufbar – ohne Kontrollwert kein Render (fail-closed)")
        if balance["before"] < plan.credits("runway"):
            raise BudgetError(f"Runway-Guthaben {balance['before']:.0f} < benötigt {plan.credits("runway"):.0f} Credits")
        out.mkdir(parents=True, exist_ok=True)
        try:
            cache = None
            if args.cache:
                from storyhouse.szenen_cache import SzenenCache
                cache = SzenenCache(args.cache)
                hit, rest = cache.coverage(plan.jobs)
                print(f"Zwischenspeicher: {len(hit)} Aufträge wiederverwendet, {len(rest)} neu")
            manifest = paid_render(ep, plan, clients, out, scenes=args.scenes, private_only=private_only, cache=cache)
        finally:
            balance["after"] = client.credit_balance()
            (out / "balance.json").write_text(json.dumps(balance), encoding="utf-8")
            if el_client:
                el_usage["after"] = el_client.used_credits()
                (out / "elevenlabs_usage.json").write_text(json.dumps(el_usage), encoding="utf-8")
        _summary(f"### 🎬 Render fertig\n\n`{manifest['video']}` · ausgegeben (geschätzt): "
                 f"{guard.run_spent_eur:.2f} € · Bild/Ton-Abweichung {manifest['av_drift_s']} s")
        return 0

    if args.cmd == "finalize":
        out = Path(args.out)

        def read(name: str) -> dict:
            f = out / name
            try:
                return json.loads(f.read_text()) if f.exists() else {}
            except json.JSONDecodeError:
                return {}

        def delta(d: dict, sign: float) -> float | None:
            if d.get("before") is None or d.get("after") is None:
                return None
            return max(0.0, sign * (float(d["after"]) - float(d["before"])))

        entry = ledger.entry(args.run_id)
        if not entry:
            print(f"Keine Reservierung für {args.run_id} – nichts abzuschließen")
            return 0
        spend, el_spend = read("spend.json"), read("spend_elevenlabs.json")
        rw_delta = delta(read("balance.json"), -1.0)            # Runway-Guthaben sinkt
        el_delta = delta(read("elevenlabs_usage.json"), +1.0)   # ElevenLabs-Verbrauch steigt
        reserved_credits = pricing.eur_to_credits(float(entry["reserved_eur"]), cfg)
        notes: list[str] = []
        el_credits = 0.0
        if not spend and rw_delta is None and not el_spend and el_delta is None:
            # Ohne jeden Nachweis bleibt die volle Reservierung als verbraucht stehen (fail-closed).
            spent_credits = reserved_credits
            notes.append("kein Ausgabenprotokoll – volle Reservierung verbucht")
        else:
            spent_credits = float(spend.get("spent_credits", 0.0))
            el_credits = max(float(el_spend.get("spent_credits", 0.0)), el_delta or 0.0)
            for name, log in (("Runway", spend), ("ElevenLabs", el_spend)):
                if log.get("stopped"):
                    notes.append(f"Abbruchgrund {name}: {log['stopped']}")
            unclear = ((spend.get("stopped") == "uncertain" and rw_delta is None)
                       or (el_spend.get("stopped") == "uncertain" and el_delta is None))
            if unclear:
                # Unklarer Aufruf + kein Kontostand: volle Reservierung verbuchen.
                spent_credits = max(spent_credits, reserved_credits)
                el_credits = 0.0
                notes.append("kein Kontostand-Nachweis – volle Reservierung verbucht")
        e = finalize(args.run_id, spent_credits, pricing, cfg, ledger, note=" | ".join(notes),
                     balance_delta_credits=rw_delta, elevenlabs_credits=el_credits)
        _summary(f"🧾 Abgeschlossen: Lauf `{args.run_id}` – {e['spent_eur']:.2f} € verbucht"
                 + (f" (davon ElevenLabs {e['elevenlabs_eur']:.2f} € / {e['elevenlabs_credits']:.0f} Credits)"
                    if e.get("elevenlabs_credits") else "")
                 + f" · verbleibend {remaining_eur(cfg, ledger):.2f} € von {cfg.total_budget_eur:.2f} €")
        return 0

    raise SystemExit(f"Unbekannter Befehl {args.cmd}")


if __name__ == "__main__":
    sys.exit(main())
