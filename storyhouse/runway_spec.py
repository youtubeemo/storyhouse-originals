"""Verbindliche Request-Spezifikation der genutzten Runway-Endpunkte.

Quelle: offizielles Runway Python SDK (aus der OpenAPI-Spezifikation generiert),
github.com/runwayml/sdk-python, Commit 949ac4e (12.11.2026), Dateien
``src/runwayml/types/*_create_params.py``, sowie docs.dev.runwayml.com.

Jeder Job wird VOR dem Absenden gegen diese Tabelle geprüft. Ein Payload,
den die API ablehnen würde, verlässt die Pipeline nie.
"""
from __future__ import annotations

import re
from typing import Any

SPEC_SOURCE = "runwayml/sdk-python@949ac4e (2026-11-12) + docs.dev.runwayml.com/guides/pricing"

GEN4_VIDEO_RATIOS = {"1280:720", "720:1280", "1104:832", "960:960", "832:1104", "1584:672"}
VEO_RATIOS = {"1280:720", "720:1280", "1080:1920", "1920:1080"}
IMAGE_RATIOS = {
    "1024:1024", "1080:1080", "1168:880", "1360:768", "1440:1080", "1080:1440", "1808:768",
    "1920:1080", "1080:1920", "2112:912", "1280:720", "720:1280", "720:720", "960:720",
    "720:960", "1680:720",
}
TTS_PRESETS = {
    "Maya", "Arjun", "Serene", "Bernard", "Billy", "Mark", "Clint", "Mabel", "Chad", "Leslie",
    "Eleanor", "Elias", "Elliot", "Grungle", "Brodie", "Sandra", "Kirk", "Kylie", "Lara", "Lisa",
    "Malachi", "Marlene", "Martin", "Miriam", "Monster", "Paula", "Pip", "Rusty", "Ragnar", "Xylar",
    "Maggie", "Jack", "Katie", "Noah", "James", "Rina", "Ella", "Mariah", "Frank", "Claudia",
    "Niki", "Vincent", "Kendrick", "Myrna", "Tom", "Wanda", "Benjamin", "Kiana", "Rachel",
}
REF_TAG = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,15}$")

VIDEO_MODELS: dict[str, dict[str, Any]] = {
    # positions: erlaubte promptImage-Positionen
    "gen4.5":      {"durations": set(range(2, 11)), "ratios": GEN4_VIDEO_RATIOS, "positions": {"first"},
                    "prompt_text_required": True},
    "gen4_turbo":  {"durations": set(range(2, 11)), "ratios": GEN4_VIDEO_RATIOS, "positions": {"first"},
                    "prompt_text_required": False},
    "veo3.1":      {"durations": {4, 6, 8}, "ratios": VEO_RATIOS, "positions": {"first", "last"},
                    "prompt_text_required": False, "has_audio_flag": True},
    "veo3.1_fast": {"durations": {4, 6, 8}, "ratios": VEO_RATIOS, "positions": {"first", "last"},
                    "prompt_text_required": False, "has_audio_flag": True},
}
IMAGE_MODELS: dict[str, dict[str, Any]] = {
    "gen4_image":       {"max_refs": 3, "refs_required": False},
    "gen4_image_turbo": {"max_refs": 3, "refs_required": True},
}
TTS_MODELS = {"eleven_v3": 5000, "eleven_multilingual_v2": 1000}  # max. Zeichen
SFX_MODELS = {"eleven_text_to_sound_v2": (0.5, 30.0)}

ENDPOINTS = {
    "image": "/v1/text_to_image",
    "video": "/v1/image_to_video",
    "tts": "/v1/text_to_speech",
    "sfx": "/v1/sound_effect",
}


class SpecError(ValueError):
    """Payload entspricht nicht der offiziellen API-Spezifikation."""


def supports_last_frame(model: str) -> bool:
    return "last" in VIDEO_MODELS.get(model, {}).get("positions", set())


def snap_duration(model: str, seconds: float) -> int:
    """Kleinste erlaubte Dauer >= seconds (Runway rechnet pro generierter Sekunde)."""
    allowed = sorted(VIDEO_MODELS[model]["durations"])
    for d in allowed:
        if d >= seconds - 1e-9:
            return d
    raise SpecError(f"{model}: keine erlaubte Dauer >= {seconds}s (erlaubt: {allowed})")


def _len16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def validate_payload(kind: str, payload: dict[str, Any]) -> None:
    """Wirft SpecError, wenn der Payload nicht der API entspricht."""
    model = payload.get("model")
    errors: list[str] = []

    if kind == "video":
        spec = VIDEO_MODELS.get(model)
        if not spec:
            raise SpecError(f"Videomodell {model!r} nicht freigegeben")
        if payload.get("ratio") not in spec["ratios"]:
            errors.append(f"ratio {payload.get('ratio')} nicht erlaubt für {model}")
        if payload.get("duration") not in spec["durations"]:
            errors.append(f"duration {payload.get('duration')} nicht erlaubt für {model}")
        pi = payload.get("promptImage")
        if isinstance(pi, list):
            positions = [p.get("position") for p in pi]
            if not set(positions) <= spec["positions"]:
                errors.append(f"{model} unterstützt nur Positionen {sorted(spec['positions'])}, nicht {positions}")
            if "first" not in positions:
                errors.append("promptImage braucht ein 'first'-Bild")
        elif not isinstance(pi, str) or not pi:
            errors.append("promptImage fehlt")
        text = payload.get("promptText", "")
        if spec["prompt_text_required"] and not text:
            errors.append("promptText ist Pflicht")
        if text and _len16(text) > 1000:
            errors.append("promptText > 1000 UTF-16-Einheiten")
        if spec.get("has_audio_flag") and payload.get("audio") is not False:
            errors.append(f"{model}: audio muss explizit false sein (Standard true = teurer)")

    elif kind == "image":
        spec = IMAGE_MODELS.get(model)
        if not spec:
            raise SpecError(f"Bildmodell {model!r} nicht freigegeben")
        if payload.get("ratio") not in IMAGE_RATIOS:
            errors.append(f"ratio {payload.get('ratio')} nicht erlaubt")
        refs = payload.get("referenceImages") or []
        if len(refs) > spec["max_refs"]:
            errors.append(f"max. {spec['max_refs']} Referenzbilder")
        if spec["refs_required"] and not refs:
            errors.append(f"{model} benötigt 1–3 Referenzbilder")
        for r in refs:
            if r.get("tag") and not REF_TAG.match(r["tag"]):
                errors.append(f"Tag {r['tag']!r}: 3–16 Zeichen, Buchstabe zuerst, nur A-Z/0-9/_")
        if not payload.get("promptText") or _len16(payload["promptText"]) > 1000:
            errors.append("promptText fehlt oder > 1000 Zeichen")

    elif kind == "tts":
        if model not in TTS_MODELS:
            raise SpecError(f"TTS-Modell {model!r} nicht freigegeben")
        text = payload.get("promptText", "")
        if not text or _len16(text) > TTS_MODELS[model]:
            errors.append("promptText leer oder zu lang")
        voice = payload.get("voice") or {}
        if voice.get("type") != "runway-preset" or voice.get("presetId") not in TTS_PRESETS:
            errors.append(f"Stimme {voice} ist kein gültiges Runway-Preset")
        if "[" in text and model != "eleven_v3":
            errors.append("Audio-Tags wie [whispers] nur mit eleven_v3")

    elif kind == "sfx":
        if model not in SFX_MODELS:
            raise SpecError(f"SFX-Modell {model!r} nicht freigegeben")
        lo, hi = SFX_MODELS[model]
        dur = payload.get("duration")
        if dur is None or not lo <= float(dur) <= hi:
            errors.append(f"duration muss {lo}–{hi} s sein (ohne Angabe teurer und unbestimmt)")
        if not payload.get("promptText"):
            errors.append("promptText fehlt")
    else:
        raise SpecError(f"Unbekannte Jobart {kind}")

    if errors:
        raise SpecError(f"{kind}/{model}: " + "; ".join(errors))
