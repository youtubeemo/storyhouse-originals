"""Orchestrierung: kostenloser Dry-Run und (freigegebener) bezahlter Render.

Bild kommt von Runway, Ton von ElevenLabs (oder Runway als Ausweichweg); FFmpeg
synchronisiert, mischt, brennt Untertitel ein und hängt das StoryHouse-Outro an.
"""
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


def _dialogue_slots(ep: Episode) -> dict[str, float]:
    """Verfügbare Zeit je Dialogzeile: bis kurz vor die nächste Zeile bzw. bis Filmende."""
    lines = ep.dialogue
    slots: dict[str, float] = {}
    for i, line in enumerate(lines):
        end = float(lines[i + 1]["at"]) - 0.10 if i + 1 < len(lines) else ep.duration
        slots[line["id"]] = max(0.3, min(end, ep.duration) - float(line["at"]))
    return slots


def assemble(ep: Episode, clips: dict[str, Path], dialogue_audio: dict[str, Path],
             sfx_audio: dict[str, Path], out_dir: Path, width: int, height: int, fps: int,
             tag: str, music_audio: Path | None = None, metadata: dict[str, str] | None = None) -> dict:
    """Gemeinsamer Endschnitt für Dry-Run und echten Render.

    Ergebnis: <slug>_<tag>.mp4 (Film + Outro) und, falls ein Outro aktiv ist,
    <slug>_<tag>_loop.mp4 (ohne Outro, letzter Frame == erster Frame).
    """
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)

    # 1) Bild: normalisieren + schneiden (jede Szene exakt auf ihre Dauer -> Bild folgt der Zeitachse)
    norm = [ff.normalize_clip(clips[s["id"]], work / f"{s['id']}_norm.mp4", float(s["duration"]), width, height, fps)
            for s in ep.scenes]
    picture = ff.concat_clips(norm, work / "picture.mp4")
    loop = next((s.get("loop") for s in ep.scenes if s.get("loop")), None)
    if loop and loop.get("match_frame_with") == ep.scenes[0]["id"]:
        # Unabhängig vom Videomodell: letzter Frame == erster Frame
        picture = ff.loop_seam(picture, work / "picture_loop.mp4", ep.duration, float(loop.get("seam_s", 0.5)), fps)

    # 2) Ton: Dialog automatisch in die Zeitfenster einpassen, dann mischen
    tracks: list[ff.Track] = []
    slots = _dialogue_slots(ep)
    sync: list[dict] = []
    spoken: dict[str, float] = {}
    for line in ep.dialogue:
        if line["id"] in dialogue_audio:
            fitted = work / f"DIA_{line['id']}.wav"
            report = ff.fit_dialogue(dialogue_audio[line["id"]], fitted, slots[line["id"]])
            spoken[line["id"]] = report["fitted_s"]
            sync.append({"id": line["id"], "at": float(line["at"]), **report})
            tracks.append(ff.Track(fitted, float(line["at"]), 0.0, "dialogue",
                                   _voice_fx(ep, line["character"])))
    for d in ep.sound.get("drones", []):
        if d["id"] in sfx_audio:
            tracks.append(ff.Track(sfx_audio[d["id"]], float(d.get("at", 0.0)), float(d["gain_db"]), "bed",
                                   fill=bool(d.get("repeat_to_fill"))))
    for s in ep.sound.get("sfx", []):
        if s["id"] in sfx_audio:
            tracks.append(ff.Track(sfx_audio[s["id"]], float(s["at"]), float(s["gain_db"]), "bed"))
    music_cfg = ep.sound.get("music", {})
    licensed = REPO_ROOT / music_cfg["file"] if music_cfg.get("file") else None
    music_source = None
    if licensed and licensed.exists():
        tracks.append(ff.Track(licensed, 0.0, float(music_cfg.get("bed_gain_db", -16)), "bed", fill=True))
        music_source = "licensed_file"
    elif music_audio and music_audio.exists():
        fade = float((music_cfg.get("generate") or {}).get("seam_fade_s", 0.4))
        tracks.append(ff.Track(music_audio, 0.0, float(music_cfg.get("bed_gain_db", -16)), "bed", edge_fade=fade))
        music_source = "generated"
    windows = [(float(w["from"]), float(w["to"])) for w in ep.sound.get("silence_windows", [])]
    mix = work / "mix.wav"
    ff.run(ff.build_mix_command(tracks, ep.duration, mix, windows,
                                float(ep.fmt.get("loudness_lufs", -14)), float(ep.fmt.get("true_peak_db", -1))))

    # 3) Untertitel (an die tatsächliche Sprechdauer angepasst)
    srt, ass = write_subtitles(ep, out_dir, spoken)

    # 4) Export: Loop-Fassung und Hauptfassung mit Outro
    final = out_dir / f"{ep.slug}_{tag}.mp4"
    result: dict = {"srt": str(srt), "ass": str(ass), "music_used": music_source is not None,
                    "music_source": music_source, "tracks": len(tracks), "sync": sync, "outro": bool(ep.outro)}
    if ep.outro:
        loop_file = out_dir / f"{ep.slug}_{tag}_loop.mp4"
        ff.finalize(picture, mix, ass, loop_file, ep.duration, metadata=metadata)
        outro_s = ep.outro_duration
        card = ff.outro_clip(ep.outro, work / "outro.mp4", width, height, fps)
        sting_cfg = ep.outro.get("sting") or {}
        tail = ff.outro_audio(sfx_audio.get("outro_sting"), work / "outro.wav", outro_s,
                              float(sting_cfg.get("gain_db", -10)))
        full_mix = ff.concat_audio([mix, tail], work / "mix_full.wav")
        ff.finalize(picture, full_mix, ass, final, ep.duration, outro=card, outro_duration=outro_s,
                    metadata=metadata)
        result.update({"loop_video": str(loop_file), "loop_probe": ff.probe(loop_file)})
    else:
        ff.finalize(picture, mix, ass, final, ep.duration, metadata=metadata)
    info = ff.probe(final)
    drift = None
    if info.get("video_duration") and info.get("audio_duration"):
        drift = round(abs(info["video_duration"] - info["audio_duration"]), 3)
    result.update({"video": str(final), "probe": info, "av_drift_s": drift,
                   "film_duration_s": ep.duration, "total_duration_s": ep.total_duration})
    if drift is not None and drift > 0.1:
        raise ff.FFmpegError(f"Bild und Ton laufen {drift} s auseinander – Export verworfen")
    return result


def dry_run(ep_full: Episode, out_dir: Path, pricing: Pricing, cfg: BudgetConfig, scenes: str = "all",
            profile: str = "test", width: int | None = None, height: int | None = None,
            fps: int | None = None, audio_provider: str | None = None, plan: Plan | None = None) -> dict:
    """Komplett kostenlos: Platzhalter statt KI, aber echter Schnitt/Ton/Untertitel/Outro-Pfad."""
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
    sting = (ep.outro or {}).get("sting")
    if sting:
        sfx["outro_sting"] = ff.placeholder_drone(raw / "SFX_outro_sting.wav", float(sting["duration"]))
    # Musikpfad wird im Dry-Run immer geprobt, auch wenn Musik für bezahlte Läufe noch gesperrt ist.
    music = ff.placeholder_music(raw / "MUSIC_score.wav", ep.duration) if ep.music_generate else None
    result = assemble(ep, clips, dia, sfx, out_dir, w, h, r, tag="dryrun", music_audio=music)

    plan = plan or build_plan(ep_full, pricing, profile=profile, scenes=scenes, audio_provider=audio_provider,
                              include_music=cfg.el_music_license_confirmed)
    estimate = format_estimate(plan, pricing, cfg)
    (out_dir / "estimate.md").write_text(estimate + "\n", encoding="utf-8")
    (out_dir / "plan.json").write_text(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    manifest = {"mode": "dry-run", "paid_api_calls": 0, "spent_eur": 0.0,
                "estimate_if_rendered_eur": plan.total_eur(pricing, cfg), **result}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


PRIVATE_NOTICE = ("PRIVATER TEST - nicht kommerziell, nicht zur Veroeffentlichung. "
                  "Ton: ElevenLabs Free-Tarif (elevenlabs.io), keine kommerzielle Lizenz.")


def paid_render(ep_full: Episode, plan: Plan, client, out_dir: Path, scenes: str = "all",
                downloader: Callable[[str, Path], Path] | None = None, log: Callable[[str], None] = print,
                private_only: bool = False) -> dict:
    """Führt einen freigegebenen Plan aus. Budgetprüfungen erfolgen in den Clients (SpendGuard).

    client: ein Runway-Client oder {"runway": ..., "elevenlabs": ...}.
    """
    from storyhouse.runway import download

    ff.require_ffmpeg()
    downloader = downloader or download
    clients = client if isinstance(client, dict) else {"runway": client}
    missing = [p for p in plan.providers if p not in clients]
    if missing:
        raise RuntimeError(f"Kein Client für Anbieter {missing} – Abbruch vor dem ersten Aufruf")
    ep = ep_full.subset(scenes)
    raw = out_dir / "raw"
    outputs: dict[str, str] = {}
    files: dict[str, Path] = {}
    started = time.time()
    try:
        # ElevenLabs zuerst: Ton ist billig und hängt von nichts ab. Fehlt eine Berechtigung
        # oder lehnt ElevenLabs ab, stoppt der Lauf, BEVOR für Bild/Video Geld ausgegeben wird.
        ordered = ([j for j in plan.jobs if j.provider == "elevenlabs"]
                   + [j for j in plan.jobs if j.provider != "elevenlabs"])
        for job in ordered:
            log(f"→ {job.id} ({job.provider}/{job.model}, ~{job.credits:.0f} Credits)")
            if job.provider == "elevenlabs":
                # synchron: Audiodaten kommen direkt zurück, keine URL
                files[job.id] = clients["elevenlabs"].run_job(job, raw / f"{job.id}.{job.ext}")
                continue
            url = clients["runway"].run_job(job, outputs)
            files[job.id] = downloader(url, raw / f"{job.id}.{job.ext}")
            # Bilder werden als Data-URI weitergereicht (keine Abhängigkeit von ablaufenden URLs)
            outputs[job.id] = ff.image_to_data_uri(files[job.id]) if job.kind == "image" else url
    finally:
        (out_dir / "outputs.json").parent.mkdir(parents=True, exist_ok=True)
        (out_dir / "outputs.json").write_text(json.dumps({k: str(v) for k, v in files.items()}, indent=2),
                                              encoding="utf-8")

    clips = {s["id"]: files[f"VID_{s['id']}"] for s in ep.scenes}
    dia = {d["id"]: files[f"TTS_{d['id']}"] for d in ep.dialogue if f"TTS_{d['id']}" in files}
    sfx = {k[4:]: v for k, v in files.items() if k.startswith("SFX_")}
    w, h, r = _dims(ep, None, None, None)
    tag = f"{plan.profile}_PRIVAT" if private_only else f"{plan.profile}"
    metadata = {"comment": PRIVATE_NOTICE, "copyright": "nicht kommerziell - privater Test"} if private_only else None
    result = assemble(ep, clips, dia, sfx, out_dir, w, h, r, tag=tag,
                      music_audio=files.get("MUSIC_score"), metadata=metadata)
    if private_only:
        (out_dir / "LIZENZ_PRIVATER_TEST.txt").write_text(
            PRIVATE_NOTICE + "\nNicht hochladen, nicht monetarisieren, nicht verbreiten. Für eine Veröffentlichung "
            "das Material mit einem bezahlten ElevenLabs-Tarif neu generieren.\n", encoding="utf-8")
    manifest = {"mode": "paid-render", "profile": plan.profile, "scenes": plan.scenes,
                "audio_provider": plan.audio_provider,
                "license": {"elevenlabs_commercial": not private_only,
                            "usage": "privat, nicht kommerziell" if private_only else "kommerziell (bezahlter Tarif)"},
                "paid_api_calls": len(plan.jobs), "elapsed_s": round(time.time() - started, 1), **result}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest
