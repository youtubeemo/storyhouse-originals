"""Minimaler Runway-API-Client (nur Standardbibliothek).

Sicherheitsprinzipien:
  * Der API-Schlüssel kommt ausschließlich aus der Umgebung und erscheint nie
    in Logs oder repr().
  * Jeder kostenpflichtige Aufruf läuft durch den SpendGuard – vorher
    (Reservierung), direkt nach der Anlage (Runways estimatedCost) und am Ende
    (Runways cost.credits).
  * Task-Anlagen werden NIE blind wiederholt: Die API kennt keinen
    Idempotency-Key. Bei Verbindungsabbruch/Zeitüberschreitung/5xx ist unklar,
    ob ein Task entstand -> Kosten bleiben verbucht, der Lauf stoppt.
    Einzige Ausnahme: HTTP 429 (Rate-Limit, Task wurde sicher nicht angelegt).
  * Gescheiterte Tasks werden nicht automatisch neu eingereicht.
  * Statusabfragen (GET, kostenlos) werden bei Netzfehlern wiederholt.
"""
from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from storyhouse.budget import BudgetError, SpendGuard
from storyhouse.plan import Job
from storyhouse.runway_spec import validate_payload

BASE_URL = "https://api.dev.runwayml.com"
API_VERSION = "2024-11-06"
TERMINAL_OK = {"SUCCEEDED"}
TERMINAL_FAIL = {"FAILED", "CANCELLED"}
PLACEHOLDER = re.compile(r"\{\{out:([A-Za-z0-9_]+)\}\}")
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError)

# transport(method, url, headers, body_bytes|None) -> (status_code, parsed_json|None, headers)
Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, Any, dict[str, str]]]


class RunwayError(RuntimeError):
    pass


class UncertainSubmissionError(RunwayError):
    """Unklar, ob Runway einen (kostenpflichtigen) Task angelegt hat."""


def urllib_transport(method: str, url: str, headers: dict[str, str], body: bytes | None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 (feste HTTPS-URL)
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None), dict(resp.headers)
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = {"raw": raw[:500].decode(errors="replace")}
        return err.code, parsed, dict(err.headers or {})


def resolve_placeholders(value: Any, outputs: dict[str, str]) -> Any:
    if isinstance(value, str):
        def sub(m: re.Match) -> str:
            key = m.group(1)
            if key not in outputs:
                raise RunwayError(f"Abhängigkeit {key} hat kein Ergebnis")
            return outputs[key]
        return PLACEHOLDER.sub(sub, value)
    if isinstance(value, list):
        return [resolve_placeholders(v, outputs) for v in value]
    if isinstance(value, dict):
        return {k: resolve_placeholders(v, outputs) for k, v in value.items()}
    return value


def _credits(obj: Any, key: str) -> float | None:
    if isinstance(obj, dict) and isinstance(obj.get(key), dict) and obj[key].get("credits") is not None:
        return float(obj[key]["credits"])
    return None


class RunwayClient:
    def __init__(self, api_secret: str, guard: SpendGuard, transport: Transport = urllib_transport,
                 base_url: str = BASE_URL, poll_interval: float = 5.0, timeout_s: float = 900.0,
                 sleep: Callable[[float], None] = time.sleep, max_poll_errors: int = 6):
        if not api_secret:
            raise RunwayError("Kein API-Schlüssel (RUNWAYML_API_SECRET) gesetzt")
        if not isinstance(guard, SpendGuard):
            raise BudgetError("RunwayClient benötigt einen SpendGuard")
        self._secret = api_secret
        self.guard = guard
        self.transport = transport
        self.base_url = base_url.rstrip("/")
        self.poll_interval = poll_interval
        self.timeout_s = timeout_s
        self.sleep = sleep
        self.max_poll_errors = max_poll_errors
        self.task_ids: dict[str, str] = {}

    def __repr__(self) -> str:  # niemals den Schlüssel zeigen
        return f"RunwayClient(base_url={self.base_url!r}, secret=***)"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._secret}",
            "X-Runway-Version": API_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, payload: dict | None = None):
        body = json.dumps(payload).encode() if payload is not None else None
        return self.transport(method, f"{self.base_url}{path}", self._headers(), body)

    # Kostenlose Abfragen ---------------------------------------------
    def credit_balance(self) -> float | None:
        try:
            status, data, _ = self._request("GET", "/v1/organization")
        except NETWORK_ERRORS:
            return None
        if status == 200 and isinstance(data, dict) and "creditBalance" in data:
            return float(data["creditBalance"])
        return None

    def cancel(self, task_id: str) -> None:
        """Storniert laufende/wartende Tasks (Erstattung nur, wenn Runway sie noch nicht startete)."""
        try:
            self._request("DELETE", f"/v1/tasks/{task_id}")
        except NETWORK_ERRORS:
            pass

    # Kostenpflichtig --------------------------------------------------
    def run_job(self, job: Job, outputs: dict[str, str]) -> str:
        payload = resolve_placeholders(job.payload, outputs)
        validate_payload(job.kind, payload)               # Spezifikation (kostenlos)
        self.guard.authorize(job.id, job.credits)         # Budget VOR dem Senden
        task_id, api_estimate = self._create(job, payload)
        self.task_ids[job.id] = task_id
        try:
            self.guard.observe_estimate(job.id, api_estimate)  # Preisabweichung?
        except BudgetError:
            self.cancel(task_id)
            raise
        return self._wait(job, task_id)

    def _create(self, job: Job, payload: dict) -> tuple[str, float | None]:
        for attempt in range(4):
            try:
                status, data, headers = self._request("POST", job.endpoint, payload)
            except NETWORK_ERRORS as exc:
                self.guard.mark_uncertain(job.id, f"Netzfehler bei Anlage: {exc}")
                raise UncertainSubmissionError(
                    f"{job.id}: Verbindung bei Task-Anlage abgebrochen – ob ein Task entstand, ist unklar. "
                    "Kosten bleiben verbucht, Lauf stoppt, KEINE Wiederholung."
                ) from exc
            if status in (200, 201) and isinstance(data, dict) and data.get("id"):
                return str(data["id"]), _credits(data, "estimatedCost")
            if status == 429 and attempt < 3:  # Rate-Limit: Task wurde nicht angelegt
                self.sleep(float(headers.get("Retry-After", 5)))
                continue
            if status >= 500 or status == 429:
                self.guard.mark_uncertain(job.id, f"HTTP {status}")
                raise UncertainSubmissionError(f"{job.id}: HTTP {status} bei Anlage – Ergebnis unklar, Stopp")
            # 4xx: Anfrage abgelehnt, es entstand kein Task -> keine Kosten
            self.guard.settle(job.id, 0.0)
            raise RunwayError(f"{job.id}: Anfrage abgelehnt (HTTP {status}): {_short(data)}")
        raise RunwayError(f"{job.id}: Rate-Limit dauerhaft überschritten")

    def _wait(self, job: Job, task_id: str) -> str:
        waited, errors = 0.0, 0
        while waited <= self.timeout_s:
            try:
                status, data, _ = self._request("GET", f"/v1/tasks/{task_id}")
            except NETWORK_ERRORS:
                status, data = 0, None
            if status != 200 or not isinstance(data, dict):
                errors += 1
                if errors > self.max_poll_errors:
                    raise RunwayError(f"{job.id}: Task {task_id} nicht abfragbar (HTTP {status}) – "
                                      "Kosten bleiben verbucht")
                self.sleep(min(60.0, self.poll_interval * 2 ** errors))
                continue
            errors = 0
            state = data.get("status")
            if state in TERMINAL_OK:
                self.guard.settle(job.id, _credits(data, "cost"))
                out = data.get("output") or []
                if not out:
                    raise RunwayError(f"{job.id}: Task {task_id} ohne Output")
                return str(out[0])
            if state in TERMINAL_FAIL:
                self.guard.settle(job.id, _credits(data, "cost"))
                raise RunwayError(
                    f"{job.id}: Task {task_id} {state}: {data.get('failure')} ({data.get('failureCode')}) "
                    "– wird NICHT automatisch neu eingereicht"
                )
            self.sleep(self.poll_interval)
            waited += self.poll_interval
        self.cancel(task_id)
        raise RunwayError(f"{job.id}: Zeitüberschreitung für Task {task_id} – storniert, Kosten bleiben verbucht")


def download(url: str, dest: Path) -> Path:
    if not url.startswith("https://"):
        raise RunwayError("Nur HTTPS-Downloads erlaubt")
    dest.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for attempt in range(3):  # Download ist kostenlos -> Wiederholung erlaubt
        try:
            with urllib.request.urlopen(url, timeout=300) as resp, dest.open("wb") as fh:  # noqa: S310
                while chunk := resp.read(1 << 20):
                    fh.write(chunk)
            return dest
        except NETWORK_ERRORS as exc:
            last = exc
            time.sleep(2 ** attempt)
    raise RunwayError(f"Download fehlgeschlagen: {last}")


def _short(data: Any) -> str:
    text = json.dumps(data) if not isinstance(data, str) else data
    return text[:300]
