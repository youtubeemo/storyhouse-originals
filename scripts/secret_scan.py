#!/usr/bin/env python3
"""Sucht versehentlich eingecheckte Zugangsdaten in Repo-Dateien und Protokollen – gibt NIE den Wert aus.

Gemeinsame Datei (identische Kopie in allen drei Repos, keine Abhängigkeit zwischen ihnen).

    python scripts/secret_scan.py                 # alle von git verwalteten Dateien
    python scripts/secret_scan.py logs/*.log      # bestimmte Dateien (z. B. heruntergeladene Protokolle)

Ausnahmen: eine Zeile mit dem Kommentar ``secret-scan: ignore`` wird übersprungen (nur für Beispiele/Tests).
Exit-Code 1, wenn etwas gefunden wurde. Treffer werden maskiert (Typ, Datei, Zeile – nie der Schlüssel).
"""
from __future__ import annotations

import math
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

VERSION = "1.0.0"

MUSTER: dict[str, re.Pattern] = {
    "GitHub-Token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{60,}\b"),
    "Anthropic-Schlüssel": re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
    "OpenAI-Schlüssel": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b"),
    "ElevenLabs-Schlüssel": re.compile(r"\bsk_[a-f0-9]{40,}\b"),
    "Google-API-Schlüssel": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "Google-OAuth-Refresh-Token": re.compile(r"\b1//0[0-9A-Za-z_\-]{40,}"),
    "Google-OAuth-Client-Secret": re.compile(r"\bGOCSPX-[0-9A-Za-z_\-]{20,}"),
    "AWS-Zugangsschlüssel": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "Privater Schlüssel": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY( BLOCK)?-----"),
    "Slack-Token": re.compile(r"\bxox[abprs]-[0-9A-Za-z\-]{10,}"),
    "Runway-/MiniMax-/Hedra-Schlüssel (Zuweisung)": re.compile(
        r"(?i)\b(?:RUNWAY|RUNWAYML|MINIMAX|HEDRA|GEMINI|ELEVENLABS|TIKTOK)_?(?:API_)?(?:KEY|SECRET|TOKEN)\s*[:=]\s*['\"]?"
        r"(?!\$\{\{|\$\(|\$[A-Z_]|<|your|dein|xxx|\*\*\*|os\.environ|None|null|''|\"\")[A-Za-z0-9_\-\.]{16,}"),
    "Passphrase im Klartext": re.compile(
        r"(?i)\b(?:ARTIFACT_PASSPHRASE|PASSPHRASE|PASSWORD|PASSWORT)\s*[:=]\s*['\"](?!\$\{\{)[^'\"\s]{8,}['\"]"),
}
IGNORIEREN = "secret-scan: ignore"
BINAER = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mov", ".mp3", ".wav", ".m4a", ".ogg", ".onnx", ".ttf",
          ".otf", ".woff", ".woff2", ".zip", ".gz", ".xz", ".pdf", ".blend", ".ico", ".enc", ".bin", ".pt"}
MAX_BYTES = 2_000_000


def entropie(s: str) -> float:
    c = Counter(s)
    return -sum(n / len(s) * math.log2(n / len(s)) for n in c.values()) if s else 0.0


def dateien(root: Path) -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout
        return [root / p for p in out.decode().split("\0") if p]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return [p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]


def scan_text(text: str, name: str) -> list[dict]:
    funde = []
    for nr, zeile in enumerate(text.splitlines(), 1):
        if IGNORIEREN in zeile:
            continue
        for typ, rx in MUSTER.items():
            m = rx.search(zeile)
            if not m:
                continue
            wert = m.group(0)
            # Platzhalter wie sk-xxxxxxxx… oder sich wiederholende Zeichen sind keine echten Schlüssel
            if typ != "Privater Schlüssel" and entropie(wert) < 3.0:
                continue
            funde.append({"datei": name, "zeile": nr, "typ": typ,
                          "maskiert": wert[:4] + "…" + f"({len(wert)} Zeichen)"})
    return funde


def scan(pfade: list[Path], root: Path) -> list[dict]:
    funde = []
    for p in pfade:
        if p.suffix.lower() in BINAER or not p.is_file() or p.stat().st_size > MAX_BYTES:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        name = str(p.relative_to(root)) if p.is_relative_to(root) else str(p)
        funde += scan_text(text, name)
    return funde


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path.cwd()
    pfade = [Path(a).resolve() for a in argv] if argv else dateien(root)
    funde = scan(pfade, root)
    for f in funde:
        print(f"❌ {f['typ']}: {f['datei']}:{f['zeile']} ({f['maskiert']})")
    print(f"{'❌' if funde else '✅'} Geheimnis-Prüfung: {len(funde)} Treffer in {len(pfade)} Dateien")
    return 1 if funde else 0


if __name__ == "__main__":
    sys.exit(main())
