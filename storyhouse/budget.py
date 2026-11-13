"""Verbindliche Budgetkontrolle.

Sicherungsebenen:
  1. Vorab-Prüfung   – Preise geprüft & aktuell, Schätzung (+Puffer, inkl. MwSt.)
                       passt in Lauf-Limit UND Restbudget, keine offene Reservierung.
  2. Reservierung    – wird im Ledger gespeichert und gepusht, BEVOR Geld fließt.
                       Offene Reservierungen zählen voll (fail-closed) und
                       blockieren weitere Läufe, bis sie abgeschlossen sind.
  3. Laufzeit-Wächter – vor jedem Aufruf: passt der Job noch in die Reservierung?
                       Nach der Task-Anlage: stimmen Runways gemeldete Kosten mit
                       unserer Tabelle? Wenn nicht: Task stornieren, Lauf stoppen.
  4. Verbuchung      – tatsächliche Kosten = Maximum aus (Summe der von Runway
                       gemeldeten Task-Kosten bzw. Schätzungen) und Kontostand-Delta.
"""
from __future__ import annotations

import datetime as dt
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

HARD_CEILING_EUR = 200.00     # absolute Obergrenze, per Konfiguration nicht erhöhbar
PRICE_DRIFT_TOLERANCE = 0.05  # 5 % (+1 Credit) Abweichung zwischen Runway-Angabe und Tabelle


class BudgetError(RuntimeError):
    """Budgetregel verletzt – Lauf muss abbrechen."""


class PriceDriftError(BudgetError):
    """Runway meldet höhere Kosten als unsere Preistabelle."""


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
    vat_rate: float
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
            vat_rate=float(raw.get("vat_rate", 0.19)),
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
        if self.vat_rate < 0.19:
            raise BudgetError("vat_rate muss mindestens 0.19 (deutsche USt.) betragen")
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
    def _entry(self, section: str, model: str) -> dict[str, Any]:
        try:
            return dict(self.raw[section][model])
        except (KeyError, TypeError) as exc:
            raise BudgetError(f"Kein Preis für {section}/{model} hinterlegt") from exc

    def video_credits(self, model: str, seconds: int) -> float:
        return float(self._entry("video", model)["credits_per_second"]) * int(seconds)

    def image_credits(self, model: str) -> float:
        return float(self._entry("image", model)["credits_per_image"])

    def tts_credits(self, model: str, text: str) -> float:
        e = self._entry("tts", model)
        return float(max(int(e["min_credits"]), math.ceil(len(text) / float(e["chars_per_credit"]))))

    def sfx_credits(self, model: str, duration: float) -> float:
        e = self._entry("sound_effect", model)
        return float(math.ceil(float(duration) * float(e["credits_per_second"])))

    SECTION_BY_KIND = {"video": "video", "image": "image", "tts": "tts", "sfx": "sound_effect"}

    def is_verified(self, kind: str, model: str) -> bool:
        return bool(self._entry(self.SECTION_BY_KIND[kind], model).get("verified", False))

    def verified_on(self) -> dt.date:
        return dt.date.fromisoformat(str(self.raw["verified_on"]))

    def assert_paid_ready(self, jobs: list, today: dt.date | None = None) -> None:
        """Bezahlte Läufe nur mit geprüften, aktuellen Preisen."""
        today = today or dt.date.today()
        age = (today - self.verified_on()).days
        if age > int(self.raw.get("max_age_days", 45)):
            raise BudgetError(
                f"Preistabelle ist {age} Tage alt (Prüfung vom {self.verified_on()}). "
                "Preise mit docs.dev.runwayml.com abgleichen und verified_on per PR aktualisieren."
            )
        bad = sorted({f"{j.kind}/{j.model}" for j in jobs if not self.is_verified(j.kind, j.model)})
        if bad:
            raise BudgetError(f"Ungeprüfte Preise für: {', '.join(bad)} – bezahlter Lauf verweigert")

    def credits_to_eur(self, credits: float, cfg: BudgetConfig) -> float:
        return round(credits * self.usd_per_credit * cfg.eur_per_usd * (1.0 + cfg.vat_rate), 4)

    def eur_to_credits(self, eur: float, cfg: BudgetConfig) -> float:
        return eur / (self.usd_per_credit * cfg.eur_per_usd * (1.0 + cfg.vat_rate))


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
        try:
            data = json.loads(p.read_text(encoding="utf-8") or "{}") or {}
        except json.JSONDecodeError as exc:
            raise BudgetError(f"Ledger {p} ist beschädigt – Abbruch (fail-closed)") from exc
        if data.get("version") != 1 or not isinstance(data.get("entries"), list):
            raise BudgetError(f"Ledger {p} hat ein unbekanntes Format – Abbruch (fail-closed)")
        for e in data["entries"]:
            if e.get("status") not in ("reserved", "finalized") or "run_id" not in e:
                raise BudgetError(f"Ledger-Eintrag ungültig: {e!r} – Abbruch (fail-closed)")
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

    def open_reservations(self) -> list[dict[str, Any]]:
        return [e for e in self.entries if e["status"] == "reserved"]

    def committed_eur(self) -> float:
        """Verbrauchtes + reserviertes Geld (offene Reservierungen zählen voll)."""
        total = 0.0
        for e in self.entries:
            if e["status"] == "finalized":
                total += float(e["spent_eur"])
            else:
                total += max(float(e["reserved_eur"]), float(e.get("spent_eur", 0.0)))
        return round(total, 4)


def remaining_eur(cfg: BudgetConfig, ledger: Ledger) -> float:
    return round(cfg.total_budget_eur - ledger.committed_eur(), 4)


def with_margin(eur: float, cfg: BudgetConfig) -> float:
    return round(eur * (1.0 + cfg.safety_margin), 2)


def check_affordable(estimate_eur: float, cfg: BudgetConfig, ledger: Ledger) -> float:
    """Gibt den zu reservierenden Betrag zurück oder wirft BudgetError."""
    if estimate_eur <= 0:
        raise BudgetError("Kostenschätzung ist 0 – das ist nie plausibel, Abbruch")
    open_runs = ledger.open_reservations()
    if open_runs:
        ids = ", ".join(e["run_id"] for e in open_runs)
        raise BudgetError(
            f"Offene Reservierung(en): {ids}. Erst abschließen (finalize), dann neuer Lauf. "
            "Schutz gegen doppelte/parallele Läufe."
        )
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
    credits = max(float(spent_credits), float(balance_delta_credits or 0.0))
    entry["spent_credits"] = credits
    entry["spent_eur"] = pricing.credits_to_eur(credits, cfg)
    entry["status"] = "finalized"
    entry["finalized_utc"] = _now()
    if balance_delta_credits is not None:
        entry["balance_delta_credits"] = float(balance_delta_credits)
    if note:
        entry["note"] = note
    if entry["spent_eur"] > float(entry["reserved_eur"]):
        entry["overrun"] = True
        entry["note"] = (entry.get("note", "") + " | ACHTUNG: Kosten über Reservierung").strip(" |")
    ledger.save()
    return entry


# ---------------------------------------------------------------------
# Laufzeit-Wächter
# ---------------------------------------------------------------------
@dataclass
class SpendGuard:
    """Wird vom Runway-Client vor und nach JEDEM kostenpflichtigen Aufruf gefragt."""

    limit_eur: float
    pricing: Pricing
    cfg: BudgetConfig
    log_path: Path | None = None
    charges: dict[str, float] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    stopped: str | None = None

    def __post_init__(self) -> None:
        self._flush()  # Protokoll existiert ab Start (0 Credits)

    @property
    def spent_credits(self) -> float:
        return float(sum(self.charges.values()))

    @property
    def spent_eur(self) -> float:
        return self.pricing.credits_to_eur(self.spent_credits, self.cfg)

    def _event(self, job_id: str, event: str, credits: float, **extra: Any) -> None:
        self.calls.append({"job": job_id, "event": event, "credits": credits, "utc": _now(), **extra})
        self._flush()

    def authorize(self, job_id: str, credits: float) -> None:
        """Vor dem Senden: Kosten vormerken (gilt als ausgegeben, bis Runway etwas anderes meldet)."""
        if self.stopped:
            raise BudgetError(f"Lauf gestoppt ({self.stopped}) – keine weiteren Aufrufe")
        if credits <= 0:
            raise BudgetError(f"{job_id}: Kostenschätzung fehlt oder ist 0 – Abbruch")
        if job_id in self.charges:
            raise BudgetError(f"{job_id}: wurde bereits eingereicht – keine Doppel-Einreichung")
        projected = self.pricing.credits_to_eur(self.spent_credits + credits, self.cfg)
        if projected > self.limit_eur + 1e-9:
            raise BudgetError(
                f"{job_id}: würde {projected:.2f} € erreichen, Reservierung erlaubt {self.limit_eur:.2f} € – STOP"
            )
        self.charges[job_id] = float(credits)
        self._event(job_id, "authorized", credits)

    def observe_estimate(self, job_id: str, api_credits: float | None) -> None:
        """Nach der Task-Anlage: Runway nennt die Maximalkosten. Höher als erwartet -> Stopp."""
        ours = self.charges.get(job_id, 0.0)
        if api_credits is None:
            self._event(job_id, "no_api_estimate", ours)
            return
        api_credits = float(api_credits)
        self.charges[job_id] = max(ours, api_credits)
        self._event(job_id, "api_estimate", api_credits, ours=ours)
        if api_credits > ours * (1 + PRICE_DRIFT_TOLERANCE) + 1:
            self.stopped = "price_drift"
            raise PriceDriftError(
                f"{job_id}: Runway meldet {api_credits:.0f} Credits, Tabelle {ours:.0f} – "
                "Preise haben sich geändert. Task wird storniert, Lauf gestoppt."
            )
        if self.pricing.credits_to_eur(self.spent_credits, self.cfg) > self.limit_eur + 1e-9:
            self.stopped = "limit"
            raise BudgetError(f"{job_id}: Runway-Kosten überschreiten die Reservierung – Stopp")

    def settle(self, job_id: str, final_credits: float | None) -> None:
        """Endgültige Kosten laut Runway (Task-Feld cost.credits)."""
        if final_credits is None:
            self._event(job_id, "no_final_cost", self.charges.get(job_id, 0.0))
            return
        self.charges[job_id] = float(final_credits)
        self._event(job_id, "settled", float(final_credits))

    def mark_uncertain(self, job_id: str, reason: str) -> None:
        """Ergebnis unbekannt (z. B. Verbindungsabbruch beim Anlegen): Kosten bleiben stehen, Lauf stoppt."""
        self.stopped = "uncertain"
        self._event(job_id, "uncertain", self.charges.get(job_id, 0.0), reason=reason[:200])

    def _flush(self) -> None:
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.write_text(
                json.dumps({"spent_credits": self.spent_credits, "stopped": self.stopped,
                            "charges": self.charges, "calls": self.calls}, indent=2),
                encoding="utf-8",
            )


# ---------------------------------------------------------------------
# Freigabe für bezahlte Läufe
# ---------------------------------------------------------------------
def require_paid_approval(cfg: BudgetConfig, approval_text: str | None, env: dict | None = None,
                          require_passphrase: bool = True) -> None:
    env = os.environ if env is None else env
    if not cfg.paid_rendering_enabled:
        raise ApprovalError(
            "Bezahltes Rendern ist gesperrt (config/budget.yaml: paid_rendering_enabled: false). "
            "Freischaltung nur per Pull Request durch die Produktion."
        )
    if (approval_text or "").strip() != cfg.approval_phrase:
        raise ApprovalError("Bestätigungsphrase fehlt oder ist falsch – kein bezahlter Render.")
    if not env.get("RUNWAYML_API_SECRET"):
        raise ApprovalError("RUNWAYML_API_SECRET ist nicht gesetzt (Secret der Umgebung 'paid-render').")
    if require_passphrase and len(env.get("ARTIFACT_PASSPHRASE", "")) < 16:
        raise ApprovalError(
            "ARTIFACT_PASSPHRASE fehlt oder ist kürzer als 16 Zeichen. Das Repo ist öffentlich – "
            "Renderergebnisse werden nur verschlüsselt hochgeladen."
        )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
