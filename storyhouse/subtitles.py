"""Untertitel (SRT als Sidecar, ASS gestylt zum Einbrennen)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from storyhouse.episode import Episode


@dataclass
class Cue:
    start: float
    end: float
    text: str


def build_cues(ep: Episode, spoken: dict[str, float] | None = None) -> list[Cue]:
    """spoken: tatsächliche Sprechdauer je Dialog-ID (aus dem fertigen Ton). Damit bleibt
    der Untertitel so lange stehen, wie wirklich gesprochen wird – nie kürzer als lesbar."""
    style = ep.subtitle_style
    min_disp = float(style.get("min_display_s", 1.0))
    cps = float(style.get("chars_per_second", 15))
    # Zeilen mit subtitle: false (z. B. Echo-Stimmen hinter den Türen) werden gesprochen, aber nicht untertitelt.
    lines = [line for line in ep.dialogue if line.get("subtitle", True)]
    cues: list[Cue] = []
    for i, line in enumerate(lines):
        start = float(line["at"])
        text = str(line["text"]).strip()
        wanted = max(min_disp, len(text) / cps + 0.4)
        if spoken and line["id"] in spoken:
            wanted = max(wanted, float(spoken[line["id"]]) + 0.25)
        hard_end = float(lines[i + 1]["at"]) - 0.05 if i + 1 < len(lines) else ep.duration
        end = min(start + wanted, hard_end, ep.duration)
        if line.get("loop_bridge"):
            end = ep.duration  # Satz hängt bis zum letzten Frame (Loop-Naht)
        cues.append(Cue(start=start, end=round(end, 3), text=text))
    return cues


def _ts_srt(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ts_ass(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def to_srt(cues: list[Cue]) -> str:
    blocks = [f"{i}\n{_ts_srt(c.start)} --> {_ts_srt(c.end)}\n{c.text}\n" for i, c in enumerate(cues, 1)]
    return "\n".join(blocks)


def to_ass(cues: list[Cue], ep: Episode) -> str:
    st = ep.subtitle_style
    w, h = ep.fmt["width"], ep.fmt["height"]
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{st.get('font', 'Inter Display')},{st.get('font_size', 64)},{st.get('primary_colour', '&H00FFFFFF')},&H000000FF,{st.get('outline_colour', '&H00000000')},&H64000000,-1,0,0,0,100,100,0,0,1,{st.get('outline', 4)},{st.get('shadow', 0)},2,{st.get('margin_lr', 140)},{st.get('margin_lr', 140)},{st.get('margin_v', 560)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = [
        f"Dialogue: 0,{_ts_ass(c.start)},{_ts_ass(c.end)},Default,,0,0,0,,{c.text.replace(chr(10), r'\N')}"
        for c in cues
    ]
    events += overlay_events(ep)
    return header + "\n".join(events) + "\n"


# Texteinblendungen im Bild: Aussehen je Stil (ASS-Tags). Farben sind &HBBGGRR&.
_OVERLAY_LOOK = {
    "uhr":       {"size": 0.085, "tags": r"\b1\bord3\shad0\blur0.6\c&HFFFFFF&\3c&H000000&"},
    "nachricht": {"size": 0.040, "tags": r"\b1\bord18\shad0\c&HFFFFFF&\3c&H2A1F1C&\3a&H18&"},
    "anzeige":   {"size": 0.200, "tags": r"\b1\bord2\shad0\blur5\c&H2020FF&\3c&H1010B0&"},
    "titel":     {"size": 0.070, "tags": r"\b1\bord4\shad0\c&HFFFFFF&\3c&H000000&"},
}


def overlay_events(ep: Episode) -> list[str]:
    """Uhrzeit, Handy-Nachrichten, Etagenanzeige: als ASS-Ereignisse über dem Bild (werden mit eingebrannt).
    Sie stehen NICHT in der SRT-Datei – die enthält nur gesprochenen Dialog."""
    w, h = ep.fmt["width"], ep.fmt["height"]
    out = []
    for o in ep.overlays:
        look = _OVERLAY_LOOK[o.get("style", "nachricht")]
        size = int(round(w * float(o.get("size", look["size"]))))
        x, y = int(round(w * float(o.get("x", 0.5)))), int(round(h * float(o.get("y", 0.5))))
        fade = int(round(1000 * float(o.get("fade_s", 0.15))))
        text = str(o["text"]).strip().replace("\n", r"\N")
        out.append(f"Dialogue: 1,{_ts_ass(float(o['from']))},{_ts_ass(float(o['to']))},Default,,0,0,0,,"
                   f"{{\\an5\\pos({x},{y})\\fad({fade},{fade})\\fs{size}{look['tags']}}}{text}")
    return out


def write_subtitles(ep: Episode, out_dir: Path, spoken: dict[str, float] | None = None) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cues = build_cues(ep, spoken)
    srt = out_dir / f"{ep.slug}.srt"
    ass = out_dir / f"{ep.slug}.ass"
    srt.write_text(to_srt(cues), encoding="utf-8")
    ass.write_text(to_ass(cues, ep), encoding="utf-8")
    return srt, ass
