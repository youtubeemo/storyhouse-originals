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


def build_cues(ep: Episode) -> list[Cue]:
    style = ep.subtitle_style
    min_disp = float(style.get("min_display_s", 1.0))
    cps = float(style.get("chars_per_second", 15))
    lines = ep.dialogue
    cues: list[Cue] = []
    for i, line in enumerate(lines):
        start = float(line["at"])
        text = str(line["text"]).strip()
        wanted = max(min_disp, len(text) / cps + 0.4)
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
    return header + "\n".join(events) + "\n"


def write_subtitles(ep: Episode, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cues = build_cues(ep)
    srt = out_dir / f"{ep.slug}.srt"
    ass = out_dir / f"{ep.slug}.ass"
    srt.write_text(to_srt(cues), encoding="utf-8")
    ass.write_text(to_ass(cues, ep), encoding="utf-8")
    return srt, ass
