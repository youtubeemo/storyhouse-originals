"""Minimaler ElevenLabs-API-Client (nur Standardbibliothek).

Sicherheitsprinzipien (wie beim Runway-Client):
  * Der API-Schlüssel kommt ausschließlich aus der Umgebung (ELEVENLABS_API_KEY),
    steht nur im Header ``xi-api-key`` und erscheint nie in Logs, repr() oder
    Fehlermeldungen.
  * Jeder kostenpflichtige Aufruf läuft durch den SpendGuard: vorher
    (Reservierung) und nachher (von ElevenLabs gemeldete Kosten, Header
    ``character-cost``).
  * Generierungen werden NIE blind wiederholt. Bei Verbindungsabbruch,
    Zeitüberschreitung oder 5xx ist unklar, ob Credits verbraucht wurden ->
    Kosten bleiben verbucht, der Lauf stoppt. Einzige Ausnahme: HTTP 429
    (Rate-Limit, es wurde sicher nichts generiert).
  * Der Client kauft nichts: Er ruft keine Abo-, Kauf- oder
    Guthaben-Endpunkte auf. Der Preflight bricht ab, wenn im Konto
    nutzungsbasierte Abrechnung (Overage) aktiv ist.
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from storyhouse.budget import ApprovalError, BudgetConfig, BudgetError, SpendGuard
from storyhouse.elevenlabs_spec import (
    BASE_URL, OUTPUT_FORMAT, SUBSCRIPTION_PATH, VOICE_ID, VOICE_PATH, validate_request,
)

NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError)
FREE_TIERS = {"free"}
FREE_STATUSES = {"free", "free_disabled"}
OK_STATUSES = {"active", "trialing"}

# transport(method, url, headers, body_bytes|None) -> (status_code, body_bytes, headers)
Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes, dict[str, str]]]


class ElevenLabsError(RuntimeError):
    pass


class UncertainGenerationError(ElevenLabsError):
    """Unklar, ob ElevenLabs die (kostenpflichtige) Generierung ausgeführt hat."""


def urllib_transport(method: str, url: str, headers: dict[str, str], body: bytes | None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310 (feste HTTPS-URL)
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as err:
        return err.code, err.read() or b"", dict(err.headers or {})


def _header(headers: dict[str, str], name: str) -> str | None:
    for k, v in (headers or {}).items():
        if k.lower() == name:
            return v
    return None


def _detail(raw: bytes) -> str:
    """Kurzer, ungefährlicher Fehlertext aus der API-Antwort (nie Header, nie Schlüssel)."""
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, AttributeError):
        return "(keine lesbare Fehlermeldung)"
    detail = data.get("detail", data) if isinstance(data, dict) else data
    if isinstance(detail, dict):
        text = f"{detail.get('status', '')}: {detail.get('message', '')}".strip(": ")
    else:
        text = json.dumps(detail, ensure_ascii=False)
    return text[:300] or "(leer)"


class ElevenLabsClient:
    def __init__(self, api_key: str, guard: SpendGuard, transport: Transport = urllib_transport,
                 base_url: str = BASE_URL, sleep: Callable[[float], None] = time.sleep):
        if not api_key:
            raise ElevenLabsError("Kein API-Schlüssel (ELEVENLABS_API_KEY) gesetzt")
        if not isinstance(guard, SpendGuard) or guard.provider != "elevenlabs":
            raise BudgetError("ElevenLabsClient benötigt einen SpendGuard(provider='elevenlabs')")
        if not base_url.startswith("https://"):
            raise ElevenLabsError("Nur HTTPS erlaubt")
        self._key = api_key
        self.guard = guard
        self.transport = transport
        self.base_url = base_url.rstrip("/")
        self.sleep = sleep

    def __repr__(self) -> str:  # niemals den Schlüssel zeigen
        return f"ElevenLabsClient(base_url={self.base_url!r}, key=***)"

    def _headers(self, accept: str) -> dict[str, str]:
        return {"xi-api-key": self._key, "Content-Type": "application/json", "Accept": accept}

    # Kostenlose Abfragen ---------------------------------------------
    def _get_json(self, path: str) -> tuple[int, Any]:
        try:
            status, raw, _ = self.transport("GET", f"{self.base_url}{path}", self._headers("application/json"), None)
        except NETWORK_ERRORS:
            return 0, None
        try:
            return status, json.loads(raw.decode("utf-8")) if raw else None
        except (ValueError, UnicodeDecodeError):
            return status, None

    def subscription(self) -> dict | None:
        """GET /v1/user/subscription – Tarif, Verbrauch, Overage-Einstellung. Kostenlos."""
        status, data = self._get_json(SUBSCRIPTION_PATH)
        if status == 200 and isinstance(data, dict) and "character_count" in data:
            return data
        return None

    def used_credits(self) -> float | None:
        sub = self.subscription()
        return float(sub["character_count"]) if sub else None

    def voice_available(self, voice_id: str) -> bool | None:
        """True/False laut API; None, wenn die Abfrage nicht möglich war."""
        if not VOICE_ID.match(voice_id or ""):
            return False
        status, data = self._get_json(VOICE_PATH.format(voice_id=voice_id))
        if status == 200 and isinstance(data, dict) and data.get("voice_id") == voice_id:
            return True
        if status in (400, 404, 422):
            return False
        return None

    # Kostenpflichtig --------------------------------------------------
    def run_job(self, job, dest: Path) -> Path:
        """Generiert Audio und schreibt es nach dest. Rückgabe: dest."""
        if getattr(job, "provider", None) != "elevenlabs":
            raise ElevenLabsError(f"{job.id}: kein ElevenLabs-Job")
        validate_request(job.kind, job.path, job.payload)   # Spezifikation (kostenlos)
        self.guard.authorize(job.id, job.credits)            # Budget VOR dem Senden
        url = f"{self.base_url}{job.path}?output_format={OUTPUT_FORMAT}"
        body = json.dumps(job.payload).encode()
        audio, headers = self._generate(job, url, body)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(audio)
        cost = _header(headers, "character-cost")
        try:
            reported = float(cost) if cost is not None else None
        except ValueError:
            reported = None
        if reported is not None and reported >= 0:
            self.guard.observe_estimate(job.id, reported)    # deutlich teurer als Tabelle -> Stopp
            self.guard.settle(job.id, reported)
        else:
            self.guard.settle(job.id, None)                  # Schätzung bleibt stehen
        return dest

    def _generate(self, job, url: str, body: bytes) -> tuple[bytes, dict[str, str]]:
        for attempt in range(4):
            try:
                status, raw, headers = self.transport("POST", url, self._headers("audio/mpeg"), body)
            except NETWORK_ERRORS as exc:
                self.guard.mark_uncertain(job.id, f"Netzfehler: {type(exc).__name__}")
                raise UncertainGenerationError(
                    f"{job.id}: Verbindung zu ElevenLabs abgebrochen – ob Credits verbraucht wurden, ist unklar. "
                    "Kosten bleiben verbucht, Lauf stoppt, KEINE Wiederholung."
                ) from None
            if status == 200:
                if len(raw) < 256:
                    self.guard.mark_uncertain(job.id, "HTTP 200 ohne Audiodaten")
                    raise UncertainGenerationError(f"{job.id}: Antwort ohne Audiodaten – Kosten bleiben verbucht")
                return raw, headers
            if status == 429 and attempt < 3:  # Rate-Limit/Parallelitätsgrenze: nichts generiert
                try:
                    wait = float(_header(headers, "retry-after") or 5)
                except ValueError:
                    wait = 5.0
                self.sleep(min(60.0, wait))
                continue
            if status >= 500 or status == 429:
                self.guard.mark_uncertain(job.id, f"HTTP {status}")
                raise UncertainGenerationError(f"{job.id}: HTTP {status} von ElevenLabs – Ergebnis unklar, Stopp")
            # 4xx: abgelehnt (Validierung, Kontingent, Tarif, Stimme) -> nichts generiert, keine Kosten
            self.guard.settle(job.id, 0.0)
            raise ElevenLabsError(f"{job.id}: Anfrage abgelehnt (HTTP {status}): {_detail(raw)}")
        raise ElevenLabsError(f"{job.id}: Rate-Limit dauerhaft überschritten")


# ---------------------------------------------------------------------
# Konto-Prüfung (Preflight, kostenlos)
# ---------------------------------------------------------------------
def check_subscription(sub: dict | None, needed_credits: float, cfg: BudgetConfig,
                       needs_music: bool = False) -> dict[str, Any]:
    """Prüft Tarif, Lizenz, Restkontingent und dass KEINE automatische Nachberechnung aktiv ist.

    Gibt eine Zusammenfassung zurück oder wirft BudgetError/ApprovalError (fail-closed).
    """
    if not sub:
        raise BudgetError("ElevenLabs-Konto nicht abrufbar (Schlüssel/Netz/Berechtigung 'user_read' prüfen) – Abbruch")
    try:
        tier = str(sub["tier"]).lower()
        status = str(sub.get("status", "")).lower()
        used = float(sub["character_count"])
        limit = float(sub["character_limit"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BudgetError("ElevenLabs-Kontodaten unvollständig – Abbruch (fail-closed)") from exc
    remaining = limit - used

    # 1) Keine automatischen Guthabenkäufe: Overage / nutzungsbasierte Abrechnung muss AUS sein.
    ext = sub.get("max_credit_limit_extension", sub.get("max_character_limit_extension", 0))
    if ext not in (0, None, "0"):
        raise ApprovalError(
            "ElevenLabs: nutzungsbasierte Abrechnung (Overage) ist aktiv "
            f"(max_credit_limit_extension = {ext}). Das wäre ein automatischer Guthabenkauf – "
            "bitte in den ElevenLabs-Abrechnungseinstellungen deaktivieren. Kein bezahlter Lauf."
        )
    overage = (sub.get("current_overage") or {}).get("amount", "0")
    try:
        overage_amount = float(overage)
    except (TypeError, ValueError):
        overage_amount = 1.0
    if overage_amount > 0:
        raise ApprovalError("ElevenLabs: Es laufen bereits Overage-Kosten auf – kein bezahlter Lauf.")

    # 2) Tarif und Lizenz
    is_free = tier in FREE_TIERS or status in FREE_STATUSES
    if is_free and cfg.el_require_commercial_license:
        raise ApprovalError(
            "ElevenLabs-Konto ist im Free-Tarif: keine kommerzielle Lizenz (Namensnennung nötig, keine "
            "Monetarisierung). Ein Abo schließt die Pipeline nicht ab – das entscheidet die Produktion. "
            "Für rein private Tests: config/budget.yaml elevenlabs.require_commercial_license: false (per PR)."
        )
    if not is_free and status and status not in OK_STATUSES:
        raise BudgetError(f"ElevenLabs-Abo hat den Status '{status}' (z. B. offene Rechnung) – Abbruch")
    if needs_music and is_free:
        raise ApprovalError("Eleven Music ist nur in bezahlten Tarifen verfügbar – Musik-Job nicht möglich.")

    # 3) Kontingent reicht inkl. Sicherheitsaufschlag
    wanted = needed_credits * (1.0 + cfg.safety_margin)
    if remaining < wanted:
        raise BudgetError(
            f"ElevenLabs-Kontingent reicht nicht: verfügbar {remaining:.0f} Credits, benötigt "
            f"{wanted:.0f} (inkl. {cfg.safety_margin:.0%} Puffer). Es wird NICHT automatisch nachgekauft."
        )
    return {"tier": tier, "status": status or "?", "used": used, "limit": limit, "remaining": remaining,
            "commercial_license": not is_free, "has_open_invoices": bool(sub.get("has_open_invoices"))}
