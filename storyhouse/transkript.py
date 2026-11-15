"""Spracherkennung mit faster-whisper (kostenlos, offline) – OPTIONAL.

Wofür: prüfen, ob die erzeugte Sprache (ElevenLabs/Piper) wirklich den Drehbuchtext sagt, und wortgenaue
Zeitstempel für Untertitel liefern. faster-whisper (MIT, CTranslate2) ist nicht in requirements.txt, damit
CI und Workflows schnell bleiben; Installation bei Bedarf: ``pip install faster-whisper`` (lädt beim ersten
Aufruf das Whisper-Modell, z. B. ``small`` ≈ 480 MB, CPU reicht).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Wort:
    text: str
    start: float
    ende: float


def verfuegbar() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def transkribieren(audio: str | Path, modell: str = "small", sprache: str = "de", _model=None) -> list[Wort]:
    """Wörter mit Zeitstempeln. ``_model`` erlaubt eine Attrappe in Tests."""
    if _model is None:
        if not verfuegbar():
            raise RuntimeError("faster-whisper ist nicht installiert (pip install faster-whisper)")
        from faster_whisper import WhisperModel
        _model = WhisperModel(modell, device="cpu", compute_type="int8")
    segmente, _info = _model.transcribe(str(audio), language=sprache, word_timestamps=True, vad_filter=True)
    return [Wort(w.word.strip(), float(w.start), float(w.end)) for s in segmente for w in (s.words or [])]


def _norm(text: str) -> list[str]:
    t = unicodedata.normalize("NFKC", text.lower())
    return [w for w in re.split(r"[^\wäöüß]+", t) if w]


def wortfehlerrate(referenz: str, erkannt: str) -> float:
    """Word Error Rate (Levenshtein auf Wortebene), 0 = identisch."""
    r, h = _norm(referenz), _norm(erkannt)
    if not r:
        return 0.0 if not h else 1.0
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
            prev, d[j] = d[j], cur
    return round(d[len(h)] / len(r), 3)


def dialog_pruefen(referenz: str, woerter: list[Wort], max_wer: float = 0.25) -> tuple[bool, str]:
    wer = wortfehlerrate(referenz, " ".join(w.text for w in woerter))
    return wer <= max_wer, f"Wortfehlerrate {wer:.0%} (Grenze {max_wer:.0%})"
