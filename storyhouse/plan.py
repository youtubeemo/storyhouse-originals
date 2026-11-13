"""Übersetzt eine Episode in eine Liste konkreter, bepreister API-Jobs.

Ein Plan ist reine Datenstruktur – er sendet nichts. Platzhalter der Form
``{{out:JOB_ID}}`` werden erst zur Laufzeit durch die Ergebnis-URL des
jeweiligen Vorgänger-Jobs ersetzt.
"""
from __future__ import annotations

import zlib
from dataclasses import asdict, dataclass, field
from typing import Any

from storyhouse.budget import BudgetConfig, Pricing
from storyhouse.episode import Episode, _tags_in

IMAGE_RATIO_9x16 = "1080:1920"
# Modelle, die Start- UND End-Keyframe akzeptieren (für den nahtlosen Loop).
# Vor dem ersten bezahlten Render in der Runway-Doku verifizieren.
LAST_FRAME_MODELS = {"gen4.5", "veo3.1", "veo3.1_fast"}

ENDPOINTS = {
    "image": "/v1/text_to_image",
    "video": "/v1/image_to_video",
    "tts": "/v1/text_to_speech",
    "sfx": "/v1/sound_effect",
}


@dataclass
class Job:
    id: str
    kind: str
    model: str
    payload: dict[str, Any]
    credits: float
    scene: str | None = None
    depends: list[str] = field(default_factory=list)
    ext: str = "bin"

    @property
    def endpoint(self) -> str:
        return ENDPOINTS[self.kind]


@dataclass
class Plan:
    episode_slug: str
    profile: str
    scenes: list[str]
    jobs: list[Job]

    @property
    def total_credits(self) -> float:
        return sum(j.credits for j in self.jobs)

    def total_eur(self, pricing: Pricing, cfg: BudgetConfig) -> float:
        return pricing.credits_to_eur(self.total_credits, cfg)

    def by_kind(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for j in self.jobs:
            out[j.kind] = out.get(j.kind, 0.0) + j.credits
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode": self.episode_slug,
            "profile": self.profile,
            "scenes": self.scenes,
            "total_credits": self.total_credits,
            "jobs": [asdict(j) for j in self.jobs],
        }


def _seed(key: str) -> int:
    return zlib.crc32(key.encode()) % 4_294_967_295


def _clean(text: str) -> str:
    return " ".join(str(text).split())


def build_plan(ep: Episode, pricing: Pricing, profile: str = "test", scenes: str | None = "all",
               include_audio: bool = True) -> Plan:
    prof = pricing.profile(profile)
    selected = ep.select_scenes(scenes)
    sel_ids = [s["id"] for s in selected]
    style = ep.global_style
    jobs: list[Job] = []

    # 1) Referenzen (nur benötigte Tags)
    needed_tags: list[str] = []
    for s in selected:
        for t in _tags_in(s["keyframe_prompt"]):
            if t not in needed_tags:
                needed_tags.append(t)
    sources = ep.reference_sources()
    for tag in needed_tags:
        model = prof["reference_image_model"]
        jobs.append(Job(
            id=f"REF_{tag}", kind="image", model=model, ext="png",
            credits=pricing.image_credits(model),
            payload={"model": model, "promptText": _clean(f"{sources[tag]} {style}")[:1000],
                     "ratio": IMAGE_RATIO_9x16, "seed": _seed(f"ref-{tag}")},
        ))

    # 2) Keyframes
    for s in selected:
        model = prof["keyframe_model"]
        tags = list(dict.fromkeys(_tags_in(s["keyframe_prompt"])))
        jobs.append(Job(
            id=f"KF_{s['id']}", kind="image", model=model, scene=s["id"], ext="png",
            credits=pricing.image_credits(model), depends=[f"REF_{t}" for t in tags],
            payload={
                "model": model,
                "promptText": _clean(f"{s['keyframe_prompt']} {style}")[:1000],
                "ratio": IMAGE_RATIO_9x16,
                "referenceImages": [{"uri": f"{{{{out:REF_{t}}}}}", "tag": t} for t in tags],
                "seed": _seed(f"kf-{s['id']}"),
            },
        ))

    # 3) Bewegtbild
    for s in selected:
        model = prof["video_model"]
        prompt_image: Any = f"{{{{out:KF_{s['id']}}}}}"
        depends = [f"KF_{s['id']}"]
        loop_target = (s.get("loop") or {}).get("match_frame_with")
        if loop_target and loop_target in sel_ids and model in LAST_FRAME_MODELS:
            prompt_image = [
                {"uri": f"{{{{out:KF_{s['id']}}}}}", "position": "first"},
                {"uri": f"{{{{out:KF_{loop_target}}}}}", "position": "last"},
            ]
            depends.append(f"KF_{loop_target}")
        jobs.append(Job(
            id=f"VID_{s['id']}", kind="video", model=model, scene=s["id"], ext="mp4",
            credits=pricing.video_credits(model, s["render_duration"]), depends=depends,
            payload={
                "model": model,
                "promptImage": prompt_image,
                "promptText": _clean(s["motion_prompt"])[:1000],
                "ratio": ep.fmt.get("generation_ratio", "720:1280"),
                "duration": int(s["render_duration"]),
                "seed": _seed(f"vid-{s['id']}"),
            },
        ))

    if include_audio:
        # 4) Dialog
        for line in ep.dialogue:
            if line["scene"] not in sel_ids:
                continue
            voice = ep.characters[line["character"]]["voice"]
            model = voice.get("model", prof["tts_model"])
            text = line.get("tts_text") or line["text"]
            jobs.append(Job(
                id=f"TTS_{line['id']}", kind="tts", model=model, scene=line["scene"], ext="mp3",
                credits=pricing.tts_credits(model, text),
                payload={"model": model, "promptText": text,
                         "voice": {"type": "runway-preset", "presetId": voice["preset_id"]}},
            ))
        # 5) Soundeffekte & Drones (nur für gewählte Szenen-Zeitfenster)
        windows = [(float(s["start"]), float(s["start"]) + float(s["duration"])) for s in selected]
        model = prof["sfx_model"]
        for item in list(ep.sound.get("drones", [])) + list(ep.sound.get("sfx", [])):
            at = float(item.get("at", 0.0))
            end = at + float(item["duration"])
            if not any(at < w_end and end > w_start for w_start, w_end in windows):
                continue
            jobs.append(Job(
                id=f"SFX_{item['id']}", kind="sfx", model=model, ext="mp3",
                credits=pricing.sfx_credits(model),
                payload={"model": model, "promptText": _clean(item["prompt"]),
                         "duration": float(item["duration"]), "loop": bool(item.get("loop", False))},
            ))

    return Plan(episode_slug=ep.slug, profile=profile, scenes=sel_ids, jobs=jobs)


def format_estimate(plan: Plan, pricing: Pricing, cfg: BudgetConfig) -> str:
    from storyhouse.budget import with_margin

    eur = plan.total_eur(pricing, cfg)
    lines = [
        f"Kostenschätzung – {plan.episode_slug} | Profil: {plan.profile} | Szenen: {', '.join(plan.scenes)}",
        "",
        "| Jobart | Anzahl | Credits | EUR (konservativ) |",
        "|---|---:|---:|---:|",
    ]
    counts: dict[str, int] = {}
    for j in plan.jobs:
        counts[j.kind] = counts.get(j.kind, 0) + 1
    for kind, cr in plan.by_kind().items():
        lines.append(f"| {kind} | {counts[kind]} | {cr:.0f} | {pricing.credits_to_eur(cr, cfg):.2f} € |")
    lines += [
        f"| **Summe** | {len(plan.jobs)} | **{plan.total_credits:.0f}** | **{eur:.2f} €** |",
        "",
        f"Mit Sicherheitsaufschlag ({cfg.safety_margin:.0%}): **{with_margin(eur, cfg):.2f} €** "
        f"· Lauf-Limit {cfg.max_per_run_eur:.2f} € · Gesamtbudget {cfg.total_budget_eur:.2f} €",
    ]
    return "\n".join(lines)
