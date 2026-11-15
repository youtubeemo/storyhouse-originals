"""Übersetzt eine Episode in eine Liste konkreter, bepreister, spezifikationsgeprüfter API-Jobs.

Ein Plan ist reine Datenstruktur – er sendet nichts. Platzhalter der Form
``{{out:JOB_ID}}`` werden erst zur Laufzeit durch das Ergebnis des Vorgänger-
Jobs ersetzt (bei Bildern: JPEG-Data-URI, siehe pipeline.py).
"""
from __future__ import annotations

import hashlib
import zlib
from dataclasses import asdict, dataclass, field
from typing import Any

from storyhouse import elevenlabs_spec as el_spec
from storyhouse.budget import BudgetConfig, Pricing, with_margin
from storyhouse.episode import AUDIO_PROVIDERS, CONTINUITY_TAG, Episode, _tags_in
from storyhouse.runway_spec import ENDPOINTS, snap_duration, supports_last_frame, validate_payload

IMAGE_RATIO_9x16 = "1080:1920"
LOOP_STRATEGIES = {"ffmpeg_seam", "keyframe_last"}


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
    provider: str = "runway"      # "runway" | "elevenlabs" – Credits sind je Anbieter eigene Einheiten
    path: str | None = None       # nur ElevenLabs: Endpunkt inkl. voice_id

    @property
    def endpoint(self) -> str:
        return self.path or ENDPOINTS[self.kind]

    def validate(self) -> None:
        """Prüft den Payload gegen die Spezifikation des jeweiligen Anbieters."""
        if self.provider == "elevenlabs":
            el_spec.validate_request(self.kind, self.path, self.payload)
        elif self.provider == "runway":
            validate_payload(self.kind, self.payload)
        else:
            raise ValueError(f"{self.id}: unbekannter Anbieter {self.provider}")


@dataclass
class Plan:
    episode_slug: str
    profile: str
    scenes: list[str]
    jobs: list[Job]
    loop_strategy: str = "ffmpeg_seam"
    audio_provider: str | None = None
    notes: list[str] = field(default_factory=list)
    # Abgenommene Referenzbilder aus dem Repo: Platzhalter-Schlüssel -> Dateipfad (kein Job, 0 €)
    frozen_refs: dict[str, str] = field(default_factory=dict)

    @property
    def providers(self) -> list[str]:
        return list(dict.fromkeys(j.provider for j in self.jobs))

    def credits(self, provider: str) -> float:
        """Credits eines Anbieters (Runway- und ElevenLabs-Credits nie addieren)."""
        return sum(j.credits for j in self.jobs if j.provider == provider)

    def total_eur(self, pricing: Pricing, cfg: BudgetConfig) -> float:
        return round(sum(pricing.credits_to_eur(self.credits(p), cfg, p) for p in self.providers), 4)

    def by_kind(self) -> dict[tuple[str, str], float]:
        out: dict[tuple[str, str], float] = {}
        for j in self.jobs:
            out[(j.provider, j.kind)] = out.get((j.provider, j.kind), 0.0) + j.credits
        return out

    def required_secrets(self) -> tuple[str, ...]:
        names = {"runway": "RUNWAYML_API_SECRET", "elevenlabs": "ELEVENLABS_API_KEY"}
        return tuple(names[p] for p in self.providers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode": self.episode_slug,
            "profile": self.profile,
            "scenes": self.scenes,
            "loop_strategy": self.loop_strategy,
            "audio_provider": self.audio_provider,
            "notes": self.notes,
            "frozen_refs": self.frozen_refs,
            "credits": {p: self.credits(p) for p in self.providers},
            "jobs": [asdict(j) for j in self.jobs],
        }


def _seed(key: str) -> int:
    return zlib.crc32(key.encode()) % 4_294_967_295


def _clean(text: str) -> str:
    return " ".join(str(text).split())


def build_plan(ep: Episode, pricing: Pricing, profile: str = "test", scenes: str | None = "all",
               include_audio: bool = True, audio_provider: str | None = None,
               include_music: bool = False, stills_only: bool = False) -> Plan:
    """include_music: Musik-Job (Eleven Music) aufnehmen – nur nach bestätigter Lizenzprüfung.
    stills_only: Bild-Storyboard-Stufe – Referenzen, Keyframes und Ton, aber KEIN Video (der teuerste Posten)."""
    prof = pricing.profile(profile)
    # Episode darf Bild-/Videomodell wählen (episode.yaml: models). Preise, verified-Prüfung, Budget- und
    # Umfangsgrenzen gelten unverändert; das Profil (und damit die Kennzeichnung „privater Test“) bleibt.
    for key, model in (ep.data.get("models") or {}).items():
        if key in ("keyframe_model", "video_model"):
            prof[key] = str(model)
    audio_provider = audio_provider or ep.audio_provider
    if audio_provider not in AUDIO_PROVIDERS:
        raise ValueError(f"Unbekannter Ton-Anbieter {audio_provider}")
    notes: list[str] = []
    selected = ep.select_scenes(scenes)
    sel_ids = [s["id"] for s in selected]
    style = ep.global_style
    jobs: list[Job] = []
    loop_strategy = "ffmpeg_seam"

    def add(job: Job) -> None:
        job.validate()  # nichts verlässt den Plan, was die API ablehnt
        jobs.append(job)

    # 1) Referenzen (nur benötigte Tags)
    needed_tags: list[str] = []
    for s in selected:
        for t in _tags_in(s["keyframe_prompt"]):
            if t not in needed_tags:
                needed_tags.append(t)
    sources = ep.reference_sources()
    frozen: dict[str, str] = {}
    ref_key: dict[str, str] = {}
    for tag, path in ep.frozen_refs().items():
        if tag in needed_tags:
            # Prüfsumme im Schlüssel: Wird die Datei ersetzt, ändern sich Payload und Cache-Schlüssel der Keyframes.
            ref_key[tag] = f"REF_{tag}_{hashlib.sha256(path.read_bytes()).hexdigest()[:8]}"
            frozen[ref_key[tag]] = str(path)
    if frozen:
        notes.append(f"Abgenommene Referenzbilder aus dem Repo (0 €): {', '.join(sorted(t for t in ref_key))}.")
    for tag in needed_tags:
        if tag in ref_key:
            continue
        ref_key[tag] = f"REF_{tag}"
        model = prof["reference_image_model"]
        add(Job(
            id=f"REF_{tag}", kind="image", model=model, ext="png",
            credits=pricing.image_credits(model),
            payload={"model": model, "promptText": _clean(f"{sources[tag]} {style}")[:1000],
                     "ratio": IMAGE_RATIO_9x16, "seed": _seed(f"ref-{tag}")},
        ))

    # 2) Keyframes
    for s in selected:
        model = prof["keyframe_model"]
        tags = list(dict.fromkeys(_tags_in(s["keyframe_prompt"])))
        refs = [{"uri": f"{{{{out:{ref_key[t]}}}}}", "tag": t} for t in tags]
        depends = [ref_key[t] for t in tags if ref_key[t] not in frozen]
        prompt = s["keyframe_prompt"]
        # Anschluss: Der Keyframe der vorherigen Einstellung dient als zusätzliche Referenz, damit Licht,
        # Kostüm, Nässe und Raum von Schnitt zu Schnitt gleich bleiben (nur wenn diese Szene mitgerendert wird).
        prev = s.get("match_previous")
        if prev and prev in sel_ids:
            refs.append({"uri": f"{{{{out:KF_{prev}}}}}", "tag": CONTINUITY_TAG})
            depends.append(f"KF_{prev}")
            prompt = (f"{prompt} Direct continuation of @{CONTINUITY_TAG}: identical lighting, set, costume, "
                      "wetness and character model.")
        add(Job(
            id=f"KF_{s['id']}", kind="image", model=model, scene=s["id"], ext="png",
            credits=pricing.image_credits(model), depends=depends,
            payload={
                "model": model,
                "promptText": _clean(f"{prompt} {style}")[:1000],
                "ratio": IMAGE_RATIO_9x16,
                # Ohne Referenz (reine Detail-Einstellung, z. B. die Anzeige in S20) darf das Feld gar nicht
                # gesendet werden: Runway lehnt eine LEERE Liste mit HTTP 400 ab (Lauf 43758293707).
                **({"referenceImages": refs} if refs else {}),
                "seed": _seed(f"kf-{s['id']}"),
            },
        ))

    # 3) Bewegtbild
    if stills_only:
        notes.append("Bild-Storyboard-Stufe: keine Videojobs – jede Einstellung ist ihr Keyframe als Standbild.")
    for s in ([] if stills_only else selected):
        model = prof["video_model"]
        prompt_image: Any = f"{{{{out:KF_{s['id']}}}}}"
        depends = [f"KF_{s['id']}"]
        loop = s.get("loop") or {}
        target = loop.get("match_frame_with")
        strategy = loop.get("strategy", "ffmpeg_seam")
        if target:
            if strategy not in LOOP_STRATEGIES:
                raise ValueError(f"{s['id']}: unbekannte Loop-Strategie {strategy}")
            loop_strategy = strategy
        if target and strategy == "keyframe_last" and target in sel_ids:
            # gen4.5/gen4_turbo können KEIN End-Keyframe -> Loop-Shot mit einem Modell, das es kann.
            model = prof.get("loop_video_model", model)
            if not supports_last_frame(model):
                raise ValueError(f"{model} unterstützt kein End-Keyframe – loop_video_model anpassen")
            prompt_image = [
                {"uri": f"{{{{out:KF_{s['id']}}}}}", "position": "first"},
                {"uri": f"{{{{out:KF_{target}}}}}", "position": "last"},
            ]
            depends.append(f"KF_{target}")
        duration = snap_duration(model, max(float(s["duration"]), float(s.get("render_duration", 0))))
        payload: dict[str, Any] = {
            "model": model,
            "promptImage": prompt_image,
            "promptText": _clean(s["motion_prompt"])[:1000],
            "ratio": ep.fmt.get("generation_ratio", "720:1280"),
            "duration": duration,
            "seed": _seed(f"vid-{s['id']}"),
        }
        if model.startswith("veo"):
            payload["audio"] = False  # Standard wäre true (doppelter Preis); Ton machen wir selbst
        add(Job(id=f"VID_{s['id']}", kind="video", model=model, scene=s["id"], ext="mp4",
                credits=pricing.video_credits(model, duration), depends=depends, payload=payload))

    if include_audio:
        # Zeitfenster der gewählten Szenen; Outro-Sting gehört immer dazu
        windows = [(float(s["start"]), float(s["start"]) + float(s["duration"])) for s in selected]
        sounds = []
        for item in list(ep.sound.get("drones", [])) + list(ep.sound.get("sfx", [])):
            at = float(item.get("at", 0.0))
            end = float(item["until"]) if item.get("until") is not None else at + float(item["duration"])
            if item.get("repeat_to_fill") and item.get("until") is None:
                end = ep.duration
            if any(at < w_end and end > w_start for w_start, w_end in windows):
                sounds.append(item)
        sting = (ep.outro or {}).get("sting")
        if sting:
            sounds.append({"id": "outro_sting", "prompt": sting["prompt"], "duration": sting["duration"]})
        lines = [line for line in ep.dialogue if line["scene"] in sel_ids]

        if audio_provider == "elevenlabs":
            # 4) Dialog – ElevenLabs Text to Speech
            for line in lines:
                voice = ep.characters[line["character"]]["voice"]
                model = voice.get("model") or pricing.elevenlabs_model("tts")
                text = line.get("tts_text") or line["text"]
                body: dict[str, Any] = {"text": text, "model_id": model,
                                        "voice_settings": {"stability": float(voice.get("stability", 0.5))},
                                        "seed": _seed(f"tts-{line['id']}")}
                if el_spec.TTS_MODELS.get(model, {}).get("language_code"):
                    body["language_code"] = ep.language  # früher fest "en" – deutsche Zeilen klangen englisch gefärbt
                add(Job(id=f"TTS_{line['id']}", kind="tts", model=model, scene=line["scene"], ext="mp3",
                        provider="elevenlabs", path=el_spec.tts_path(str(voice.get("voice_id", ""))),
                        credits=pricing.el_tts_credits(model, text), payload=body))
            # 5) Soundeffekte & Drones – ElevenLabs Sound Effects (Dauer immer fest)
            model = pricing.elevenlabs_model("sfx")
            for item in sounds:
                add(Job(
                    id=f"SFX_{item['id']}", kind="sfx", model=model, ext="mp3",
                    provider="elevenlabs", path=el_spec.SFX_PATH,
                    credits=pricing.el_sfx_credits(model, float(item["duration"])),
                    payload={"text": _clean(item["prompt"]), "model_id": model,
                             "duration_seconds": float(item["duration"]), "loop": bool(item.get("loop", False)),
                             "prompt_influence": float(item.get("prompt_influence", 0.3))},
                ))
            # 6) Musik – Eleven Music (nur nach bestätigter Lizenzprüfung)
            gen = ep.music_generate
            if gen and include_music:
                model = pricing.elevenlabs_model("music")
                length_ms = max(3000, int(round(sum(float(s["duration"]) for s in selected) * 1000)))
                add(Job(
                    id="MUSIC_score", kind="music", model=model, ext="mp3",
                    provider="elevenlabs", path=el_spec.MUSIC_PATH,
                    credits=pricing.el_music_credits(model, length_ms / 1000.0),
                    payload={"prompt": _clean(gen["prompt"]), "model_id": model,
                             "music_length_ms": length_ms, "force_instrumental": True},
                ))
            elif gen:
                notes.append("Musik (Eleven Music) nicht im Plan: erst nach Lizenzprüfung "
                             "(config/budget.yaml elevenlabs.music_license_confirmed) – Drone dient als Musikbett.")
        else:
            # Ausweichweg: Ton über Runways Proxy-Endpunkte
            for line in lines:
                voice = ep.characters[line["character"]]["voice"]
                model = voice.get("model", prof["tts_model"])
                text = line.get("tts_text") or line["text"]
                payload = {"model": model, "promptText": text,
                           "voice": {"type": "runway-preset", "presetId": voice["preset_id"]}}
                if model == "eleven_v3":
                    payload["languageCode"] = ep.language
                add(Job(id=f"TTS_{line['id']}", kind="tts", model=model, scene=line["scene"], ext="mp3",
                        credits=pricing.tts_credits(model, text), payload=payload))
            model = prof["sfx_model"]
            for item in sounds:
                add(Job(
                    id=f"SFX_{item['id']}", kind="sfx", model=model, ext="mp3",
                    credits=pricing.sfx_credits(model, float(item["duration"])),
                    payload={"model": model, "promptText": _clean(item["prompt"]),
                             "duration": float(item["duration"]), "loop": bool(item.get("loop", False))},
                ))
            if ep.music_generate:
                notes.append("Musik: über Runway nicht bepreist/gesperrt – Drone dient als Musikbett.")

    return Plan(episode_slug=ep.slug, profile=profile, scenes=sel_ids, jobs=jobs, loop_strategy=loop_strategy,
                audio_provider=audio_provider if include_audio else None, notes=notes, frozen_refs=frozen)


def format_estimate(plan: Plan, pricing: Pricing, cfg: BudgetConfig) -> str:
    eur = plan.total_eur(pricing, cfg)
    lines = [
        f"Kostenschätzung – {plan.episode_slug} | Profil: {plan.profile} | Szenen: {', '.join(plan.scenes)} "
        f"| Loop: {plan.loop_strategy} | Ton: {plan.audio_provider or 'ohne'}",
        "",
        "| Anbieter | Jobart | Anzahl | Credits (anbietereigen) | EUR (konservativ, inkl. USt.) |",
        "|---|---|---:|---:|---:|",
    ]
    counts: dict[tuple[str, str], int] = {}
    for j in plan.jobs:
        counts[(j.provider, j.kind)] = counts.get((j.provider, j.kind), 0) + 1
    for (provider, kind), cr in plan.by_kind().items():
        lines.append(f"| {provider} | {kind} | {counts[(provider, kind)]} | {cr:.0f} | "
                     f"{pricing.credits_to_eur(cr, cfg, provider):.2f} € |")
    for provider in plan.providers:
        lines.append(f"| **{provider} gesamt** | | | **{plan.credits(provider):.0f}** | "
                     f"**{pricing.credits_to_eur(plan.credits(provider), cfg, provider):.2f} €** |")
    lines += [
        f"| **Summe** | | {len(plan.jobs)} | | **{eur:.2f} €** |",
        "",
        f"Mit Sicherheitsaufschlag ({cfg.safety_margin:.0%}): **{with_margin(eur, cfg):.2f} €** "
        f"· Lauf-Limit {cfg.max_per_run_eur:.2f} € · Gesamtbudget {cfg.total_budget_eur:.2f} € "
        f"· Annahmen: 1 USD = {cfg.eur_per_usd:.2f} €, USt. {cfg.vat_rate:.0%}, Preise geprüft am "
        f"{pricing.verified_on()}",
    ]
    if "elevenlabs" in plan.providers:
        lines.append("ElevenLabs-Credits werden aus dem Kontingent eures Kontos verbraucht; die Abo-Gebühr "
                     "selbst ist hier nicht enthalten.")
    lines += [f"Hinweis: {n}" for n in plan.notes]
    return "\n".join(lines)
