"""Werkzeug-Katalog (config/providers.yaml) – gleiches Format in storyhouse-originals, gta-shorts-germany, dschungel-freunde."""
from __future__ import annotations

from pathlib import Path

import yaml

STATUS = {"vorhanden", "neu integriert", "vorbereitet", "ungeeignet"}
REQUIRED = ("name", "status", "funktion", "kosten", "lizenz", "schluessel", "naechster_schritt", "geprueft_am")


def load(path: str | Path) -> list[dict]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    tools = data["werkzeuge"]
    errors = []
    for t in tools:
        for k in REQUIRED:
            if k not in t:
                errors.append(f"{t.get('name', '?')}: Feld {k} fehlt")
        if t.get("status") not in STATUS:
            errors.append(f"{t.get('name')}: Status {t.get('status')!r} unbekannt")
        if t.get("kostenpflichtig") and t.get("status") == "neu integriert" and not t.get("freigabe_noetig", True):
            errors.append(f"{t.get('name')}: kostenpflichtig integriert ohne Freigabe")
    if errors:
        raise ValueError("; ".join(errors))
    return tools
