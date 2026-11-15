#!/usr/bin/env python3
"""Kostenlose Qualitätsprüfung für fertige Videos (nur ffprobe/ffmpeg, keine Netzaufrufe).

Gemeinsame Datei für StoryHouse Originals, GTA-Shorts.Germany und Dschungel-Freunde – bewusst als
eigenständige Kopie in jedem Repo (keine Abhängigkeit zwischen den Repos). Version unten erhöhen,
wenn die Datei geändert wird, und in allen drei Repos gleich halten.

Prüft: Datei vorhanden, Länge, Auflösung/Seitenverhältnis, Bildrate, Codec + Pixelformat (iPhone/YouTube/
TikTok), Tonspur, Lautheit (LUFS) und True Peak, schwarze und eingefrorene Stellen, Untertitel (SRT),
sowie doppelte Videos über einen Bild-Fingerabdruck.

    python scripts/videoqc.py film.mp4 --profil shorts --srt film.srt --json bericht.json
    python scripts/videoqc.py film.mp4 --dauer 45 --toleranz 1.5 --lufs -14
    python scripts/videoqc.py --duplikate output/*.mp4
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path

VERSION = "1.0.0"

# Voreinstellungen je Ziel. Jede Angabe lässt sich per Kommandozeile überschreiben.
PROFILE: dict[str, dict] = {
    # YouTube Shorts / TikTok: hochkant 9:16, ≤ 180 s (Shorts) bzw. ≤ 10 min (TikTok-Upload)
    "shorts": {"seitenverhaeltnis": "9:16", "min_hoehe": 1280, "fps": (24, 60), "max_dauer": 180,
               "lufs": -14.0, "lufs_toleranz": 2.5, "max_true_peak": -1.0},
    # Film/Episode quer 16:9 (YouTube)
    "film": {"seitenverhaeltnis": "16:9", "min_hoehe": 720, "fps": (23.9, 60), "max_dauer": 3600,
             "lufs": -14.0, "lufs_toleranz": 3.0, "max_true_peak": -1.0},
    # Kinderserie (ruhiger gemischt, Made for Kids)
    "kinder": {"seitenverhaeltnis": "16:9", "min_hoehe": 360, "fps": (23.9, 60), "max_dauer": 3600,
               "lufs": -16.0, "lufs_toleranz": 3.0, "max_true_peak": -1.0},
    # Vorschau/Animatic: nur Technik prüfen, keine Lautheits-/Auflösungsvorgaben
    "vorschau": {"seitenverhaeltnis": None, "min_hoehe": 0, "fps": (10, 60), "max_dauer": 3600,
                 "lufs": None, "lufs_toleranz": 0, "max_true_peak": None},
}

SRT_ZEIT = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


@dataclass
class Pruefung:
    name: str
    ok: bool
    detail: str = ""
    warnung: bool = False  # Warnung = nicht blockierend


@dataclass
class Bericht:
    datei: str
    version: str = VERSION
    pruefungen: list[Pruefung] = field(default_factory=list)
    werte: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(p.ok or p.warnung for p in self.pruefungen)

    def add(self, name: str, ok: bool, detail: str = "", warnung: bool = False) -> None:
        self.pruefungen.append(Pruefung(name, bool(ok), detail, warnung and not ok))

    def as_dict(self) -> dict:
        return {"datei": self.datei, "version": self.version, "ok": self.ok, "werte": self.werte,
                "pruefungen": [asdict(p) for p in self.pruefungen]}

    def markdown(self) -> str:
        zeilen = [f"### Qualitätsprüfung `{Path(self.datei).name}` – {'✅ bestanden' if self.ok else '❌ NICHT bestanden'}",
                  "", "| Prüfung | Ergebnis | Detail |", "|---|---|---|"]
        for p in self.pruefungen:
            sym = "✅" if p.ok else ("⚠️" if p.warnung else "❌")
            zeilen.append(f"| {p.name} | {sym} | {p.detail} |")
        return "\n".join(zeilen)


# ----------------------------------------------------------------------------------------- Werkzeuge --
def _ffprobe() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        raise RuntimeError("ffprobe fehlt (ffmpeg installieren)")
    return exe


def probe(path: Path) -> dict:
    out = subprocess.run([_ffprobe(), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe kann die Datei nicht lesen: {out.stderr.strip()[:200]}")
    data = json.loads(out.stdout)
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    dauer = float(data.get("format", {}).get("duration") or (v or {}).get("duration") or 0)
    fps = 0.0
    if v and v.get("avg_frame_rate") not in (None, "0/0"):
        fps = float(Fraction(v["avg_frame_rate"]))
    return {
        "dauer": round(dauer, 3),
        "breite": int(v["width"]) if v else 0,
        "hoehe": int(v["height"]) if v else 0,
        "fps": round(fps, 3),
        "video_codec": (v or {}).get("codec_name", ""),
        "pix_fmt": (v or {}).get("pix_fmt", ""),
        "ton": a is not None,
        "ton_codec": (a or {}).get("codec_name", ""),
        "abtastrate": int((a or {}).get("sample_rate") or 0),
    }


def lautheit(path: Path) -> dict:
    """Integrierte Lautheit (LUFS) und True Peak (dBTP) nach EBU R128 über ffmpeg-loudnorm."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
                        "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=600)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        raise RuntimeError("Lautheit nicht messbar")
    d = json.loads(m.group(0))
    return {"lufs": float(d["input_i"]), "true_peak": float(d["input_tp"])}


def _detect(path: Path, filt: str, start_key: str, end_key: str, audio: bool = False) -> list[tuple[float, float]]:
    args = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path)]
    args += (["-vn", "-af", filt] if audio else ["-an", "-vf", filt]) + ["-f", "null", "-"]
    r = subprocess.run(args, capture_output=True, text=True, timeout=900)
    starts = [float(x) for x in re.findall(rf"{start_key}:\s*([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(rf"{end_key}:\s*([\d.]+)", r.stderr)]
    return list(zip(starts, ends + [float("inf")] * (len(starts) - len(ends))))


def schwarze_stellen(path: Path, min_dauer: float = 1.0) -> list[tuple[float, float]]:
    return _detect(path, f"blackdetect=d={min_dauer}:pix_th=0.10", "black_start", "black_end")


def eingefrorene_stellen(path: Path, min_dauer: float = 3.0) -> list[tuple[float, float]]:
    return _detect(path, f"freezedetect=n=0.003:d={min_dauer}", "freeze_start", "freeze_end")


def srt_pruefen(srt: Path, dauer: float) -> tuple[bool, str]:
    text = srt.read_text(encoding="utf-8-sig", errors="replace")
    bloecke = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    if not bloecke:
        return False, "leer"
    vorher = -1.0
    for i, b in enumerate(bloecke, 1):
        m = SRT_ZEIT.search(b)
        if not m:
            return False, f"Block {i}: keine Zeitangabe"
        g = [int(x) for x in m.groups()]
        a = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        e = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        lines = b.splitlines()
        idx = next(j for j, z in enumerate(lines) if SRT_ZEIT.search(z))
        zeilen = [z for z in lines[idx + 1:] if z.strip()]
        if e <= a:
            return False, f"Block {i}: Ende vor Anfang"
        if a < vorher - 0.001:
            return False, f"Block {i}: überlappt/ungeordnet"
        if dauer and e > dauer + 0.5:
            return False, f"Block {i}: endet nach dem Video ({e:.1f} s > {dauer:.1f} s)"
        if not zeilen:
            return False, f"Block {i}: ohne Text"
        vorher = e
    return True, f"{len(bloecke)} Einträge, zeitlich gültig"


def _seitenverhaeltnis_ok(b: int, h: int, soll: str, toleranz: float = 0.02) -> bool:
    sb, sh = (int(x) for x in soll.split(":"))
    return b > 0 and h > 0 and abs(b / h - sb / sh) <= toleranz * (sb / sh)


# ------------------------------------------------------------------------------------------- Prüfung --
def pruefen(path: str | Path, profil: str = "vorschau", dauer: float | None = None, toleranz: float = 1.0,
            srt: str | Path | None = None, lufs: float | None = None, inhalt: bool = True, **ueber) -> Bericht:
    p = Path(path)
    cfg = {**PROFILE[profil], **{k: v for k, v in ueber.items() if v is not None}}
    if lufs is not None:
        cfg["lufs"] = lufs
    rep = Bericht(str(p))
    if not p.is_file() or p.stat().st_size == 0:
        rep.add("Datei vorhanden", False, "fehlt oder leer")
        return rep
    rep.add("Datei vorhanden", True, f"{p.stat().st_size / 1e6:.1f} MB")
    try:
        info = probe(p)
    except RuntimeError as exc:
        rep.add("Lesbar", False, str(exc))
        return rep
    rep.werte.update(info)
    rep.add("Videospur", info["breite"] > 0, f"{info['breite']}×{info['hoehe']} {info['video_codec']}")
    if dauer is not None:
        rep.add("Länge", abs(info["dauer"] - dauer) <= toleranz, f"{info['dauer']:.2f} s (Soll {dauer:g} ± {toleranz:g})")
    else:
        rep.add("Länge", 0 < info["dauer"] <= cfg["max_dauer"], f"{info['dauer']:.2f} s (max. {cfg['max_dauer']} s)")
    if cfg.get("seitenverhaeltnis"):
        rep.add("Seitenverhältnis", _seitenverhaeltnis_ok(info["breite"], info["hoehe"], cfg["seitenverhaeltnis"]),
                f"{info['breite']}×{info['hoehe']} (Soll {cfg['seitenverhaeltnis']})")
    if cfg.get("min_hoehe"):
        kurz = min(info["breite"], info["hoehe"]) if cfg.get("seitenverhaeltnis") == "16:9" else info["hoehe"]
        rep.add("Auflösung", kurz >= cfg["min_hoehe"], f"{info['hoehe']} px (min. {cfg['min_hoehe']})")
    lo, hi = cfg["fps"]
    rep.add("Bildrate", lo <= info["fps"] <= hi, f"{info['fps']:g} fps ({lo:g}–{hi:g})")
    rep.add("Codec iPhone/YouTube", info["video_codec"] in ("h264", "hevc") and info["pix_fmt"] in ("yuv420p", "yuvj420p"),
            f"{info['video_codec']} {info['pix_fmt']} (h264/hevc + yuv420p)")
    rep.add("Tonspur", info["ton"], f"{info['ton_codec']} {info['abtastrate']} Hz" if info["ton"] else "keine")
    if info["ton"] and cfg.get("lufs") is not None:
        try:
            l = lautheit(p)
            rep.werte.update(l)
            rep.add("Lautheit", abs(l["lufs"] - cfg["lufs"]) <= cfg["lufs_toleranz"],
                    f"{l['lufs']:.1f} LUFS (Soll {cfg['lufs']:g} ± {cfg['lufs_toleranz']:g})")
            if cfg.get("max_true_peak") is not None:
                rep.add("True Peak", l["true_peak"] <= cfg["max_true_peak"] + 0.05,
                        f"{l['true_peak']:.1f} dBTP (max. {cfg['max_true_peak']:g})")
        except RuntimeError as exc:
            rep.add("Lautheit", False, str(exc))
    if inhalt:
        schwarz = [s for s in schwarze_stellen(p) if s[0] > 0.2]  # Abblende am Anfang ist erlaubt
        rep.add("Keine schwarzen Stellen", not schwarz,
                ", ".join(f"{a:.1f}–{min(e, info['dauer']):.1f} s" for a, e in schwarz) or "keine ≥ 1 s",
                warnung=True)
        frei = eingefrorene_stellen(p)
        rep.add("Keine eingefrorenen Stellen", not frei,
                ", ".join(f"{a:.1f}–{min(e, info['dauer']):.1f} s" for a, e in frei) or "keine ≥ 3 s", warnung=True)
    if srt is not None:
        sp = Path(srt)
        if not sp.is_file():
            rep.add("Untertitel", False, f"{sp.name} fehlt")
        else:
            ok, detail = srt_pruefen(sp, info["dauer"])
            rep.add("Untertitel", ok, detail)
    return rep


# --------------------------------------------------------------------------------------- Duplikate --
def fingerabdruck(path: str | Path, bilder: int = 8) -> str:
    """Bild-Fingerabdruck (dHash über gleichmäßig verteilte Bilder) – erkennt neu kodierte Kopien."""
    info = probe(Path(path))
    dauer = max(info["dauer"], 0.1)
    bits = []
    for i in range(bilder):
        t = dauer * (i + 0.5) / bilder
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                            "-vf", "scale=9:8:flags=area,format=gray", "-f", "rawvideo", "-"],
                           capture_output=True, timeout=60)
        px = r.stdout[:72].ljust(72, b"\0")
        bits += [px[y * 9 + x] > px[y * 9 + x + 1] for y in range(8) for x in range(8)]
    return "".join("1" if b else "0" for b in bits)


def aehnlichkeit(a: str, b: str) -> float:
    n = min(len(a), len(b))
    return 1 - sum(x != y for x, y in zip(a[:n], b[:n])) / max(n, 1)


def duplikate(pfade: list[str | Path], schwelle: float = 0.92) -> list[tuple[str, str, float]]:
    fp = {str(p): fingerabdruck(p) for p in pfade}
    namen = list(fp)
    return [(a, b, round(aehnlichkeit(fp[a], fp[b]), 3))
            for i, a in enumerate(namen) for b in namen[i + 1:] if aehnlichkeit(fp[a], fp[b]) >= schwelle]


# ---------------------------------------------------------------------------------------------- CLI --
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("videos", nargs="+")
    ap.add_argument("--profil", default="vorschau", choices=sorted(PROFILE))
    ap.add_argument("--dauer", type=float)
    ap.add_argument("--toleranz", type=float, default=1.0)
    ap.add_argument("--lufs", type=float)
    ap.add_argument("--srt")
    ap.add_argument("--ohne-inhalt", action="store_true", help="schwarze/eingefrorene Stellen nicht prüfen")
    ap.add_argument("--json", help="Bericht als JSON speichern")
    ap.add_argument("--duplikate", action="store_true", help="Videos untereinander auf Duplikate prüfen")
    a = ap.parse_args(argv)
    if a.duplikate:
        treffer = duplikate(a.videos)
        for x, y, s in treffer:
            print(f"❌ Duplikat ({s:.0%}): {x} ≈ {y}")
        print("✅ keine Duplikate" if not treffer else "")
        return 1 if treffer else 0
    ok = True
    berichte = []
    for v in a.videos:
        rep = pruefen(v, a.profil, a.dauer, a.toleranz, a.srt, a.lufs, inhalt=not a.ohne_inhalt)
        print(rep.markdown())
        berichte.append(rep.as_dict())
        ok &= rep.ok
    if a.json:
        Path(a.json).write_text(json.dumps(berichte if len(berichte) > 1 else berichte[0], ensure_ascii=False, indent=2),
                                encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
