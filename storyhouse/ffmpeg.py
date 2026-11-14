"""FFmpeg-Bausteine: Platzhalter, Normalisierung, Schnitt, Mischung, Export."""
from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

BOLD_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/inter/InterDisplay-Bold.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/opentype/inter/InterDisplay-Regular.otf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
]

GRADE_FILTER = (
    "eq=contrast=1.06:saturation=0.88:gamma=0.97,"
    "colorbalance=rs=-0.03:gs=0.0:bs=0.04:rh=0.03:bh=-0.02,"
    "vignette=PI/5,"
    "noise=alls=5:allf=t"
)


class FFmpegError(RuntimeError):
    pass


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise FFmpegError(f"{tool} nicht gefunden – bitte installieren")


def run(cmd: Sequence[str]) -> None:
    proc = subprocess.run(list(cmd), capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-25:])
        raise FFmpegError(f"FFmpeg fehlgeschlagen ({proc.returncode}):\n{' '.join(cmd)}\n{tail}")


def find_font() -> str | None:
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return f
    return None


def _font_opt(bold: bool = False) -> str:
    f = next((c for c in BOLD_FONT_CANDIDATES if Path(c).exists()), None) if bold else None
    f = f or find_font()
    return f"fontfile='{f}'" if f else "font='Sans'"


def _escape_filter_path(p: Path) -> str:
    # Für Filter-Argumente (ass=, textfile=) Sonderzeichen maskieren.
    return str(p).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


# ---------------------------------------------------------------------
# Platzhalter (Dry-Run, kostenlos)
# ---------------------------------------------------------------------
def placeholder_clip(scene: dict, out: Path, width: int, height: int, fps: int) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    txt = out.with_suffix(".txt")
    body = textwrap.fill(" ".join(scene["motion_prompt"].split()), width=34)
    txt.write_text(
        f"DRY-RUN  ·  KEIN KOSTENPFLICHTIGER RENDER\n\n"
        f"{scene['id']} – {scene['name']}\n"
        f"{scene['start']:.1f}s – {scene['start'] + scene['duration']:.1f}s\n\n"
        f"{textwrap.fill(scene['shot'], width=34)}\n\n{body}\n",
        encoding="utf-8",
    )
    fs = max(12, height // 48)
    dur = int(scene["render_duration"])
    vf = (
        f"noise=alls=18:allf=t,vignette=PI/4,"
        f"drawtext={_font_opt()}:textfile='{_escape_filter_path(txt)}':fontcolor=white@0.92:"
        f"fontsize={fs}:line_spacing={fs // 3}:x=(w-text_w)/2:y=h*0.18:box=1:boxcolor=black@0.45:boxborderw={fs // 2},"
        f"drawtext={_font_opt()}:text='%{{pts\\:hms\\:{float(scene['start']):.3f}}}':fontcolor=white@0.7:fontsize={fs}:x=w*0.06:y=h*0.06"
    )
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=0x0d1a1e:s={width}x{height}:r={fps}:d={dur}",
        "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
        str(out),
    ])
    return out


def placeholder_tone(out: Path, duration: float, freq: int = 440) -> Path:
    """Ersetzt eine Stimme im Dry-Run: kurzer modulierter Ton in Satzlänge."""
    out.parent.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration:.2f}:sample_rate=48000",
        "-af", "tremolo=f=6:d=0.7,afade=t=in:d=0.03,afade=t=out:st={:.2f}:d=0.05,volume=-8dB".format(
            max(0.0, duration - 0.05)),
        "-c:a", "pcm_s16le", str(out),
    ])
    return out


def placeholder_noise(out: Path, duration: float, color: str = "pink") -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"anoisesrc=color={color}:duration={duration:.2f}:sample_rate=48000:amplitude=0.25",
        "-af", "lowpass=f=2500,afade=t=in:d=0.02", "-c:a", "pcm_s16le", str(out),
    ])
    return out


def placeholder_drone(out: Path, duration: float) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency=55:duration={duration:.2f}:sample_rate=48000",
        "-f", "lavfi", "-i", f"sine=frequency=82.5:duration={duration:.2f}:sample_rate=48000",
        "-filter_complex", "[0][1]amix=inputs=2:normalize=0,volume=-6dB",
        "-c:a", "pcm_s16le", str(out),
    ])
    return out


def placeholder_music(out: Path, duration: float) -> Path:
    """Ersetzt den Score im Dry-Run: leiser Moll-Akkord mit langsamem Tremolo."""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for f in (110.0, 130.81, 164.81):
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={f}:duration={duration:.2f}:sample_rate=48000"]
    cmd += ["-filter_complex", "[0][1][2]amix=inputs=3:normalize=0,tremolo=f=0.5:d=0.6,volume=-14dB",
            "-c:a", "pcm_s16le", str(out)]
    run(cmd)
    return out


# ---------------------------------------------------------------------
# Outro (StoryHouse-Karte, kostenlos)
# ---------------------------------------------------------------------
def outro_clip(outro: dict, out: Path, width: int, height: int, fps: int) -> Path:
    """Schwarze Karte mit Titel/Untertitel/Tagline, weich ein- und ausgeblendet."""
    out.parent.mkdir(parents=True, exist_ok=True)
    dur = float(outro["duration_s"])
    fade = min(float(outro.get("fade_s", 0.4)), dur / 3)
    colour = str(outro.get("text_colour", "0xf2f2f2"))
    rows = [  # (Text, Schriftgröße relativ zur Breite, y relativ zur Höhe, fett, Deckkraft)
        (str(outro.get("title", "")), 0.105, 0.455, True, 1.0),
        (str(outro.get("subtitle", "")), 0.042, 0.535, False, 0.85),
        (str(outro.get("tagline", "")), 0.026, 0.60, False, 0.55),
    ]
    filters = []
    for i, (text, rel_size, rel_y, bold, alpha) in enumerate(rows):
        if not text.strip():
            continue
        txt = out.with_name(f"{out.stem}_{i}.txt")
        txt.write_text(text, encoding="utf-8")  # textfile: keine Escaping-Probleme
        filters.append(
            f"drawtext={_font_opt(bold)}:textfile='{_escape_filter_path(txt)}':fontcolor={colour}@{alpha}:"
            f"fontsize={max(8, int(width * rel_size))}:x=(w-text_w)/2:y=h*{rel_y}-text_h/2"
        )
    filters += [f"fade=t=in:st=0:d={fade:.3f}", f"fade=t=out:st={dur - fade:.3f}:d={fade:.3f}", "format=yuv420p"]
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c={outro.get('background', '0x000000')}:s={width}x{height}:r={fps}:d={dur:.3f}",
        "-vf", ",".join(filters), "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p",
        str(out),
    ])
    return out


def outro_audio(sting: Path | None, out: Path, duration: float, gain_db: float = -10.0) -> Path:
    """Tonspur des Outros in exakter Länge: Sting (oder Stille), am Ende ausgeblendet."""
    out.parent.mkdir(parents=True, exist_ok=True)
    fade = min(0.6, duration / 3)
    tail = f"apad=whole_dur={duration:.3f},atrim=0:{duration:.3f},afade=t=out:st={duration - fade:.3f}:d={fade:.3f}"
    if sting and sting.exists():
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(sting), "-af",
               f"aformat=sample_rates=48000:channel_layouts=stereo,volume={gain_db}dB,alimiter=limit=0.89,{tail}"]
    else:
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
               "anullsrc=r=48000:cl=stereo", "-af", tail]
    run(cmd + ["-c:a", "pcm_s16le", str(out)])
    return out


def concat_audio(parts: Sequence[Path], out: Path) -> Path:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for part in parts:
        cmd += ["-i", str(part)]
    graph = "".join(f"[{i}:a]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1[out]"
    run(cmd + ["-filter_complex", graph, "-map", "[out]", "-c:a", "pcm_s16le", "-ar", "48000", str(out)])
    return out


# ---------------------------------------------------------------------
# Dialog automatisch an die Zeitachse anpassen
# ---------------------------------------------------------------------
MAX_TEMPO = 1.25          # schneller klingt unnatürlich -> dann wird zusätzlich gekürzt
_SILENCE = "silenceremove=start_periods=1:start_duration=0.02:start_threshold=-50dB"


def media_duration(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError as exc:
        raise FFmpegError(f"{path}: keine lesbare Audiodatei ({proc.stderr.strip()[-200:]})") from exc


def fit_dialogue(src: Path, dst: Path, slot_s: float) -> dict:
    """Schneidet Stille am Anfang/Ende ab und passt die Zeile in ihr Zeitfenster ein.

    Passt sie nicht, wird sie (tonhöhenneutral) bis MAX_TEMPO beschleunigt; reicht
    auch das nicht, zusätzlich am Ende weich gekürzt. Rückgabe: Messwerte für den
    Sync-Bericht.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    trimmed = dst.with_name(dst.stem + "_trim.wav")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-af",
         f"aformat=sample_rates=48000:channel_layouts=stereo,{_SILENCE},areverse,{_SILENCE},areverse",
         "-c:a", "pcm_s16le", str(trimmed)])
    raw = media_duration(src)
    spoken = media_duration(trimmed)
    if spoken < 0.05:  # alles als Stille erkannt -> Original behalten
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-af",
             "aformat=sample_rates=48000:channel_layouts=stereo", "-c:a", "pcm_s16le", str(trimmed)])
        spoken = media_duration(trimmed)
    tempo, cut = 1.0, False
    chain: list[str] = []
    if spoken > slot_s + 0.01:
        tempo = min(MAX_TEMPO, spoken / slot_s)
        chain.append(f"atempo={tempo:.4f}")
        if spoken / tempo > slot_s + 0.01:
            cut = True
            chain += [f"atrim=0:{slot_s:.3f}", f"afade=t=out:st={max(0.0, slot_s - 0.08):.3f}:d=0.08"]
    if chain:
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(trimmed), "-af", ",".join(chain),
             "-c:a", "pcm_s16le", str(dst)])
    else:
        trimmed.replace(dst)
    return {"raw_s": round(raw, 3), "spoken_s": round(spoken, 3), "slot_s": round(slot_s, 3),
            "tempo": round(tempo, 3), "cut": cut, "fitted_s": round(media_duration(dst), 3)}


# ---------------------------------------------------------------------
# Video
# ---------------------------------------------------------------------
def normalize_clip(src: Path, dst: Path, duration: float, width: int, height: int, fps: int) -> Path:
    """Auf Zielformat skalieren/croppen, Framerate setzen, exakt auf Szenendauer schneiden."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={width}:{height},fps={fps},setsar=1,format=yuv420p"
    )
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
        "-vf", vf, "-t", f"{duration:.3f}", "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", str(dst),
    ])
    return dst


def concat_clips(clips: Sequence[Path], dst: Path) -> Path:
    lst = dst.with_suffix(".txt")
    lst.write_text("".join(f"file '{c.resolve().as_posix()}'\n" for c in clips), encoding="utf-8")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(lst), "-c", "copy", str(dst)])
    return dst


def loop_seam(picture: Path, dst: Path, duration: float, seam_s: float, fps: int = 24) -> Path:
    """Nahtloser Loop ohne Modellunterstützung: Die letzten seam_s Sekunden blenden
    über eine Standbild-Ebene in exakt den ersten Frame des Films über.
    Ergebnis: letzter Frame == erster Frame."""
    first = dst.with_name(dst.stem + "_first.png")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(picture),
         "-frames:v", "1", str(first)])
    start = max(0.0, duration - seam_s)
    fade_d = max(0.05, seam_s - 2.0 / fps)  # Überblendung endet 2 Frames vor Schluss -> volle Deckung
    fc = (
        f"[1:v]format=rgba,fade=t=in:st={start:.3f}:d={fade_d:.3f}:alpha=1[still];"
        f"[0:v][still]overlay=shortest=1:format=auto,format=yuv420p[v]"
    )
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(picture),
         "-loop", "1", "-t", f"{duration:.3f}", "-framerate", str(fps), "-i", str(first),
         "-filter_complex", fc, "-map", "[v]", "-t", f"{duration:.3f}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", str(dst)])
    return dst


def image_to_data_uri(src: Path, max_bytes: int = 3_500_000) -> str:
    """Bild als JPEG-Data-URI (Runway erlaubt Data-URIs bis 5 MB).
    Unabhängig von ablaufenden Runway-Output-URLs."""
    import base64

    for q in (2, 4, 6, 9, 13):
        jpg = src.with_suffix(f".q{q}.jpg")
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
             "-frames:v", "1", "-q:v", str(q), str(jpg)])
        data = jpg.read_bytes()
        if len(data) <= max_bytes:
            return "data:image/jpeg;base64," + base64.b64encode(data).decode()
    raise FFmpegError(f"{src}: Bild lässt sich nicht unter {max_bytes} Bytes komprimieren")


# ---------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------
@dataclass
class Track:
    path: Path
    at: float
    gain_db: float = 0.0
    role: str = "bed"           # "dialogue" | "bed"
    fx: str | None = None
    fill: bool = False          # bis Filmende wiederholen (Drones)
    edge_fade: float = 0.0      # Ein-/Ausblenden NUR dieser Spur (Musik an der Loop-Naht)


def build_mix_command(tracks: Sequence[Track], duration: float, out: Path,
                      silence_windows: Sequence[tuple[float, float]] = (),
                      loudness: float = -14.0, true_peak: float = -1.0) -> list[str]:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    parts: list[str] = []
    dia, bed = [], []
    for i, t in enumerate(tracks):
        if t.fill:
            cmd += ["-stream_loop", "-1"]
        cmd += ["-i", str(t.path)]
        delay = int(round(t.at * 1000))
        chain = ["aformat=sample_rates=48000:channel_layouts=stereo"]
        if t.fx:
            chain.append(t.fx)
        if t.fill:
            chain.append(f"atrim=0:{max(0.0, duration - t.at):.3f}")
        if t.edge_fade > 0:
            span = max(0.0, duration - t.at)
            chain += [f"apad=whole_dur={span:.3f}", f"atrim=0:{span:.3f}", f"afade=t=in:d={t.edge_fade:.3f}",
                      f"afade=t=out:st={max(0.0, span - t.edge_fade):.3f}:d={t.edge_fade:.3f}"]
        chain += [f"volume={t.gain_db}dB", f"adelay={delay}|{delay}"]
        label = f"a{i}"
        parts.append(f"[{i}:a]{','.join(chain)}[{label}]")
        (dia if t.role == "dialogue" else bed).append(label)

    def mix(labels: list[str], name: str) -> None:
        if len(labels) == 1:
            parts.append(f"[{labels[0]}]anull[{name}]")
        else:
            parts.append(f"{''.join(f'[{l}]' for l in labels)}amix=inputs={len(labels)}:normalize=0:dropout_transition=0[{name}]")

    if dia:
        mix(dia, "dia")
    if bed:
        mix(bed, "bed")
    if dia and bed:
        parts.append("[dia]asplit=2[dia_sc][dia_mix]")
        parts.append("[bed][dia_sc]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[bed_duck]")
        parts.append("[bed_duck][dia_mix]amix=inputs=2:normalize=0:dropout_transition=0[pre]")
    elif dia or bed:
        parts.append(f"[{'dia' if dia else 'bed'}]anull[pre]")
    else:
        cmd += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={duration}"]
        parts.append(f"[{len(tracks)}:a]anull[pre]")

    post = [f"apad=whole_dur={duration:.3f}", f"atrim=0:{duration:.3f}"]
    for a, b in silence_windows:
        post.append(f"volume=enable='between(t,{a:.3f},{b:.3f})':volume=0")
    post += [f"loudnorm=I={loudness}:TP={true_peak}:LRA=11", "aresample=48000"]
    parts.append(f"[pre]{','.join(post)}[out]")

    cmd += ["-filter_complex", ";".join(parts), "-map", "[out]", "-c:a", "pcm_s16le", str(out)]
    return cmd


# ---------------------------------------------------------------------
# Finaler Export
# ---------------------------------------------------------------------
def finalize(video: Path, audio: Path, ass: Path | None, out: Path, duration: float, grade: bool = True,
             outro: Path | None = None, outro_duration: float = 0.0,
             metadata: dict[str, str] | None = None) -> Path:
    """Grading + eingebrannte Untertitel + Ton in EINEM Encode; optional Outro-Karte angehängt.

    video/ass decken den Film ab (duration); audio muss duration + outro_duration lang sein.
    """
    filters = [GRADE_FILTER] if grade else []
    if ass is not None:
        filters.append(f"ass='{_escape_filter_path(ass)}'")
    filters.append("format=yuv420p")
    total = duration + (outro_duration if outro else 0.0)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-i", str(audio)]
    if outro:
        graph = (
            f"[0:v]trim=duration={duration:.3f},setpts=PTS-STARTPTS,{','.join(filters)},setsar=1[film];"
            f"[2:v]trim=duration={outro_duration:.3f},setpts=PTS-STARTPTS,format=yuv420p,setsar=1[outro];"
            f"[film][outro]concat=n=2:v=1:a=0[v]"
        )
        cmd += ["-i", str(outro), "-filter_complex", graph, "-map", "[v]", "-map", "1:a:0"]
    else:
        cmd += ["-vf", ",".join(filters), "-map", "0:v:0", "-map", "1:a:0"]
    run(cmd + [
        "-t", f"{total:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-profile:v", "high",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        *[arg for k, v in (metadata or {}).items() for arg in ("-metadata", f"{k}={v}")],
        "-movflags", "+faststart", str(out),
    ])
    return out


def probe(path: Path) -> dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(proc.stdout)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), {})
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), {})
    num, den = (v.get("r_frame_rate", "0/1").split("/") + ["1"])[:2]
    return {
        "video_duration": float(v["duration"]) if v.get("duration") else None,
        "audio_duration": float(a["duration"]) if a.get("duration") else None,
        "width": v.get("width"),
        "height": v.get("height"),
        "fps": round(float(num) / float(den or 1), 3) if v else None,
        "duration": float(data["format"]["duration"]),
        "video_codec": v.get("codec_name"),
        "audio_codec": a.get("codec_name"),
        "audio_rate": int(a["sample_rate"]) if a else None,
    }
