"""Minimaler Runway-API-Client (nur Standardbibliothek).

Sicherheitsprinzipien:
  * Der API-Schlüssel kommt ausschließlich aus der Umgebung
    (RUNWAYML_API_SECRET) und erscheint nie in Logs oder repr().
  * Jeder kostenpflichtige Aufruf läuft durch ``SpendGuard.authorize`` –
    ohne Wächter kann der Client gar nicht erzeugt werden.
  * Gescheiterte Tasks werden NICHT automatisch erneut eingereicht.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from storyhouse.budget import BudgetError, SpendGuard
from storyhouse.plan import Job

BASE_URL = "https://api.dev.runwayml.com"
API_VERSION = "2024-11-06"
TERMINAL_OK = {"SUCCEEDED"}
TERMINAL_FAIL = {"FAILED", "CANCELLED"}
PLACEHOLDER = re.compile(r"\{\{out:([A-Za-z0-9_]+)\}\}")

# transport(method, url, headers, body_bytes|None) -> (status_code, parsed_json|None, headers)
Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, Any, dict[str, str]]]


class RunwayError(RuntimeError):
    pass


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


class RunwayClient:
    def __init__(self, api_secret: str, guard: SpendGuard, transport: Transport = urllib_transport,
                 base_url: str = BASE_URL, poll_interval: float = 5.0, timeout_s: float = 900.0,
                 sleep: Callable[[float], None] = time.sleep):
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
        status, data, _ = self._request("GET", "/v1/organization")
        if status == 200 and isinstance(data, dict) and "creditBalance" in data:
            return float(data["creditBalance"])
        return None

    # Kostenpflichtig --------------------------------------------------
    def run_job(self, job: Job, outputs: dict[str, str]) -> str:
        payload = resolve_placeholders(job.payload, outputs)
        self.guard.authorize(job.id, job.credits)  # wirft BudgetError VOR dem Senden
        task_id = self._create(job, payload)
        return self._wait(job, task_id)

    def _create(self, job: Job, payload: dict) -> str:
        for attempt in range(4):
            status, data, headers = self._request("POST", job.endpoint, payload)
            if status in (200, 201) and isinstance(data, dict) and data.get("id"):
                return str(data["id"])
            if status == 429 and attempt < 3:  # Task wurde nicht angelegt -> warten & erneut
                self.sleep(float(headers.get("Retry-After", 5)))
                continue
            raise RunwayError(f"{job.id}: Anlage fehlgeschlagen (HTTP {status}): {_short(data)}")
        raise RunwayError(f"{job.id}: Rate-Limit dauerhaft überschritten")

    def _wait(self, job: Job, task_id: str) -> str:
        waited = 0.0
        while waited <= self.timeout_s:
            status, data, _ = self._request("GET", f"/v1/tasks/{task_id}")
            if status != 200 or not isinstance(data, dict):
                raise RunwayError(f"{job.id}: Task-Abfrage HTTP {status}: {_short(data)}")
            state = data.get("status")
            if state in TERMINAL_OK:
                out = data.get("output") or []
                if not out:
                    raise RunwayError(f"{job.id}: Task {task_id} ohne Output")
                return str(out[0])
            if state in TERMINAL_FAIL:
                raise RunwayError(
                    f"{job.id}: Task {task_id} {state}: {data.get('failure')} ({data.get('failureCode')}) "
                    "– wird NICHT automatisch neu eingereicht"
                )
            self.sleep(self.poll_interval)
            waited += self.poll_interval
        raise RunwayError(f"{job.id}: Zeitüberschreitung für Task {task_id}")


def download(url: str, dest: Path) -> Path:
    if not url.startswith("https://"):
        raise RunwayError("Nur HTTPS-Downloads erlaubt")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=300) as resp, dest.open("wb") as fh:  # noqa: S310
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
    return dest


def _short(data: Any) -> str:
    text = json.dumps(data) if not isinstance(data, str) else data
    return text[:300]
