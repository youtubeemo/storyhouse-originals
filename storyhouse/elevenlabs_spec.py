"""Verbindliche Request-Spezifikation der genutzten ElevenLabs-Endpunkte.

Quelle (abgeglichen am 14.11.2026, Details in docs/API_ABGLEICH.md):
  * elevenlabs.io/docs/api-reference/text-to-speech/convert
  * elevenlabs.io/docs/api-reference/text-to-sound-effects/convert
  * elevenlabs.io/docs/api-reference/music/compose
  * elevenlabs.io/docs/api-reference/user/subscription/get
  * elevenlabs.io/docs/overview/models

Jeder Job wird VOR dem Absenden gegen diese Tabelle geprüft. Bewusst enger als
die API: Es sind nur Modelle erlaubt, für die ein Preis hinterlegt ist, und nur
Parameter, deren Kosten bekannt sind (z. B. SFX immer mit fester Dauer).
"""
from __future__ import annotations

import re
from typing import Any

from storyhouse.runway_spec import SpecError

SPEC_SOURCE = "elevenlabs.io/docs/api-reference (2026-11-14)"
BASE_URL = "https://api.elevenlabs.io"

# mp3_44100_128 ist in allen Tarifen erlaubt (192 kbps erst ab Creator, PCM/WAV 44,1 kHz ab Pro).
OUTPUT_FORMAT = "mp3_44100_128"

TTS_PATH = "/v1/text-to-speech/{voice_id}"
SFX_PATH = "/v1/sound-generation"
MUSIC_PATH = "/v1/music"
SUBSCRIPTION_PATH = "/v1/user/subscription"   # GET, kostenlos
VOICE_PATH = "/v1/voices/{voice_id}"          # GET, kostenlos

VOICE_ID = re.compile(r"^[A-Za-z0-9]{20}$")

TTS_MODELS: dict[str, dict[str, Any]] = {
    # eleven_v3: 5.000 Zeichen, Regie-Tags ([whispers]), language_code wird unterstützt.
    # stability: v3 kennt nur drei Stufen (Creative/Natural/Robust).
    "eleven_v3": {"max_chars": 5000, "audio_tags": True, "language_code": True, "stability": {0.0, 0.5, 1.0}},
    # multilingual_v2: 10.000 Zeichen, KEINE Tags, language_code laut Doku nicht unterstützt.
    "eleven_multilingual_v2": {"max_chars": 10000, "audio_tags": False, "language_code": False, "stability": None},
}
SFX_MODELS: dict[str, dict[str, Any]] = {
    # duration_seconds 0,5–30; loop nur bei v2. (eleven_text_to_sound_v3 existiert, ist aber nicht bepreist.)
    "eleven_text_to_sound_v2": {"min_s": 0.5, "max_s": 30.0, "loop": True},
}
MUSIC_MODELS: dict[str, dict[str, Any]] = {
    # music_length_ms 3.000–600.000 (nur mit prompt); seed ist mit prompt NICHT erlaubt.
    "music_v1": {"min_ms": 3000, "max_ms": 600000},
}
MUSIC_PROMPT_MAX = 2000  # eigene Obergrenze; die API nennt keine

TTS_FIELDS = {"text", "model_id", "language_code", "voice_settings", "seed"}
SFX_FIELDS = {"text", "model_id", "duration_seconds", "loop", "prompt_influence"}
MUSIC_FIELDS = {"prompt", "model_id", "music_length_ms", "force_instrumental"}


def tts_path(voice_id: str) -> str:
    if not VOICE_ID.match(voice_id or ""):
        raise SpecError(f"voice_id {voice_id!r} ist keine gültige ElevenLabs-Voice-ID (20 Zeichen A-Z/a-z/0-9)")
    return TTS_PATH.format(voice_id=voice_id)


def validate_request(kind: str, path: str | None, body: dict[str, Any]) -> None:
    """Wirft SpecError, wenn Pfad oder Body nicht der API entsprechen."""
    model = body.get("model_id")
    errors: list[str] = []

    if kind == "tts":
        spec = TTS_MODELS.get(model)
        if not spec:
            raise SpecError(f"ElevenLabs-TTS-Modell {model!r} nicht freigegeben")
        m = re.fullmatch(r"/v1/text-to-speech/([^/?#]+)", path or "")
        if not m or not VOICE_ID.match(m.group(1)):
            errors.append(f"Pfad {path!r} enthält keine gültige voice_id")
        extra = set(body) - TTS_FIELDS
        if extra:
            errors.append(f"unbekannte Felder {sorted(extra)}")
        text = body.get("text", "")
        if not isinstance(text, str) or not text.strip():
            errors.append("text fehlt")
        elif len(text) > spec["max_chars"]:
            errors.append(f"text > {spec['max_chars']} Zeichen")
        if isinstance(text, str) and "[" in text and not spec["audio_tags"]:
            errors.append("Regie-Tags wie [whispers] nur mit eleven_v3")
        if body.get("language_code") is not None:
            if not spec["language_code"]:
                errors.append(f"{model} unterstützt language_code nicht")
            elif not re.fullmatch(r"[a-z]{2}", str(body["language_code"])):
                errors.append("language_code muss ISO 639-1 sein (z. B. en)")
        vs = body.get("voice_settings") or {}
        if not isinstance(vs, dict) or set(vs) - {"stability", "similarity_boost", "style", "speed",
                                                  "use_speaker_boost"}:
            errors.append("voice_settings enthält unbekannte Felder")
        elif "stability" in vs:
            allowed = spec["stability"]
            st = vs["stability"]
            if not isinstance(st, (int, float)) or not 0.0 <= float(st) <= 1.0:
                errors.append("voice_settings.stability muss 0–1 sein")
            elif allowed is not None and float(st) not in allowed:
                errors.append(f"{model}: stability nur {sorted(allowed)}")
        seed = body.get("seed")
        if seed is not None and not (isinstance(seed, int) and 0 <= seed <= 4_294_967_295):
            errors.append("seed muss 0–4294967295 sein")

    elif kind == "sfx":
        spec = SFX_MODELS.get(model)
        if not spec:
            raise SpecError(f"ElevenLabs-SFX-Modell {model!r} nicht freigegeben")
        if path != SFX_PATH:
            errors.append(f"Pfad muss {SFX_PATH} sein")
        extra = set(body) - SFX_FIELDS
        if extra:
            errors.append(f"unbekannte Felder {sorted(extra)}")
        if not str(body.get("text", "")).strip():
            errors.append("text fehlt")
        dur = body.get("duration_seconds")
        if dur is None or not spec["min_s"] <= float(dur) <= spec["max_s"]:
            errors.append(f"duration_seconds muss {spec['min_s']}–{spec['max_s']} s sein "
                          "(ohne Angabe sind Länge und Kosten unbestimmt)")
        if body.get("loop") and not spec["loop"]:
            errors.append(f"{model} unterstützt loop nicht")
        pi = body.get("prompt_influence")
        if pi is not None and not 0.0 <= float(pi) <= 1.0:
            errors.append("prompt_influence muss 0–1 sein")

    elif kind == "music":
        spec = MUSIC_MODELS.get(model)
        if not spec:
            raise SpecError(f"ElevenLabs-Musikmodell {model!r} nicht freigegeben")
        if path != MUSIC_PATH:
            errors.append(f"Pfad muss {MUSIC_PATH} sein")
        extra = set(body) - MUSIC_FIELDS
        if extra:
            errors.append(f"unbekannte Felder {sorted(extra)} (seed ist zusammen mit prompt nicht erlaubt)")
        prompt = str(body.get("prompt", "")).strip()
        if not prompt or len(prompt) > MUSIC_PROMPT_MAX:
            errors.append(f"prompt fehlt oder > {MUSIC_PROMPT_MAX} Zeichen")
        ms = body.get("music_length_ms")
        if not isinstance(ms, int) or not spec["min_ms"] <= ms <= spec["max_ms"]:
            errors.append(f"music_length_ms muss {spec['min_ms']}–{spec['max_ms']} sein "
                          "(ohne Angabe wählt das Modell die Länge – Kosten unbestimmt)")
        if body.get("force_instrumental") is not True:
            errors.append("force_instrumental muss true sein (Score ohne Gesang)")
    else:
        raise SpecError(f"Unbekannte ElevenLabs-Jobart {kind}")

    if errors:
        raise SpecError(f"elevenlabs/{kind}/{model}: " + "; ".join(errors))
