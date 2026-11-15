"""Zusätzliche KI-Anbieter – VORBEREITET, nicht freigeschaltet.

Hailuo/MiniMax (Video), Hedra Character-3 (sprechende Figuren, Lippensynchronisation) und Google
Gemini „Nano Banana“ (Bilder, Charakter-Referenzen). Runway und ElevenLabs bleiben die freigegebenen
Anbieter der Pipeline (eigene Clients mit SpendGuard).

Sicherheitsregeln (im Code erzwungen, Test: tests/test_zusatz_anbieter.py):
* ``submit`` ruft NIE das Netz, solange in config/budget.yaml unter ``zusatz_anbieter`` der Anbieter
  nicht ``true`` ist UND der API-Schlüssel nicht als Umgebungsvariable gesetzt ist.
* Hedra-Endpunkte sind zusätzlich gesperrt, bis sie gegen die offizielle Doku geprüft wurden
  (``ENDPUNKTE_GEPRUEFT``).
* Vor jedem Auftrag wird der Preis geschätzt; über ``max_usd`` wird abgebrochen.
* Schlüssel tauchen nie in ``repr``, Logs oder Fehlermeldungen auf.

Preise: Listenpreise laut Anbieter-Websites, geprüft am 15.11.2026 – vor einer Freischaltung erneut prüfen.
Qualität: grobe Einschätzung (1–5) aus öffentlichen Vergleichen, KEIN eigener Test. Vor dem Einsatz für
StoryHouse mit einer bezahlten Probe-Einstellung (nach Freigabe) bestätigen.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

ROOT = Path(__file__).resolve().parent.parent
GEPRUEFT_AM = "2026-11-15"


@dataclass(frozen=True)
class Modell:
    anbieter: str
    name: str
    art: str                       # video | bild | sprechender_kopf
    qualitaet: int                 # 1–5, Einschätzung
    usd_pro_sekunde: float = 0.0   # Video
    usd_pro_bild: float = 0.0      # Bild
    aufloesung: str = ""
    max_sekunden: int = 10
    schluessel: str = ""
    hinweis: str = ""
    kommerziell: str = ""

    def kosten(self, sekunden: float = 0.0, bilder: int = 0) -> float:
        return round(self.usd_pro_sekunde * sekunden + self.usd_pro_bild * bilder, 4)


def _runway_modelle() -> list[Modell]:
    """Runway-Preise aus config/pricing.yaml (eine Quelle, keine Doppelpflege)."""
    p = yaml.safe_load((ROOT / "config" / "pricing.yaml").read_text(encoding="utf-8"))
    usd = float(p.get("usd_per_credit", 0.01))
    q = {"gen4.5": 5, "veo3.1": 5, "veo3.1_fast": 4, "gen4_turbo": 3}
    return [Modell("runway", name, "video", q.get(name, 3), usd_pro_sekunde=e["credits_per_second"] * usd,
                   max_sekunden=10, schluessel="RUNWAY_API_KEY", kommerziell="ja (bezahlter Tarif)",
                   hinweis="freigegebener Anbieter")
            for name, e in (p.get("video") or {}).items() if e.get("verified")]


KATALOG: list[Modell] = [
    # Hailuo-02: Preis je 6-s-Clip laut MiniMax-Preisliste → je Sekunde umgerechnet
    Modell("minimax", "MiniMax-Hailuo-02", "video", 4, usd_pro_sekunde=0.10 / 6, aufloesung="512P", max_sekunden=10,
           schluessel="MINIMAX_API_KEY", kommerziell="laut AGB mit bezahltem API-Konto", hinweis="6-s-Clip 0,10 $"),
    Modell("minimax", "MiniMax-Hailuo-02", "video", 4, usd_pro_sekunde=0.28 / 6, aufloesung="768P", max_sekunden=10,
           schluessel="MINIMAX_API_KEY", kommerziell="laut AGB mit bezahltem API-Konto", hinweis="6-s-Clip 0,28 $"),
    Modell("minimax", "MiniMax-Hailuo-02", "video", 4, usd_pro_sekunde=0.49 / 6, aufloesung="1080P", max_sekunden=6,
           schluessel="MINIMAX_API_KEY", kommerziell="laut AGB mit bezahltem API-Konto", hinweis="6-s-Clip 0,49 $"),
    Modell("hedra", "Character-3", "sprechender_kopf", 4, usd_pro_sekunde=0.025, aufloesung="540p", max_sekunden=60,
           schluessel="HEDRA_API_KEY", kommerziell="nur bezahlter Tarif (Free: Wasserzeichen, nicht kommerziell)"),
    Modell("hedra", "Character-3", "sprechender_kopf", 4, usd_pro_sekunde=0.05, aufloesung="720p", max_sekunden=60,
           schluessel="HEDRA_API_KEY", kommerziell="nur bezahlter Tarif (Free: Wasserzeichen, nicht kommerziell)"),
    Modell("hedra", "Character-3", "sprechender_kopf", 4, usd_pro_sekunde=0.0625, aufloesung="1080p", max_sekunden=60,
           schluessel="HEDRA_API_KEY", kommerziell="nur bezahlter Tarif (Free: Wasserzeichen, nicht kommerziell)"),
    Modell("gemini", "gemini-2.5-flash-image", "bild", 4, usd_pro_bild=0.039, aufloesung="1K",
           schluessel="GEMINI_API_KEY", kommerziell="ja (bezahlte API; SynthID-Wasserzeichen unsichtbar)"),
    Modell("gemini", "gemini-3-pro-image-preview", "bild", 5, usd_pro_bild=0.134, aufloesung="2K",
           schluessel="GEMINI_API_KEY", kommerziell="ja (bezahlte API; Vorschau-Modell)"),
]


def katalog() -> list[Modell]:
    return _runway_modelle() + KATALOG


# ------------------------------------------------------------------------------- Modellauswahl ---
@dataclass
class Auswahl:
    modell: Modell | None
    kosten_usd: float
    grund: str
    kandidaten: list[tuple[str, float, int, bool]] = field(default_factory=list)


def waehle(art: str, sekunden: float = 0.0, bilder: int = 0, mindest_qualitaet: int = 3,
           max_usd: float | None = None, freigegeben: set[str] | None = None,
           aufloesung_min: int = 0) -> Auswahl:
    """Günstigstes Modell, das Qualität, Budget und Freigabe erfüllt (Freigabe: Anbieternamen)."""
    def px(a: str) -> int:
        digits = "".join(ch for ch in a if ch.isdigit())
        return int(digits) if digits and not a.endswith("K") else (1024 * int(digits or 1))
    passend = []
    kandidaten = []
    for m in katalog():
        if m.art != art:
            continue
        k = m.kosten(sekunden, bilder)
        ok_frei = freigegeben is None or m.anbieter in freigegeben
        ok = (m.qualitaet >= mindest_qualitaet and (max_usd is None or k <= max_usd) and ok_frei
              and (not aufloesung_min or not m.aufloesung or px(m.aufloesung) >= aufloesung_min))
        kandidaten.append((f"{m.anbieter}/{m.name} {m.aufloesung}".strip(), k, m.qualitaet, ok))
        if ok:
            passend.append((k, -m.qualitaet, m))
    kandidaten.sort(key=lambda x: x[1])
    if not passend:
        return Auswahl(None, 0.0, "Kein Modell erfüllt Qualität, Budget und Freigabe", kandidaten)
    k, _, m = min(passend, key=lambda x: (x[0], x[1]))
    return Auswahl(m, k, f"günstigstes freigegebenes Modell mit Qualität ≥ {mindest_qualitaet}", kandidaten)


# ------------------------------------------------------------------------------------ Freigabe ---
class NichtFreigegeben(RuntimeError):
    pass


def freigaben(path: Path | None = None) -> dict[str, bool]:
    cfg = yaml.safe_load((path or ROOT / "config" / "budget.yaml").read_text(encoding="utf-8")) or {}
    return {k: v is True for k, v in (cfg.get("zusatz_anbieter") or {}).items()}


Transport = Callable[[str, str, dict, bytes | None], tuple[int, Any]]


def _urllib(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, Any]:  # pragma: no cover
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 – feste Anbieter-URLs
        return r.status, json.loads(r.read() or b"null")


class _Adapter:
    anbieter = ""
    schluessel_env = ""
    endpunkte_geprueft = True

    def __init__(self, transport: Transport = _urllib, budget_yaml: Path | None = None):
        self._transport = transport
        self._budget_yaml = budget_yaml

    def __repr__(self) -> str:   # niemals den Schlüssel zeigen
        return f"<{type(self).__name__} anbieter={self.anbieter}>"

    def _schluessel(self) -> str:
        if not freigaben(self._budget_yaml).get(self.anbieter):
            raise NichtFreigegeben(f"{self.anbieter}: in config/budget.yaml (zusatz_anbieter) nicht freigegeben")
        if not self.endpunkte_geprueft:
            raise NichtFreigegeben(f"{self.anbieter}: Endpunkte noch nicht gegen die offizielle Doku geprüft")
        key = os.environ.get(self.schluessel_env, "")
        if not key:
            raise NichtFreigegeben(f"{self.anbieter}: Secret {self.schluessel_env} fehlt")
        return key

    def _pruefe_budget(self, kosten: float, max_usd: float) -> None:
        if kosten > max_usd:
            raise NichtFreigegeben(f"{self.anbieter}: geschätzt {kosten:.2f} $ > Grenze {max_usd:.2f} $")


class HailuoVideo(_Adapter):
    """MiniMax Hailuo – Text/Bild zu Video (asynchron: Auftrag → Status → Datei)."""
    anbieter, schluessel_env = "minimax", "MINIMAX_API_KEY"
    BASIS = "https://api.minimax.io/v1"

    def anfrage(self, prompt: str, sekunden: int = 6, aufloesung: str = "768P",
                startbild: str | None = None) -> dict:
        if sekunden not in (6, 10):
            raise ValueError("Hailuo-02: Dauer 6 oder 10 s")
        if aufloesung == "1080P" and sekunden != 6:
            raise ValueError("Hailuo-02: 1080P nur mit 6 s")
        body = {"model": "MiniMax-Hailuo-02", "prompt": prompt[:2000], "duration": sekunden,
                "resolution": aufloesung, "prompt_optimizer": True}
        if startbild:
            body["first_frame_image"] = startbild
        return body

    def kosten(self, body: dict) -> float:
        m = next(m for m in KATALOG if m.anbieter == "minimax" and m.aufloesung == body["resolution"])
        return m.kosten(body["duration"])

    def submit(self, body: dict, max_usd: float) -> str:
        key = self._schluessel()
        self._pruefe_budget(self.kosten(body), max_usd)
        status, data = self._transport("POST", f"{self.BASIS}/video_generation",
                                       {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                       json.dumps(body).encode())
        task = (data or {}).get("task_id")
        if status != 200 or not task:
            raise RuntimeError(f"Hailuo: Auftrag abgelehnt (HTTP {status})")
        return task


class GeminiBild(_Adapter):
    """Google Gemini „Nano Banana“ – Charakter-Referenzen, Szenenbilder, Storyboards."""
    anbieter, schluessel_env = "gemini", "GEMINI_API_KEY"
    BASIS = "https://generativelanguage.googleapis.com/v1beta/models"

    def anfrage(self, prompt: str, referenzen: list[Path] | None = None, seitenverhaeltnis: str = "9:16",
                modell: str = "gemini-2.5-flash-image") -> dict:
        teile: list[dict] = [{"text": prompt}]
        for r in referenzen or []:
            mime = "image/png" if r.suffix.lower() == ".png" else "image/jpeg"
            teile.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(r.read_bytes()).decode()}})
        return {"_modell": modell, "contents": [{"parts": teile}],
                "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": seitenverhaeltnis}}}

    def kosten(self, body: dict) -> float:
        return next(m for m in KATALOG if m.name == body["_modell"]).kosten(bilder=1)

    def submit(self, body: dict, max_usd: float) -> bytes:
        key = self._schluessel()
        self._pruefe_budget(self.kosten(body), max_usd)
        modell = body["_modell"]
        payload = {k: v for k, v in body.items() if not k.startswith("_")}
        status, data = self._transport("POST", f"{self.BASIS}/{modell}:generateContent",
                                       {"x-goog-api-key": key, "Content-Type": "application/json"},
                                       json.dumps(payload).encode())
        for cand in (data or {}).get("candidates", []):
            for part in (cand.get("content") or {}).get("parts", []):
                inline = part.get("inline_data") or part.get("inlineData")
                if inline and inline.get("data"):
                    return base64.b64decode(inline["data"])
        raise RuntimeError(f"Gemini: kein Bild in der Antwort (HTTP {status})")


class HedraSprecher(_Adapter):
    """Hedra Character-3 – Standbild + Tonspur → sprechende Figur. Endpunkte noch NICHT geprüft."""
    anbieter, schluessel_env = "hedra", "HEDRA_API_KEY"
    endpunkte_geprueft = False      # erst nach Abgleich mit der offiziellen API-Doku auf True setzen (eigener PR)

    def anfrage(self, bild: Path, ton: Path, sekunden: float, aufloesung: str = "720p",
                prompt: str = "") -> dict:
        if not bild.is_file() or not ton.is_file():
            raise ValueError("Hedra braucht Bild und Tonspur")
        return {"bild": str(bild), "ton": str(ton), "sekunden": float(sekunden), "aufloesung": aufloesung,
                "text_prompt": prompt[:500]}

    def kosten(self, body: dict) -> float:
        m = next(m for m in KATALOG if m.anbieter == "hedra" and m.aufloesung == body["aufloesung"])
        return m.kosten(body["sekunden"])

    def submit(self, body: dict, max_usd: float) -> str:
        self._schluessel()          # bricht ab: Endpunkte ungeprüft
        raise NichtFreigegeben("hedra: nicht implementiert")  # pragma: no cover


def vergleich_tabelle(sekunden: float, bilder: int = 0) -> str:
    """Markdown-Vergleich für eine Episode (z. B. Summe der Videosekunden)."""
    zeilen = ["| Anbieter / Modell | Art | Qualität (Einschätzung) | Kosten | Schlüssel | kommerziell |",
              "|---|---|---:|---:|---|---|"]
    for m in sorted(katalog(), key=lambda m: (m.art, m.kosten(sekunden, bilder))):
        k = m.kosten(sekunden if m.art != "bild" else 0, bilder if m.art == "bild" else 0)
        zeilen.append(f"| {m.anbieter} / {m.name} {m.aufloesung} | {m.art} | {m.qualitaet} | {k:.2f} $ | "
                      f"{m.schluessel} | {m.kommerziell} |")
    return "\n".join(zeilen)
