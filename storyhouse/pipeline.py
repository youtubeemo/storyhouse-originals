"""Orchestrierung: kostenloser Dry-Run und (freigegebener) bezahlter Render."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from storyhouse import ffmpeg as ff
from storyhouse.budget import BudgetConfig, Pricing
from storyhouse.episode import Episode
from storyhouse.plan import Plan, build_plan, format_estimate
from storyhouse.subtitles import write_subtitles

REPO_ROOT = Path(__file__).resolve().parent.parent
VOICE_TONES = {"reflection": 520, "maya": 440, "the_other": 330}


def _dims(ep: Episode, width: int | None, height: int | None, fps: int | None) -> tuple[int, int, int]:
    return (width or ep.fmt["width"], height or ep.fmt["height"], fps or ep.fmt["fps"])


def _voice_fx(ep: Episode, character: str) -> str | None:
    fx_name = ep.characters[character].get("voice", {}).get("fx")
    return ep.sound.get("voice_fx", {}).get(fx_name) if fx_name else None


def assemble(ep: Episode, clips: dict[str, Path], dialogue_audio: dict[str, Path],
             sfx_audio: dict[str, Path], out_dir: Path, width: int, height: int, fps: int,
             tag: str) -> dict:
    """Gemeinsamer Endschnitt für Dry-Run und echten Render."""
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)

    # 1) Bild: normalisieren + schneiden
    norm = [ff.normalize_clip(clips[s["id"]], work / f"{s['id']}_norm.mp4", float(s["duration"]), width, height, fps)
            for s in ep.scenes]
    picture = ff.concat_clips(norm, work / "picture.mp4")

    # 2) Ton
    tracks: list[ff.Track] = []
    for line in ep.dialogue:
        if line["id"] in dialogue_audio:
            tracks.append(ff.Track(dialogue_audio[line["id"]], float(line["at"]), 0.0, "dialogue",
                                   _voice_fx(ep, line["character"])))
    for d in ep.sound.get("drones", []):
        if d["id"] in sfx_audio:
            tracks.append(ff.Track(sfx_audio[d["id"]], float(d.get("at", 0.0)), float(d["gain_db"]), "bed",
                                   fill=bool(d.get("repeat_to_fill"))))
    for s in ep.sound.get("sfx", []):
        if s["id"] in sfx_audio:
            tracks.append(ff.Track(sfx_audio[s["id"]], float(s["at"]), float(s["gain_db"]), "bed"))
    music_cfg = ep.sound.get("music", {})
    music = REPO_ROOT / music_cfg["file"] if music_cfg.get("file") else None
    if music and music.exists():
        tracks.append(ff.Track(music, 0.0, float(music_cfg.get("bed_gain_db", -16)), "bed", fill=True))
    windows = [(float(w["from"]), float(w["to"])) for w in ep.sound.get("silence_windows", [])]
    mix = work / "mix.wav"
    ff.run(ff.build_mix_command(tracks, ep.duration, mix, windows,
                                float(ep.fmt.get("loudness_lufs", -14)), float(ep.fmt.get("true_peak_db", -1))))

    # 3) Untertitel + Export
    srt, ass = write_subtitles(ep, out_dir)
    final = out_dir / f"{ep.slug}_{tag}.mp4"
    ff.finalize(picture, mix, ass, final, ep.duration)
    info = ff.probe(final)
    return {"video": str(final), "srt": str(srt), "ass": str(ass), "probe": info,
            "music_used": bool(music and music.exists()), "tracks": len(tracks)}


def dry_run(ep_full: Episode, out_dir: Path, pricing: Pricing, cfg: BudgetConfig, scenes: str = "all",
            profile: str = "test", width: int | None = None, height: int | None = None,
            fps: int | None = None) -> dict:
    """Komplett kostenlos: Platzhalter statt KI, aber echter Schnitt/Ton/Untertitel-Pfad."""
    ff.require_ffmpeg()
    ep = ep_full.subset(scenes)
    w, h, r = _dims(ep, width, height, fps)
    raw = out_dir / "raw"
    clips = {s["id"]: ff.placeholder_clip(s, raw / f"VID_{s['id']}.mp4", w, h, r) for s in ep.scenes}
    dia = {
        d["id"]: ff.placeholder_tone(raw / f"TTS_{d['id']}.wav", max(0.6, len(d["text"]) / 13),
                                     VOICE_TONES.get(d["character"], 440))
        for d in ep.dialogue
    }
    sfx = {x["id"]: ff.placeholder_noise(raw / f"SFX_{x['id']}.wav", float(x["duration"]))
           for x in ep.sound.get("sfx", [])}
    for d in ep.sound.get("drones", []):
        sfx[d["id"]] = ff.placeholder_drone(raw / f"SFX_{d['id']}.wav", float(d["duration"]))
    result = assemble(ep, clips, dia, sfx, out_dir, w, h, r, tag="dryrun")

    plan = build_plan(ep_full, pricing, profile=profile, scenes=scenes)
    estimate = format_estimate(plan, pricing, cfg)
    (out_dir / "estimate.md").write_text(estimate + "\n", encoding="utf-8")
    (out_dir / "plan.json").write_text(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    manifest = {"mode": "dry-run", "paid_api_calls": 0, "spent_eur": 0.0,
                "estimate_if_rendered_eur": plan.total_eur(pricing, cfg), **result}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def paid_render(ep_full: Episode, plan: Plan, client, out_dir: Path, scenes: str = "all",
                downloader: Callable[[str, Path], Path] | None = None, log: Callable[[str], None] = print) -> dict:
    """Führt einen freigegebenen Plan aus. Budgetprüfungen erfolgen im Client (SpendGuard)."""
    from storyhouse.runway import download

    ff.require_ffmpeg()
    downloader = downloader or download
    ep = ep_full.subset(scenes)
    raw = out_dir / "raw"
    outputs: dict[str, str] = {}
    files: dict[str, Path] = {}
    started = time.time()
    try:
        for job in plan.jobs:
            log(f"→ {job.id} ({job.model}, ~{job.credits:.0f} Credits)")
            url = client.run_job(job, outputs)
            outputs[job.id] = url
            files[job.id] = downloader(url, raw / f"{job.id}.{job.ext}")
    finally:
        (out_dir / "outputs.json").parent.mkdir(parents=True, exist_ok=True)
        (out_dir / "outputs.json").write_text(json.dumps({k: str(v) for k, v in files.items()}, indent=2),
                                              encoding="utf-8")

    clips = {s["id"]: files[f"VID_{s['id']}"] for s in ep.scenes}
    dia = {d["id"]: files[f"TTS_{d['id']}"] for d in ep.dialogue if f"TTS_{d['id']}" in files}
    sfx = {k[4:]: v for k, v in files.items() if k.startswith("SFX_")}
    w, h, r = _dims(ep, None, None, None)
    result = assemble(ep, clips, dia, sfx, out_dir, w, h, r, tag=f"{plan.profile}")
    manifest = {"mode": "paid-render", "profile": plan.profile, "scenes": plan.scenes,
                "paid_api_calls": len(plan.jobs), "elapsed_s": round(time.time() - started, 1), **result}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest
