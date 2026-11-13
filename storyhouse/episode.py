"""Episode laden und streng validieren."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

EPS = 1e-6
MAX_PROMPT_CHARS = 1000  # Runway-Limit für promptText


class EpisodeError(ValueError):
    """Fehler in der Episodendefinition."""


@dataclass
class Episode:
    path: Path
    data: dict[str, Any]

    # Bequeme Zugriffe -------------------------------------------------
    @property
    def slug(self) -> str:
        return self.data["slug"]

    @property
    def fmt(self) -> dict[str, Any]:
        return self.data["format"]

    @property
    def duration(self) -> float:
        return float(self.fmt["duration_s"])

    @property
    def scenes(self) -> list[dict[str, Any]]:
        return self.data["scenes"]

    @property
    def dialogue(self) -> list[dict[str, Any]]:
        return self.data.get("dialogue", [])

    @property
    def characters(self) -> dict[str, Any]:
        return self.data["characters"]

    @property
    def locations(self) -> dict[str, Any]:
        return self.data.get("locations", {})

    @property
    def sound(self) -> dict[str, Any]:
        return self.data.get("sound_design", {})

    @property
    def subtitle_style(self) -> dict[str, Any]:
        return self.data.get("subtitles", {})

    @property
    def global_style(self) -> str:
        return " ".join(self.data.get("look", {}).get("global_style", "").split())

    def scene(self, scene_id: str) -> dict[str, Any]:
        for s in self.scenes:
            if s["id"] == scene_id:
                return s
        raise EpisodeError(f"Unbekannte Szene: {scene_id}")

    def select_scenes(self, selector: str | None) -> list[dict[str, Any]]:
        """'all' / None / 'S01,S03' / 'S02-S04'."""
        if not selector or selector.strip().lower() == "all":
            return list(self.scenes)
        ids = [s["id"] for s in self.scenes]
        chosen: list[str] = []
        for part in selector.split(","):
            part = part.strip().upper()
            if not part:
                continue
            if "-" in part:
                a, b = (p.strip() for p in part.split("-", 1))
                if a not in ids or b not in ids:
                    raise EpisodeError(f"Ungültiger Szenenbereich: {part}")
                chosen.extend(ids[ids.index(a): ids.index(b) + 1])
            else:
                if part not in ids:
                    raise EpisodeError(f"Unbekannte Szene: {part}")
                chosen.append(part)
        wanted = set(chosen)
        return [self.scene(i) for i in ids if i in wanted]

    def subset(self, selector: str | None) -> "Episode":
        """Teil-Episode mit neu getakteter Zeitachse (für Testläufe einzelner Szenen)."""
        chosen = self.select_scenes(selector)
        if len(chosen) == len(self.scenes):
            return self
        import copy

        data = copy.deepcopy(self.data)
        shift: dict[str, float] = {}
        cursor = 0.0
        new_scenes = []
        for s in chosen:
            s2 = copy.deepcopy(s)
            shift[s["id"]] = cursor - float(s["start"])
            s2["start"] = round(cursor, 3)
            cursor += float(s["duration"])
            new_scenes.append(s2)

        def remap(t: float) -> float | None:
            for s in chosen:
                if float(s["start"]) <= t < float(s["start"]) + float(s["duration"]):
                    return round(t + shift[s["id"]], 3)
            return None

        data["scenes"] = new_scenes
        data["format"]["duration_s"] = round(cursor, 3)
        data["dialogue"] = [
            {**d, "at": remap(float(d["at"]))} for d in self.dialogue if d["scene"] in shift
        ]
        snd = data.get("sound_design", {})
        snd["sfx"] = [
            {**x, "at": remap(float(x["at"]))} for x in snd.get("sfx", []) if remap(float(x["at"])) is not None
        ]
        snd["silence_windows"] = [
            {"from": remap(float(w["from"])), "to": round(remap(float(w["from"])) + float(w["to"]) - float(w["from"]), 3)}
            for w in snd.get("silence_windows", []) if remap(float(w["from"])) is not None
        ]
        return Episode(path=self.path, data=data)

    def ref_tags(self) -> set[str]:
        tags = {c["ref_tag"] for c in self.characters.values() if "ref_tag" in c}
        tags |= {loc["ref_tag"] for loc in self.locations.values() if "ref_tag" in loc}
        return tags

    def reference_sources(self) -> dict[str, str]:
        """ref_tag -> reference_prompt (je Tag genau eine Quelle)."""
        out: dict[str, str] = {}
        for group in (self.characters, self.locations):
            for item in group.values():
                tag, prompt = item.get("ref_tag"), item.get("reference_prompt")
                if tag and prompt and tag not in out:
                    out[tag] = " ".join(prompt.split())
        return out


def load_episode(path: str | Path) -> Episode:
    p = Path(path)
    if p.is_dir():
        p = p / "episode.yaml"
    with p.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    ep = Episode(path=p, data=data)
    validate_episode(ep)
    return ep


def validate_episode(ep: Episode) -> None:
    errors: list[str] = []
    d = ep.data
    for key in ("slug", "format", "scenes", "characters"):
        if key not in d:
            errors.append(f"Pflichtfeld fehlt: {key}")
    if errors:
        raise EpisodeError("; ".join(errors))

    fmt = ep.fmt
    if (fmt.get("width"), fmt.get("height")) != (1080, 1920):
        errors.append("Format muss 1080x1920 (9:16) sein")

    # Szenen: lückenlos, ohne Überlappung, exakte Gesamtdauer
    cursor = 0.0
    ids: set[str] = set()
    tags = ep.ref_tags()
    sfx_ids = {s["id"] for s in ep.sound.get("sfx", [])}
    for s in ep.scenes:
        sid = s.get("id", "?")
        if sid in ids:
            errors.append(f"{sid}: doppelte Szenen-ID")
        ids.add(sid)
        start, dur = float(s["start"]), float(s["duration"])
        if abs(start - cursor) > EPS:
            errors.append(f"{sid}: Start {start} erwartet {cursor:.3f} (Lücke/Überlappung)")
        if dur <= 0:
            errors.append(f"{sid}: Dauer muss > 0 sein")
        rd = int(s.get("render_duration", 0))
        if not 2 <= rd <= 10:
            errors.append(f"{sid}: render_duration {rd} außerhalb 2–10 s")
        if rd + EPS < math.ceil(dur - EPS):
            errors.append(f"{sid}: render_duration {rd} kürzer als Szenendauer {dur}")
        for k in ("keyframe_prompt", "motion_prompt"):
            text = " ".join(str(s.get(k, "")).split())
            if not text:
                errors.append(f"{sid}: {k} fehlt")
            full = f"{text} {ep.global_style}" if k == "keyframe_prompt" else text
            if len(full) > MAX_PROMPT_CHARS:
                errors.append(f"{sid}: {k} inkl. Look zu lang ({len(full)} > {MAX_PROMPT_CHARS})")
        for tag in _tags_in(s.get("keyframe_prompt", "")):
            if tag not in tags:
                errors.append(f"{sid}: unbekannter Referenz-Tag @{tag}")
        for sfx in s.get("sfx", []) or []:
            if sfx not in sfx_ids:
                errors.append(f"{sid}: SFX '{sfx}' nicht in sound_design.sfx definiert")
        cursor = start + dur
    if abs(cursor - ep.duration) > EPS:
        errors.append(f"Szenen ergeben {cursor:.3f} s, erwartet {ep.duration} s")

    # Dialog: innerhalb der Szene, Figur existiert, keine Überlappung, Hook
    last_at = -1.0
    for line in ep.dialogue:
        lid = line.get("id", "?")
        if line.get("character") not in ep.characters:
            errors.append(f"{lid}: unbekannte Figur {line.get('character')}")
        if line.get("scene") not in ids:
            errors.append(f"{lid}: unbekannte Szene {line.get('scene')}")
            continue
        sc = ep.scene(line["scene"])
        at = float(line["at"])
        if not (sc["start"] - EPS <= at < sc["start"] + sc["duration"]):
            errors.append(f"{lid}: at={at} liegt nicht in {sc['id']}")
        if at <= last_at:
            errors.append(f"{lid}: Dialog nicht chronologisch")
        last_at = at
        if not str(line.get("text", "")).strip():
            errors.append(f"{lid}: text fehlt")
    if ep.dialogue and float(ep.dialogue[0]["at"]) > 1.0:
        errors.append("Hook: erste Dialogzeile muss innerhalb der ersten Sekunde starten")

    for sfx in ep.sound.get("sfx", []):
        if not 0 <= float(sfx["at"]) < ep.duration:
            errors.append(f"SFX {sfx['id']}: at außerhalb des Films")
        if not 0.5 <= float(sfx["duration"]) <= 30:
            errors.append(f"SFX {sfx['id']}: duration muss 0.5–30 s sein")

    if errors:
        raise EpisodeError("Episode ungültig:\n  - " + "\n  - ".join(errors))


def _tags_in(text: str) -> list[str]:
    import re

    return re.findall(r"@([a-z0-9_]+)", text or "")
