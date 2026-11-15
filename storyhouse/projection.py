"""Hochrechnung der Produktionskosten auf längere Filme (nur Rechnung, keine API).

Grundlage ist der echte, spezifikationsgeprüfte Plan einer Episode (Credits pro Filmsekunde)
und die Preistabelle config/pricing.yaml. Dazu kommt eine Nachdrehquote: KI-Video gelingt
selten im ersten Versuch (Standard 1,5 = jede zweite Einstellung wird einmal neu erzeugt).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from storyhouse.budget import BudgetConfig, Pricing, with_margin
from storyhouse.episode import Episode
from storyhouse.plan import build_plan

# ElevenLabs-Tarife (Credits/Monat, USD/Monat) – Stand 15.11.2026, elevenlabs.io/pricing
EL_PLANS = [("Free (nicht kommerziell)", 10_000, 0.0), ("Starter", 30_000, 6.0), ("Creator", 121_000, 22.0),
            ("Pro", 600_000, 99.0)]


@dataclass
class Projection:
    minutes: float
    profile: str
    runway_credits: float
    elevenlabs_credits: float
    eur: float
    eur_with_margin: float
    runs_needed: int
    el_plan: str
    el_plan_usd: float
    fits_total_budget: bool


def project(ep: Episode, pricing: Pricing, cfg: BudgetConfig, minutes: float, profile: str = "final",
            retake_factor: float = 1.5, audio_retake: float = 1.2, music: bool = True) -> Projection:
    if retake_factor < 1 or audio_retake < 1:
        raise ValueError("Nachdrehquoten müssen >= 1 sein")
    plan = build_plan(ep, pricing, profile=profile, include_music=music)
    seconds = minutes * 60.0
    scale = seconds / ep.duration
    rw = plan.credits("runway") * scale * retake_factor
    el = plan.credits("elevenlabs") * scale * audio_retake
    eur = pricing.credits_to_eur(rw, cfg) + pricing.credits_to_eur(el, cfg, "elevenlabs")
    needed = with_margin(eur, cfg)
    plan_name, plan_usd = next(((n, usd) for n, cr, usd in EL_PLANS if el <= cr and usd > 0),
                               ("Pro oder mehr", EL_PLANS[-1][2]))
    return Projection(minutes, profile, round(rw), round(el), round(eur, 2), needed,
                      max(1, math.ceil(needed / cfg.max_per_run_eur)), plan_name, plan_usd,
                      needed <= cfg.total_budget_eur)


def table(ep: Episode, pricing: Pricing, cfg: BudgetConfig, minutes=(1, 5, 10), retake_factor: float = 1.5) -> str:
    rows = ["| Länge | Profil | Runway-Credits | ElevenLabs-Credits | Kosten inkl. USt. | mit Puffer | Läufe (≤ 25 €) "
            "| ElevenLabs-Tarif | im 200-€-Budget |",
            "|---|---|---:|---:|---:|---:|---:|---|---|"]
    for prof in ("test", "final"):
        for m in minutes:
            p = project(ep, pricing, cfg, m, prof, retake_factor)
            rows.append(f"| {m:g} min | {prof} | {p.runway_credits:,} | {p.elevenlabs_credits:,} | {p.eur:.2f} € | "
                        f"{p.eur_with_margin:.2f} € | {p.runs_needed} | {p.el_plan} ({p.el_plan_usd:.0f} $/Monat) | "
                        f"{'ja' if p.fits_total_budget else 'NEIN'} |")
    return "\n".join(rows).replace(",", ".") + (
        f"\n\nNachdrehquote Video {retake_factor:g}×, Ton 1,2×; Basis: {ep.slug} ({ep.duration:.0f} s). "
        "ElevenLabs-Abo kommt zusätzlich; mit Abo sind die ElevenLabs-Credits im Monatsbetrag enthalten.")
