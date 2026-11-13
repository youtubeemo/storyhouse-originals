"""Verbindliche Budgetkontrolle.

Drei Sicherungsebenen:
  1. Vorab-Prüfung   – Kostenschätzung (+Sicherheitsaufschlag) muss in das
                       Lauf-Limit UND in das verbleibende Gesamtbudget passen.
  2. Reservierung    – Der geschätzte Betrag wird im Ledger reserviert und
                       (im Workflow) gepusht, BEVOR ein Cent ausgegeben wird.
                       Nicht abgeschlossene Reservierungen zählen voll als
                       verbraucht (fail-closed).
  3. Laufzeit-Wächter – Jeder einzelne API-Aufruf wird vor dem Absenden gegen
                       die Reservierung geprüft; bei Überschreitung bricht der
                       Lauf sofort ab.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BUDGET = REPO_ROOT / "config" / "budget.yaml"
DEFAULT_PRICING = REPO_ROOT / "config" / "pricing.yaml"

HARD_CEILING_EUR = 200.00  # absolute Obergrenze, nicht per Konfiguration erhöhbar


class BudgetError(RuntimeError):
    """Budgetregel verletzt – Lauf muss abbrechen."""


class ApprovalError(RuntimeError):
    """Fehlende Freigabe für einen kostenpflichtigen Lauf."""


# ---------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class BudgetConfig:
    paid_rendering_enabled: bool
    total_budget_eur: float
    max_per_run_eur: float
    safety_margin: float
    eur_per_usd: float
    warn_at_fraction: float
    approval_phrase: str
    ledger_branch: str = "budget-ledger"
    ledger_file: str = "ledger.json"

    @classmethod
    def load(cls, path: str | Path = DEFAULT_BUDGET) -> "BudgetConfig":
        with Path(path).open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        cfg = cls(
            paid_rendering_enabled=bool(raw.get("paid_rendering_enabled", False)),
            total_budget_eur=float(raw["total_budget_eur"]),
            max_per_run_eur=float(raw["max_per_run_eur"]),
            safety_margin=float(raw.get("safety_margin", 0.25)),
            eur_per_usd=float(raw.get("eur_per_usd", 1.0)),
            warn_at_fraction=float(raw.get("warn_at_fraction", 0.75)),
            approval_phrase=str(raw["approval_phrase"]),
            ledger_branch=str(raw.get("ledger_branch", "budget-ledger")),
            ledger_file=str(raw.get("ledger_file", "ledger.json")),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.total_budget_eur <= 0 or self.total_budget_eur > HARD_CEILING_EUR:
            raise BudgetError(
                f"total_budget_eur muss in (0, {HARD_CEILING_EUR}] liegen, ist {self.total_budget_eur}"
            )
        if not 0 < self.max_per_run_eur <= self.total_budget_eur:
            raise BudgetError("max_per_run_eur muss > 0 und <= total_budget_eur sein")
        if self.safety_margin < 0.10:
            raise BudgetError("safety_margin muss mindestens 0.10 (10 %) betragen")
        if self.eur_per_usd < 0.85:
            raise BudgetError("eur_per_usd unter 0.85 ist nicht konservativ genug")
        if len(self.approval_phrase.strip()) < 10:
            raise BudgetError("approval_phrase zu kurz")


@dataclass(frozen=True)
class Pricing:
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path = DEFAULT_PRICING) -> "Pricing":
        with Path(path).open(encoding="utf-8") as fh:
            return cls(raw=yaml.safe_load(fh))

    @property
    def usd_per_credit(self) -> float:
        return float(self.raw["usd_per_credit"])

    def profile(self, name: str) -> dict[str, str]:
        try:
            return dict(self.raw["profiles"][name])
        except KeyError as exc:
            raise BudgetError(f"Unbekanntes Profil: {name}") from exc

    # Credits je Jobart -------------------------------------------------
    def video_credits(self, model: str, seconds: int) -> float:
        rate = self._lookup("video", model)
        return float(rate) * int(seconds)

    def image_credits(self, model: str) -> float:
        return float(self._lookup("image", model))

    def tts_credits(self, model: str, text: str) -> float:
        cfg = self._lookup("tts", model)
        return max(float(cfg["min_credits"]), math.ceil(len(text) * float(cfg["credits_per_char"])))

    def sfx_credits(self, model: str) -> float:
        return float(self._lookup("sound_effect", model))

    def _lookup(self, section: str, model: str) -> Any:
        try:
            return self.raw[section][model]
        except KeyError as exc:
            # Unbekannte Modelle niemals mit 0 bewerten.
            raise BudgetError(f"Kein Preis für {section}/{model} hinterlegt") from exc

    def credits_to_eur(self, credits: float, cfg: BudgetConfig) -> float:
        return round(credits * self.usd_per_credit * cfg.eur_per_usd, 4)


# ---------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------
EMPTY_LEDGER = {"version": 1, "entries": []}


@dataclass
class Ledger:
    path: Path
    data: dict[str, Any] = field(default_factory=lambda: json.loads(json.dumps(EMPTY_LEDGER)))

    @classmethod
    def load(cls, path: str | Path) -> "Ledger":
        p = Path(path)
        if not p.exists():
            return cls(path=p)
        data = json.loads(p.read_text(encoding="utf-8") or "{}") or {}
        if data.get("version") != 1 or not isinstance(data.get("entries"), list):
            raise BudgetError(f"Ledger {p} hat ein unbekanntes Format – Abbruch (fail-closed)")
        return cls(path=p, data=data)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    @property
    def entries(self) -> list[dict[str, Any]]:
        return self.data["entries"]

    def entry(self, run_id: str) -> dict[str, Any] | None:
        for e in self.entries:
            if e["run_id"] == run_id:
                return e
        return None

    def committed_eur(self) -> float:
        """Verbrauchtes + reserviertes Geld (offene Reservierungen zählen voll)."""
        total = 0.0
        for e in self.entries:
            if e["status"] == "finalized":
                total += float(e["spent_eur"])
            else:
                total += max(float(e["reserved_eur"]), float(e.get("spent_eur", 0.0)))
        return round(total, 4)

    def spent_eur(self) -> float:
        return round(sum(float(e.get("spent_eur", 0.0)) for e in self.entries), 4)


def remaining_eur(cfg: BudgetConfig, ledger: Ledger) -> float:
    return round(cfg.total_budget_eur - ledger.committed_eur(), 4)


def with_margin(eur: float, cfg: BudgetConfig) -> float:
    return round(eur * (1.0 + cfg.safety_margin), 2)


def check_affordable(estimate_eur: float, cfg: BudgetConfig, ledger: Ledger) -> float:
    """Gibt den zu reservierenden Betrag zurück oder wirft BudgetError."""
    needed = with_margin(estimate_eur, cfg)
    if needed > cfg.max_per_run_eur:
        raise BudgetError(
            f"Geschätzt {needed:.2f} € (inkl. {cfg.safety_margin:.0%} Puffer) "
            f"> Lauf-Limit {cfg.max_per_run_eur:.2f} €"
        )
    rem = remaining_eur(cfg, ledger)
    if needed > rem:
        raise BudgetError(
            f"Geschätzt {needed:.2f} € > verbleibendes Gesamtbudget {rem:.2f} € "
            f"(von {cfg.total_budget_eur:.2f} €)"
        )
    return needed


def reserve(run_id: str, estimate_eur: float, cfg: BudgetConfig, ledger: Ledger, meta: dict | None = None) -> dict:
    if ledger.entry(run_id):
        raise BudgetError(f"Run-ID {run_id} existiert bereits im Ledger")
    amount = check_affordable(estimate_eur, cfg, ledger)
    entry = {
        "run_id": run_id,
        "status": "reserved",
        "created_utc": _now(),
        "estimate_eur": round(estimate_eur, 2),
        "reserved_eur": amount,
        "spent_eur": 0.0,
        "spent_credits": 0.0,
        **(meta or {}),
    }
    ledger.entries.append(entry)
    ledger.save()
    return entry


def finalize(run_id: str, spent_credits: float, pricing: Pricing, cfg: BudgetConfig, ledger: Ledger,
             note: str = "", balance_delta_credits: float | None = None) -> dict:
    entry = ledger.entry(run_id)
    if not entry:
        raise BudgetError(f"Keine Reservierung für {run_id}")
    if entry["status"] == "finalized":
        return entry
    # Tatsächlicher Kontostand-Delta (falls bekannt) hat Vorrang, wenn er höher ist.
    credits = max(float(spent_credits), float(balance_delta_credits or 0.0))
    entry["spent_credits"] = credits
    entry["spent_eur"] = pricing.credits_to_eur(credits, cfg)
    entry["status"] = "finalized"
    entry["finalized_utc"] = _now()
    if note:
        entry["note"] = note
    ledger.save()
    return entry


# ---------------------------------------------------------------------
# Laufzeit-Wächter
# ---------------------------------------------------------------------
@dataclass
class SpendGuard:
    """Wird vom Runway-Client vor JEDEM kostenpflichtigen Aufruf gefragt."""

    limit_eur: float
    pricing: Pricing
    cfg: BudgetConfig
    log_path: Path | None = None
    spent_credits: float = 0.0
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._flush()  # Protokoll existiert ab Start (0 Credits), auch wenn nichts gesendet wird

    @property
    def spent_eur(self) -> float:
        return self.pricing.credits_to_eur(self.spent_credits, self.cfg)

    def authorize(self, job_id: str, credits: float) -> None:
        if credits <= 0:
            raise BudgetError(f"{job_id}: Kostenschätzung fehlt oder ist 0 – Abbruch")
        projected = self.pricing.credits_to_eur(self.spent_credits + credits, self.cfg)
        if projected > self.limit_eur + 1e-9:
            raise BudgetError(
                f"{job_id}: würde {projected:.2f} € erreichen, Reservierung erlaubt {self.limit_eur:.2f} € – STOP"
            )
        # Kosten werden beim Absenden verbucht (konservativ, auch wenn der Task scheitert).
        self.spent_credits += credits
        self.calls.append({"job": job_id, "credits": credits, "utc": _now()})
        self._flush()

    def _flush(self) -> None:
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.write_text(
                json.dumps({"spent_credits": self.spent_credits, "calls": self.calls}, indent=2),
                encoding="utf-8",
            )


# ---------------------------------------------------------------------
# Freigabe für bezahlte Läufe
# ---------------------------------------------------------------------
def require_paid_approval(cfg: BudgetConfig, approval_text: str | None, env: dict | None = None) -> None:
    env = os.environ if env is None else env
    if not cfg.paid_rendering_enabled:
        raise ApprovalError(
            "Bezahltes Rendern ist gesperrt (config/budget.yaml: paid_rendering_enabled: false). "
            "Freischaltung nur per Pull Request durch die Produktion."
        )
    if (approval_text or "").strip() != cfg.approval_phrase:
        raise ApprovalError("Bestätigungsphrase fehlt oder ist falsch – kein bezahlter Render.")
    if not env.get("RUNWAYML_API_SECRET"):
        raise ApprovalError("RUNWAYML_API_SECRET ist nicht gesetzt (GitHub-Secret der Umgebung 'paid-render').")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
