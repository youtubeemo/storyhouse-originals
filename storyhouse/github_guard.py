"""GitHub-Schutzprüfungen für Workflows (nur lesende API-Aufrufe, kostenlos).

  visibility  Ist das Repository nachweislich PRIVAT? Nur dann dürfen Filme
              unverschlüsselt als Artefakt hochgeladen werden. Jede Unsicherheit
              (Fehler, Zeitüberschreitung, unerwartete Antwort) gilt als "nicht privat".

  approval    Wurde DIESER Lauf für das Environment "paid-render" nachweislich von
              einer berechtigten Person freigegeben? GitHub erzwingt Required Reviewers
              in privaten Repos nur mit GitHub Enterprise (Free/Pro/Team: nur öffentlich).
              Wird das Repo privat, gibt es keinen Freigabe-Datensatz mehr – dann
              verweigert diese Prüfung jeden bezahlten Render (fail-closed).

Aufruf im Workflow (Token nur für diesen Schritt):
    python -m storyhouse.github_guard visibility --github-output "$GITHUB_OUTPUT"
    python -m storyhouse.github_guard approval --environment paid-render
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
from typing import Any, Callable

API_URL = "https://api.github.com"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError, ValueError)

# transport(url, headers) -> (status, parsed_json | None)
Transport = Callable[[str, dict[str, str]], tuple[int, Any]]


class GuardError(RuntimeError):
    pass


def urllib_transport(url: str, headers: dict[str, str]) -> tuple[int, Any]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (feste HTTPS-URL)
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as err:
        return err.code, None


def _get(path: str, token: str, transport: Transport, api_url: str) -> tuple[int, Any]:
    if not api_url.startswith("https://"):
        raise GuardError("GitHub-API nur über HTTPS")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "storyhouse-guard"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        return transport(f"{api_url.rstrip('/')}{path}", headers)
    except NETWORK_ERRORS:
        return 0, None


def repo_is_private(repo: str, token: str, transport: Transport = urllib_transport,
                    api_url: str = API_URL) -> tuple[bool, str]:
    """(True, Grund) nur wenn die API eindeutig ein privates Repository meldet."""
    if not REPO_RE.match(repo or ""):
        return False, "Repository-Name fehlt oder ist ungültig"
    status, data = _get(f"/repos/{repo}", token, transport, api_url)
    if status != 200 or not isinstance(data, dict):
        return False, f"Sichtbarkeit nicht prüfbar (HTTP {status}) – behandelt als öffentlich"
    if data.get("full_name", "").lower() != repo.lower():
        return False, "API-Antwort passt nicht zum Repository – behandelt als öffentlich"
    if data.get("private") is True and data.get("visibility") == "private":
        return True, "Repository ist privat (GitHub-API)"
    return False, f"Repository ist nicht privat (visibility={data.get('visibility')!r})"


def run_was_approved(repo: str, run_id: str, environment: str, allowed: set[str], token: str,
                     transport: Transport = urllib_transport, api_url: str = API_URL) -> tuple[bool, str]:
    """(True, Grund) nur wenn eine berechtigte Person diesen Lauf für das Environment freigegeben hat."""
    if not REPO_RE.match(repo or "") or not str(run_id).isdigit():
        return False, "Repository oder Lauf-ID ungültig"
    status, data = _get(f"/repos/{repo}/actions/runs/{run_id}/approvals", token, transport, api_url)
    if status != 200 or not isinstance(data, list):
        return False, f"Freigabe-Historie nicht abrufbar (HTTP {status})"
    allowed_l = {a.lower() for a in allowed if a}
    states: list[str] = []
    for review in data:
        if not isinstance(review, dict):
            continue
        envs = {str((e or {}).get("name", "")) for e in review.get("environments") or []}
        if environment not in envs:
            continue
        user = str((review.get("user") or {}).get("login", "")).lower()
        states.append(f"{review.get('state')}:{user}")
        if review.get("state") == "rejected":
            return False, f"Lauf wurde für {environment} abgelehnt"
    approved = [s for s in states if s.startswith("approved:") and s.split(":", 1)[1] in allowed_l]
    if approved:
        return True, f"Freigabe für {environment} durch {approved[-1].split(':', 1)[1]} nachgewiesen"
    return False, (f"Keine nachweisliche Freigabe für {environment} durch {', '.join(sorted(allowed_l)) or '?'}. "
                   "In privaten Repositories setzt GitHub Required Reviewers nur mit GitHub Enterprise durch "
                   "(Free/Pro/Team: nur öffentliche Repos). Bezahlter Render verweigert.")


def _write_output(path: str | None, key: str, value: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")


def _summary(text: str) -> None:
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n\n")


def main(argv: list[str] | None = None, transport: Transport = urllib_transport) -> int:
    ap = argparse.ArgumentParser(prog="storyhouse.github_guard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("visibility", help="Ist das Repository privat? Schreibt private=true|false")
    p.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    p.add_argument("--require-private", action="store_true", help="Exit-Code 3, wenn nicht privat")
    p = sub.add_parser("approval", help="Wurde dieser Lauf nachweislich freigegeben?")
    p.add_argument("--environment", default="paid-render")
    p.add_argument("--allowed", default=os.environ.get("GITHUB_REPOSITORY_OWNER", ""),
                   help="kommagetrennte GitHub-Logins, die freigeben dürfen")
    args = ap.parse_args(argv)

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    api_url = os.environ.get("GITHUB_API_URL", API_URL)

    if args.cmd == "visibility":
        private, reason = repo_is_private(repo, token, transport, api_url)
        _write_output(args.github_output, "private", "true" if private else "false")
        _summary(("🔓 " if private else "🔒 ") + reason
                 + ("" if private else " – Filme werden NUR verschlüsselt hochgeladen."))
        if args.require_private and not private:
            return 3
        return 0

    ok, reason = run_was_approved(repo, os.environ.get("GITHUB_RUN_ID", ""), args.environment,
                                  {a.strip() for a in args.allowed.split(",")}, token, transport, api_url)
    _summary(("✅ " if ok else "❌ **Abbruch:** ") + reason)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
