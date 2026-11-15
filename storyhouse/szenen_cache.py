"""Zwischenergebnisse wiederverwenden: bezahlte Bild-/Video-/Tonaufträge nur einmal ausführen.

Jeder Auftrag (Job) bekommt einen Schlüssel aus Art, Modell, Anfrage und – rekursiv – den Schlüsseln der
Aufträge, von denen er abhängt. Ändert sich ein Prompt oder ein vorgelagertes Bild, ändert sich der
Schlüssel und der Auftrag wird neu erzeugt; sonst liegt das Ergebnis im Cache und kostet nichts.

So lässt sich nach einem Fehler nur die fehlgeschlagene Szene erneut rendern: alle schon bezahlten
Ergebnisse werden aus dem Cache genommen. Der Cache enthält bezahltes Material – im Workflow nur
verschlüsselt sichern (wie die übrigen Ergebnisse).
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Iterable

INDEX = "index.json"


def job_keys(jobs: Iterable) -> dict[str, str]:
    """Schlüssel je Job-ID (abhängigkeitsbewusst)."""
    by_id = {j.id: j for j in jobs}
    keys: dict[str, str] = {}

    def key(jid: str, stack: tuple[str, ...] = ()) -> str:
        if jid in keys:
            return keys[jid]
        if jid in stack:
            raise ValueError(f"Zyklische Abhängigkeit bei {jid}")
        j = by_id[jid]
        deps = [key(d, stack + (jid,)) for d in sorted(j.depends) if d in by_id]
        raw = json.dumps({"kind": j.kind, "model": j.model, "provider": getattr(j, "provider", "runway"),
                          "path": getattr(j, "path", None), "payload": j.payload, "deps": deps},
                         sort_keys=True, ensure_ascii=False, default=str)
        keys[jid] = hashlib.sha256(raw.encode()).hexdigest()[:20]
        return keys[jid]

    for jid in by_id:
        key(jid)
    return keys


class SzenenCache:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        p = self.root / INDEX
        self.index: dict[str, str] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def _save(self) -> None:
        (self.root / INDEX).write_text(json.dumps(self.index, indent=1, sort_keys=True), encoding="utf-8")

    def get(self, key: str) -> Path | None:
        name = self.index.get(key)
        if not name:
            return None
        p = self.root / name
        return p if p.is_file() and p.stat().st_size > 0 else None

    def put(self, key: str, src: Path) -> Path:
        src = Path(src)
        if not src.is_file() or src.stat().st_size == 0:
            raise ValueError(f"Leeres Ergebnis wird nicht zwischengespeichert: {src}")
        name = f"{key}{src.suffix}"
        dst = self.root / name
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        self.index[key] = name
        self._save()
        return dst

    def coverage(self, jobs: Iterable) -> tuple[list[str], list[str]]:
        """(im Cache, noch zu erzeugen) – für Kostenschätzung und Bericht vor dem Lauf."""
        jobs = list(jobs)
        keys = job_keys(jobs)
        hit = [j.id for j in jobs if self.get(keys[j.id])]
        return hit, [j.id for j in jobs if j.id not in hit]
