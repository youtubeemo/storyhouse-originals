"""Storyboard und sekundengenauer Bild-/Tonplan – erzeugt aus episode.yaml.

Die Episode bleibt die einzige Quelle: Dieses Dokument wird nie von Hand gepflegt, sondern mit
``python -m storyhouse storyboard --episode … --out …/STORYBOARD.md`` neu geschrieben. Ein Test prüft,
dass die Datei im Repo zum aktuellen Stand der Episode passt – Drehbuch, Plan und Render können so
nicht auseinanderlaufen.
"""
from __future__ import annotations

from storyhouse.episode import Episode, speech_estimate_s


def tc(t: float) -> str:
    """Sekunden -> m:ss.s"""
    m, s = divmod(round(float(t), 1), 60)
    return f"{int(m)}:{s:04.1f}"


def _cell(text: object) -> str:
    return " ".join(str(text or "").split()).replace("|", "/")


def _transition(scene: dict) -> str:
    parts = []
    if float(scene.get("fade_in", 0) or 0) > 0:
        parts.append(f"Aufblende {float(scene['fade_in']):g} s")
    parts.append(_cell(scene.get("transition") or "harter Schnitt"))
    if float(scene.get("fade_out", 0) or 0) > 0:
        parts.append(f"Abblende {float(scene['fade_out']):g} s")
    return " · ".join(parts)


def _events(ep: Episode) -> list[tuple[float, float | None, str, str]]:
    """Alle Ton- und Texteinsätze: (Start, Ende, Spur, Beschreibung)."""
    ev: list[tuple[float, float | None, str, str]] = []
    for d in ep.dialogue:
        who = ep.characters[d["character"]]["name"]
        at = float(d["at"])
        extra = " (läuft über die nächste Zeile)" if d.get("allow_overlap") else ""
        ev.append((at, round(at + speech_estimate_s(d), 1), "Dialog", f"{d['id']} {who}: „{_cell(d['text'])}“{extra}"))
    for s in ep.sound.get("drones", []):
        at = float(s.get("at", 0.0))
        end = float(s["until"]) if s.get("until") is not None else ep.duration
        fades = []
        if s.get("fade_in"):
            fades.append(f"ein {float(s['fade_in']):g} s")
        if s.get("fade_out"):
            fades.append(f"aus {float(s['fade_out']):g} s")
        note = f" · Blende {', '.join(fades)}" if fades else ""
        ev.append((at, end, "Musik/Atmo", f"{s['id']} ({float(s['gain_db']):g} dB{note}) – {_cell(s.get('label') or s['prompt'])}"))
    for s in ep.sound.get("sfx", []):
        at = float(s["at"])
        ev.append((at, round(at + float(s["duration"]), 1), "Geräusch",
                   f"{s['id']} ({float(s['gain_db']):g} dB) – {_cell(s.get('label') or s['prompt'])}"))
    for w in ep.sound.get("silence_windows", []):
        ev.append((float(w["from"]), float(w["to"]), "STILLE", _cell(w.get("label") or "alles stumm")))
    for o in ep.overlays:
        ev.append((float(o["from"]), float(o["to"]), "Einblendung",
                   f"[{o.get('style', 'nachricht')}] {_cell(str(o['text']).replace(chr(10), ' / '))}"))
    return sorted(ev, key=lambda e: (e[0], e[2]))


def render(ep: Episode) -> str:
    d = ep.data
    out = [
        f"# {d.get('series', '')} – Episode {d.get('episode', '')}: {d.get('title', '')}",
        "",
        "**Storyboard und sekundengenauer Bild-/Tonplan.** Automatisch erzeugt aus `episode.yaml` "
        "(`python -m storyhouse storyboard`) – nicht von Hand ändern.",
        "",
        f"Film {tc(ep.duration)} min ({ep.duration:g} s) · Abspann/Entscheidungskarte {ep.outro_duration:g} s · "
        f"gesamt {tc(ep.total_duration)} min · {ep.fmt['width']}×{ep.fmt['height']} · {ep.fmt['fps']} fps · "
        f"{len(ep.scenes)} Einstellungen · {len(ep.dialogue)} Dialogzeilen",
        "",
    ]
    acts: list[str] = []
    for s in ep.scenes:
        if s.get("act") and s["act"] not in acts:
            acts.append(s["act"])
    for act in acts or [None]:
        scenes = [s for s in ep.scenes if s.get("act") == act] if act else ep.scenes
        a, b = float(scenes[0]["start"]), float(scenes[-1]["start"]) + float(scenes[-1]["duration"])
        out += [f"## {act or 'Einstellungen'} ({tc(a)} – {tc(b)})", "",
                "| Nr. | Zeit | Dauer | Ort | Einstellung | Kamera | Handlung | Übergang |",
                "|---|---|---:|---|---|---|---|---|"]
        for s in scenes:
            start = float(s["start"])
            out.append(f"| **{s['id']}** {_cell(s.get('name'))} | {tc(start)}–{tc(start + float(s['duration']))} | "
                       f"{float(s['duration']):g} s | {_cell(s.get('location'))} | {_cell(s.get('shot'))} | "
                       f"{_cell(s.get('camera'))} | {_cell(s.get('action'))} | {_transition(s)} |")
        out.append("")
        out += ["| Zeit | bis | Spur | Einsatz |", "|---|---|---|---|"]
        for at, end, track, text in _events(ep):
            if a <= at < b:
                out.append(f"| {tc(at)} | {tc(end) if end is not None else ''} | {track} | {text} |")
        out.append("")
    if ep.outro:
        o = ep.outro
        out += [f"## Abspann / Entscheidungskarte ({tc(ep.duration)} – {tc(ep.total_duration)})", ""]
        rows = o.get("rows") or [{"text": o.get(k), "appear_at": 0} for k in ("title", "subtitle", "tagline") if o.get(k)]
        out += ["| Einsatz | Text |", "|---|---|"]
        out += [f"| {tc(ep.duration + float(r.get('appear_at', 0)))} | {_cell(r['text'])} |" for r in rows]
        if o.get("sting"):
            out += ["", f"Ton: {_cell(o['sting']['prompt'])} ({float(o['sting'].get('gain_db', -10)):g} dB)"]
        out.append("")
    out += ["## Bild-Prompts (für die Bild-KI, englisch)", "",
            f"**Einheitlicher Look (an jeden Keyframe angehängt):** {ep.global_style}", ""]
    for s in ep.scenes:
        out += [f"**{s['id']} – {_cell(s.get('name'))}**" + (f" · Anschluss an {s['match_previous']}" if s.get("match_previous") else ""),
                f"- Standbild: {_cell(s['keyframe_prompt'])}",
                f"- Bewegung: {_cell(s['motion_prompt'])}", ""]
    return "\n".join(out).rstrip() + "\n"
