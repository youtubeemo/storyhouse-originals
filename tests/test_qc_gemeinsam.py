"""Tests für die gemeinsamen Prüfwerkzeuge scripts/videoqc.py und scripts/secret_scan.py.

Nutzt winzige, lokal erzeugte Testvideos (ffmpeg lavfi, < 2 s) – schnell und kostenlos.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _lade(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


qc = _lade("videoqc")
ss = _lade("secret_scan")
braucht_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg fehlt")


def _video(path: Path, w=180, h=320, sek=1.5, ton=True, quelle="testsrc2", lufs_quelle="sine=f=440:r=48000",
           pix="yuv420p", vf=None) -> Path:
    sep = ":" if "=" in quelle else "="
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{quelle}{sep}s={w}x{h}:r=30"]
    if ton:
        args += ["-f", "lavfi", "-i", lufs_quelle, "-c:a", "aac"]
    if vf:
        args += ["-vf", vf]
    args += ["-t", str(sek), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", pix, str(path)]
    subprocess.run(args, check=True)
    return path


@braucht_ffmpeg
def test_gutes_video_besteht(tmp_path):
    rep = qc.pruefen(_video(tmp_path / "ok.mp4"), "vorschau", dauer=1.5, toleranz=0.2)
    assert rep.ok, rep.markdown()
    assert rep.werte["breite"] == 180 and rep.werte["ton"] is True and 29 < rep.werte["fps"] < 31


@braucht_ffmpeg
def test_fehlende_datei_und_tonspur(tmp_path):
    assert not qc.pruefen(tmp_path / "fehlt.mp4").ok
    rep = qc.pruefen(_video(tmp_path / "stumm.mp4", ton=False), "vorschau", inhalt=False)
    assert not rep.ok and any(p.name == "Tonspur" and not p.ok for p in rep.pruefungen)


@braucht_ffmpeg
def test_seitenverhaeltnis_und_aufloesung_fuer_shorts(tmp_path):
    quer = _video(tmp_path / "quer.mp4", w=320, h=180)
    rep = qc.pruefen(quer, "shorts", inhalt=False, min_hoehe=180, lufs=None)
    assert any(p.name == "Seitenverhältnis" and not p.ok for p in rep.pruefungen)
    hoch = _video(tmp_path / "hoch.mp4", w=180, h=320)
    rep = qc.pruefen(hoch, "shorts", inhalt=False, min_hoehe=1280, lufs=None)
    assert any(p.name == "Auflösung" and not p.ok for p in rep.pruefungen)


@braucht_ffmpeg
def test_falsches_pixelformat_fuer_iphone(tmp_path):
    rep = qc.pruefen(_video(tmp_path / "444.mp4", pix="yuv444p"), "vorschau", inhalt=False)
    assert any(p.name == "Codec iPhone/YouTube" and not p.ok for p in rep.pruefungen)


@braucht_ffmpeg
def test_lautheit_wird_gemessen_und_bewertet(tmp_path):
    v = _video(tmp_path / "laut.mp4", lufs_quelle="sine=f=440:r=48000")
    l = qc.lautheit(v)
    assert -30 < l["lufs"] < 0 and l["true_peak"] <= 0.5
    # Sinuston liegt weit über -40 LUFS → mit dieser Vorgabe muss die Lautheitsprüfung scheitern
    rep =qc.pruefen(v, "film", inhalt=False, lufs=-40.0, min_hoehe=0, seitenverhaeltnis=None)
    assert any(p.name == "Lautheit" and not p.ok for p in rep.pruefungen)


@braucht_ffmpeg
def test_schwarze_stellen(tmp_path):
    # Abblende am Anfang ist erlaubt
    v = _video(tmp_path / "schwarz.mp4", quelle="color=c=black", sek=1.6)
    assert next(p for p in qc.pruefen(v, "vorschau").pruefungen if p.name == "Keine schwarzen Stellen").ok
    # schwarzes Loch mitten im Video → Warnung, aber nicht blockierend
    v = _video(tmp_path / "loch.mp4", sek=2.0, vf="drawbox=t=fill:c=black:enable='gte(t,0.6)'")
    rep = qc.pruefen(v, "vorschau")
    schwarz = next(p for p in rep.pruefungen if p.name == "Keine schwarzen Stellen")
    assert not schwarz.ok and schwarz.warnung and rep.ok


def test_srt_pruefung(tmp_path):
    gut = tmp_path / "gut.srt"
    gut.write_text("1\n00:00:00,000 --> 00:00:01,000\nHallo\n\n2\n00:00:01,100 --> 00:00:01,400\nWelt\n", encoding="utf-8")
    assert qc.srt_pruefen(gut, 1.5) == (True, "2 Einträge, zeitlich gültig")
    zu_lang = tmp_path / "lang.srt"
    zu_lang.write_text("1\n00:00:00,000 --> 00:00:09,000\nHallo\n", encoding="utf-8")
    assert not qc.srt_pruefen(zu_lang, 1.5)[0]
    verdreht = tmp_path / "verdreht.srt"
    verdreht.write_text("1\n00:00:01,000 --> 00:00:00,500\nHallo\n", encoding="utf-8")
    assert not qc.srt_pruefen(verdreht, 1.5)[0]
    leer = tmp_path / "ohne_text.srt"
    leer.write_text("1\n00:00:00,000 --> 00:00:01,000\n\n", encoding="utf-8")
    assert not qc.srt_pruefen(leer, 1.5)[0]


@braucht_ffmpeg
def test_duplikate_erkennt_neu_kodierte_kopie(tmp_path):
    a = _video(tmp_path / "a.mp4", quelle="testsrc2", ton=False)
    kopie = tmp_path / "kopie.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(a), "-c:v", "libx264", "-crf", "35", "-preset", "ultrafast",
                    str(kopie)], check=True)
    anders = _video(tmp_path / "b.mp4", quelle="mandelbrot", ton=False)
    treffer = qc.duplikate([a, kopie, anders])
    paare = {frozenset((Path(x).name, Path(y).name)) for x, y, _ in treffer}
    assert frozenset(("a.mp4", "kopie.mp4")) in paare
    assert not any("b.mp4" in p for p in paare)


def test_secret_scan_findet_und_maskiert():
    token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"   # zur Laufzeit gebaut, steht so nicht im Repo
    funde = ss.scan_text(f"export GH={token}\n", "beispiel.sh")
    assert funde and funde[0]["typ"] == "GitHub-Token" and token not in str(funde)
    key = "sk-ant-" + "api03-" + "Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Ji1Hg0Fe"
    assert ss.scan_text(f"ANTHROPIC_API_KEY={key}", "x")[0]["typ"] == "Anthropic-Schlüssel"


def test_secret_scan_ignoriert_platzhalter_und_secrets_verweise():
    text = "\n".join([
        "RUNWAY_API_KEY: ${{ secrets.RUNWAY_API_KEY }}",
        "MINIMAX_API_KEY=$MINIMAX_API_KEY",
        "HEDRA_API_KEY = os.environ['HEDRA_API_KEY']",
        "GEMINI_API_KEY=<dein-schluessel>",
        "token = 'ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'",
        "ARTIFACT_PASSPHRASE: ${{ secrets.ARTIFACT_PASSPHRASE }}",
    ])
    assert ss.scan_text(text, "workflow.yml") == []


def test_repo_enthaelt_keine_geheimnisse():
    funde = ss.scan(ss.dateien(ROOT), ROOT)
    assert funde == [], funde
