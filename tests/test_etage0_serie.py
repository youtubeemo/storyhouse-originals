"""ETAGE 0 – S01E01 (Serienfassung, ca. 3 Minuten): Episode, Anschlüsse, Timing, Ton-Mastering, Vorschau.

Alles hier läuft ohne API-Schlüssel und ohne Kosten.
"""
import copy
import subprocess
from pathlib import Path

import pytest

from storyhouse import ffmpeg as ff
from storyhouse.budget import ApprovalError, BudgetConfig, Pricing, require_paid_scope, with_margin
from storyhouse.episode import CONTINUITY_TAG, EpisodeError, load_episode, speech_estimate_s, validate_episode
from storyhouse.plan import build_plan
from storyhouse.storyboard import render
from storyhouse.subtitles import build_cues, to_ass, to_srt

ROOT = Path(__file__).resolve().parent.parent
EP = ROOT / "episodes" / "etage-0" / "s01e01-der-verbotene-knopf"


@pytest.fixture
def ep():
    return load_episode(EP)


def _broken(ep, change):
    data = copy.deepcopy(ep.data)
    change(data)
    bad = type(ep)(path=ep.path, data=data)
    with pytest.raises(EpisodeError) as err:
        validate_episode(bad)
    return str(err.value)


# ----------------------------------------------------------------- Episode
def test_laenge_format_und_schluss(ep):
    assert ep.duration == pytest.approx(168.0)
    assert ep.total_duration == pytest.approx(176.0)
    assert ep.total_duration <= 180  # YouTube Shorts: höchstens 3 Minuten
    assert (ep.fmt["width"], ep.fmt["height"], ep.language) == (1080, 1920, "de")
    assert len(ep.scenes) == 37
    assert [d["text"] for d in ep.dialogue][-2:] == [
        "Aber denk daran …", "… beim letzten Mal hast du die falsche Entscheidung getroffen."]
    rows = [r["text"] for r in ep.outro["rows"]]
    assert rows[:3] == ["WAS SOLL LEON TUN?", "A – Zurück in den Aufzug", "B – Tür 008 öffnen"]
    assert "Schreib A oder B in die Kommentare." in rows and "Deine Entscheidung bestimmt Episode 2." in rows


def test_jede_einstellung_ist_vollstaendig_beschrieben(ep):
    for s in ep.scenes:
        for key in ("act", "location", "shot", "camera", "action", "transition"):
            assert str(s.get(key, "")).strip(), f"{s['id']}: {key} fehlt"
    assert len({s["act"] for s in ep.scenes}) == 6


def test_dialog_passt_ohne_beschleunigen_in_die_zeitfenster(ep):
    slots = ep.dialogue_slots()
    for line in ep.dialogue:
        assert speech_estimate_s(line) <= slots[line["id"]], line["id"]
    msg = _broken(ep, lambda d: d["dialogue"][6].update(text="x" * 200, tts_text="x" * 200))
    assert "D07" in msg and "Sprechzeit" in msg


def test_storyboard_im_repo_ist_aktuell(ep):
    assert (EP / "STORYBOARD.md").read_text(encoding="utf-8") == render(ep), \
        "STORYBOARD.md neu erzeugen: python -m storyhouse storyboard --episode … --out …/STORYBOARD.md"


def test_validierung_faengt_fehler_in_anschluss_referenzen_und_einblendungen(ep):
    assert "frühere Szene" in _broken(ep, lambda d: d["scenes"][1].update(match_previous="S30"))
    assert "höchstens 3 Referenzbilder" in _broken(
        ep, lambda d: d["scenes"][12].update(match_previous="S12"))  # S13 hat schon drei @-Referenzen
    assert "overlays[0]" in _broken(ep, lambda d: d["overlays"][0].update(to=999))
    assert "fade_out" in _broken(ep, lambda d: d["scenes"][0].update(fade_out=5))


# ----------------------------------------------------------------- Plan
def test_plan_deutsch_anschlussbilder_und_requisiten(ep):
    plan = build_plan(ep, Pricing.load(), profile="final")
    jobs = {j.id: j for j in plan.jobs}
    assert all(j.payload["language_code"] == "de" for j in plan.jobs if j.kind == "tts")
    kf = jobs["KF_S02"]
    assert {"uri": "{{out:KF_S01}}", "tag": CONTINUITY_TAG} in kf.payload["referenceImages"]
    assert "KF_S01" in kf.depends and f"@{CONTINUITY_TAG}" in kf.payload["promptText"]
    assert {"REF_kopfhoerer", "REF_tuer008", "REF_leon", "REF_leon_alt"} <= set(jobs)
    for j in plan.jobs:
        assert len(j.payload.get("referenceImages", [])) <= 3
        if j.kind in ("image", "video"):
            assert len(j.payload["promptText"]) <= 1000
    assert all("3D" in j.payload["promptText"] for j in plan.jobs if j.kind == "image")
    # Teil-Render ohne die Vorgänger-Einstellung: kein Verweis auf ein Bild, das es dann nicht gibt
    only = build_plan(ep, Pricing.load(), profile="final", scenes="S02")
    assert all(r["tag"] != CONTINUITY_TAG for j in only.jobs for r in j.payload.get("referenceImages", []))


def test_auch_die_alte_testfolge_spricht_jetzt_deutsch():
    old = load_episode(ROOT / "episodes" / "etage-0" / "ep01-der-verbotene-knopf")
    plan = build_plan(old, Pricing.load(), profile="final")
    assert {j.payload["language_code"] for j in plan.jobs if j.kind == "tts"} == {"de"}


def test_kostenstufen(ep):
    pricing, cfg = Pricing.load(), BudgetConfig.load()
    full = build_plan(ep, pricing, profile="final")
    stills = build_plan(ep, pricing, profile="final", stills_only=True)
    assert not [j for j in stills.jobs if j.kind == "video"]
    assert len([j for j in stills.jobs if j.kind == "image"]) == len([j for j in full.jobs if j.kind == "image"])
    assert with_margin(stills.total_eur(pricing, cfg), cfg) < 11.0
    assert 28.0 < full.total_eur(pricing, cfg) < 36.0
    assert full.credits("elevenlabs") < 30_000  # passt in ein Starter-Monatskontingent
    assert not [j for j in full.jobs if j.kind == "music"]


def test_freigabe_deckt_nur_die_standbild_stufe_bis_10_euro(ep, tmp_path):
    """Bild-Storyboard-Stufe passt in die 10 € inkl. Reserve; der bewegte Film wird verweigert."""
    from storyhouse.budget import BudgetError, Ledger, require_scope_budget

    cfg, pricing = BudgetConfig.load(), Pricing.load()
    assert cfg.scope_budget_eur == 10.0
    ledger = Ledger.load(tmp_path / "l.json")
    stills = build_plan(ep, pricing, profile="final", stills_only=True)
    require_paid_scope(cfg, stills)
    assert with_margin(stills.total_eur(pricing, cfg), cfg) <= 10.0
    require_scope_budget(cfg, ledger, stills.total_eur(pricing, cfg))
    with pytest.raises(BudgetError, match="Kein bezahlter Lauf"):
        require_scope_budget(cfg, ledger, build_plan(ep, pricing, profile="final").total_eur(pricing, cfg))
    old = load_episode(ROOT / "episodes" / "etage-0" / "ep01-der-verbotene-knopf")
    with pytest.raises(ApprovalError, match="etage-0-der-verbotene-knopf"):
        require_paid_scope(cfg, build_plan(old, pricing, profile="final"))


# ----------------------------------------------------------------- Untertitel / Einblendungen
def test_einblendungen_im_bild_aber_nicht_in_der_srt(ep):
    cues = build_cues(ep)
    assert len(cues) == len([d for d in ep.dialogue if d.get("subtitle", True)]) == 22
    ass, srt = to_ass(cues, ep), to_srt(cues)
    assert r"\pos(540,269)" in ass and "02:17" in ass          # Uhrzeit: x 0.5, y 0.14 von 1080×1920
    assert r"DEINE EIGENE NUMMER\NDU HAST ES WIEDER GETAN." in ass
    assert "DU HAST ES WIEDER GETAN" not in srt and "Sprachnachricht" not in srt
    assert ass.count("Leon? Bist du das?") == 1                # Echo-Stimmen werden nicht untertitelt


# ----------------------------------------------------------------- Ton
def _tp_lufs(path):
    return ff.measure_loudness(path)


def test_mastering_haelt_true_peak_auch_nach_aac_und_laesst_stille_still(tmp_path):
    """Der Fehler aus Lauf 43724968662: True Peak -0,4 dBTP im fertigen Film (Grenze -1)."""
    src = tmp_path / "premix.wav"
    # 3 s leises Rauschen, 1 s digitale Stille, 2 s sehr lauter Schlag
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "anoisesrc=color=pink:duration=3:sample_rate=48000:amplitude=0.02",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=1",
                    "-f", "lavfi", "-i", "sine=frequency=60:duration=2:sample_rate=48000",
                    "-filter_complex", "[2:a]volume=0.95[hit];[0:a][1:a][hit]concat=n=3:v=0:a=1,"
                    "aformat=channel_layouts=stereo", "-c:a", "pcm_s16le", str(src)], check=True)
    dst = tmp_path / "mix.wav"
    report = ff.master_audio(src, dst, loudness=-14.0, true_peak=-1.0)
    assert report["true_peak_dbtp"] <= -1.5
    aac = tmp_path / "mix.m4a"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(dst), "-c:a", "aac", "-b:a", "192k", str(aac)],
                   check=True)
    assert _tp_lufs(aac)["true_peak"] <= -1.0
    # Die stille Sekunde bleibt still (kein Hochregeln wie bei dynamischer Normalisierung)
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", "3.2", "-t", "0.6", "-i", str(dst),
                          "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    assert float(out.split("max_volume:")[1].split("dB")[0]) < -60


def test_spuren_enden_und_blenden_am_aktwechsel(tmp_path):
    t = ff.Track(tmp_path / "a.wav", at=23.0, gain_db=-20, fill=True, length=23.0, fade_in=0.2, fade_out=1.5)
    graph = ff.build_mix_command([t], 168.0, tmp_path / "m.wav", normalize=False)
    graph = graph[graph.index("-filter_complex") + 1]
    assert "atrim=0:23.000" in graph and "afade=t=in:d=0.200" in graph and "afade=t=out:st=21.500:d=1.500" in graph
    assert "loudnorm" not in graph


# ----------------------------------------------------------------- Bild
def _luma(path, at):
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", str(at), "-i", str(path), "-frames:v", "1",
                          "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-", "-f", "null", "-"],
                         capture_output=True, text=True).stdout
    return float(out.strip().splitlines()[-1].split("=")[1])


def test_abblende_bleibt_in_der_szenendauer_und_standbild_stufe(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=white:s=180x320:r=12:d=4",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    clip = ff.normalize_clip(src, tmp_path / "n.mp4", 3.0, 180, 320, 12, fade_out=1.0)
    assert ff.media_duration(clip) == pytest.approx(3.0, abs=0.1)
    assert _luma(clip, 0.5) > 200 and _luma(clip, 2.9) < 40
    img = tmp_path / "kf.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=360x640", "-frames:v", "1",
                    str(img)], check=True)
    still = ff.still_clip(img, tmp_path / "s.mp4", 2.5, 180, 320, 12)
    info = ff.probe(still)
    assert (info["width"], info["height"]) == (180, 320) and info["duration"] == pytest.approx(2.5, abs=0.1)


def test_vorschau_teilstueck_mit_einblendungen_und_entscheidungskarte(ep, tmp_path):
    """Animatic für den Absturz (S20–S21): Anzeige-Ziffern, Handy-Nachricht, Entscheidungskarte – 0 €."""
    from storyhouse.pipeline import dry_run

    m = dry_run(ep, tmp_path, Pricing.load(), BudgetConfig.load(), scenes="S20-S21", profile="final",
                width=180, height=320, fps=12)
    assert m["paid_api_calls"] == 0 and m["av_drift_s"] <= 0.1
    assert m["probe"]["duration"] == pytest.approx(9.0 + 8.0, abs=0.2)
    assert m["dialogue_problems"] == []
    ass = Path(m["ass"]).read_text(encoding="utf-8")
    assert "-8" in ass and "DU HAST ES WIEDER GETAN." in ass
    panel = (tmp_path / "raw" / "VID_S20.txt").read_text(encoding="utf-8")
    assert "EINSTELLUNG" in panel and "HANDLUNG" in panel and not panel.endswith("\n\n")


def test_workflow_meldet_nicht_bestandene_qualitaetspruefung():
    wf = (ROOT / ".github" / "workflows" / "test-video.yml").read_text(encoding="utf-8")
    assert "steps.qc.outcome == 'failure'" in wf
    assert wf.index("Ergebnisse verschlüsseln") < wf.index("Ergebnis der Qualitätsprüfung")  # erst sichern, dann melden


# ----------------------------------------------------------------- Abgenommene Referenzbilder + Bild-Storyboard-Stufe
def test_abgenommene_referenzen_und_standbild_stufe_im_simulierten_lauf(ep, tmp_path):
    """Liegt referenzen/leon.png im Serienordner, wird Leon nie neu erzeugt; --stills-only rendert ohne Video."""
    import shutil

    from storyhouse.budget import SpendGuard
    from storyhouse.pipeline import paid_render
    from tests.test_paid_flow_simulated import FakeClient, FakeElevenLabs, fake_downloader

    series = tmp_path / "etage-0"
    shutil.copytree(EP, series / "s01e01")
    (series / "referenzen").mkdir()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=540x960", "-frames:v", "1",
                    str(series / "referenzen" / "leon.png")], check=True)
    local = load_episode(series / "s01e01")
    pricing, cfg = Pricing.load(), BudgetConfig.load()
    plan = build_plan(local, pricing, profile="final", scenes="S15-S16", stills_only=True)
    ids = [j.id for j in plan.jobs]
    assert "REF_leon" not in ids and "REF_aufzug" in ids and not [j for j in plan.jobs if j.kind == "video"]
    (key,) = plan.frozen_refs
    assert key.startswith("REF_leon_") and plan.frozen_refs[key].endswith("leon.png")
    kf = next(j for j in plan.jobs if j.id == "KF_S16")
    assert {"uri": f"{{{{out:{key}}}}}", "tag": "leon"} in kf.payload["referenceImages"]
    assert build_plan(ep, pricing, profile="final", scenes="S15-S16").frozen_refs == {}  # Repo: noch nichts abgenommen

    guard = SpendGuard(limit_eur=20.0, pricing=pricing, cfg=cfg, log_path=tmp_path / "spend.json")
    el_guard = SpendGuard(limit_eur=20.0, pricing=pricing, cfg=cfg, provider="elevenlabs",
                          log_path=tmp_path / "spend_el.json")
    guard.link(el_guard)
    client = FakeClient(guard)
    out = tmp_path / "out"
    m = paid_render(local, plan, {"runway": client, "elevenlabs": FakeElevenLabs(el_guard)}, out,
                    scenes="S15-S16", downloader=fake_downloader, log=lambda *_: None)
    assert "REF_leon" not in client.submitted
    sent = client.payloads["KF_S16"]["referenceImages"]
    assert all(r["uri"].startswith("data:image/jpeg;base64,") for r in sent)   # Leon kommt aus der Datei
    assert m["video"].endswith("_final_standbild.mp4")
    assert m["probe"]["duration"] == pytest.approx(11.0 + 8.0, abs=0.2)        # S15 4 s + S16 7 s + Karte 8 s


# ----------------------------------------------------------------- Abbruch bei KF_S20 (Lauf 43758293707)
def test_kein_bildauftrag_sendet_eine_leere_referenzliste(ep):
    """Runway: HTTP 400 „At least one reference image is required“ bei referenceImages: []."""
    from storyhouse.runway_spec import SpecError, validate_payload

    plan = build_plan(ep, Pricing.load(), profile="final", stills_only=True)
    images = [j for j in plan.jobs if j.kind == "image"]
    assert all(j.payload.get("referenceImages", True) for j in images)       # fehlt oder hat Einträge
    assert "referenceImages" not in next(j for j in images if j.id == "KF_S20").payload
    only = build_plan(ep, Pricing.load(), profile="final", scenes="S23")      # Anschluss an S20 entfällt
    assert all(j.payload.get("referenceImages", True) for j in only.jobs if j.kind == "image")
    with pytest.raises(SpecError, match="leer"):
        validate_payload("image", {**images[-1].payload, "referenceImages": []})


def test_zwischenspeicher_wird_verschluesselt_gesichert_und_senkt_die_reservierung(ep, tmp_path, monkeypatch, capsys):
    import os

    from storyhouse.szenen_cache import SzenenCache, job_keys

    wf = (ROOT / ".github" / "workflows" / "test-video.yml").read_text(encoding="utf-8")
    assert wf.count("--cache output/szenen-cache") == 3                       # preflight, reserve, render
    assert wf.index("Rendern (kostenpflichtig)") < wf.index("Zwischenspeicher verschlüsseln") < wf.index("Kosten verbuchen")
    save = wf[wf.index("- name: Zwischenspeicher sichern"):]
    assert "always()" in save.split("uses:")[0] and "szenen-cache.tar.enc" in save

    plan = build_plan(ep, Pricing.load(), profile="final", stills_only=True)
    cache, keys = SzenenCache(tmp_path / "cache"), job_keys(plan.jobs)
    f = tmp_path / "bezahlt.png"
    f.write_bytes(b"bezahltes-bild" * 50)
    cache.put(keys["REF_leon"], f)
    env = {**os.environ, "ARTIFACT_PASSPHRASE": "correct horse battery staple"}
    sh, enc = str(ROOT / "scripts" / "szenen_cache.sh"), tmp_path / "c.tar.enc"
    subprocess.run(["bash", sh, "pack", str(tmp_path / "cache"), str(enc)], env=env, check=True)
    assert b"bezahltes-bild" not in enc.read_bytes()
    subprocess.run(["bash", sh, "unpack", str(enc), str(tmp_path / "neu")], env=env, check=True)
    hit, rest = SzenenCache(tmp_path / "neu").coverage(plan.jobs)
    assert hit == ["REF_leon"] and len(rest) == len(plan.jobs) - 1

    # Reservierung über die CLI: Alle Bilder liegen schon im Zwischenspeicher -> nur noch der Ton wird reserviert.
    import json

    import yaml

    from storyhouse.cli import main

    for j in plan.jobs:
        if j.kind == "image":
            cache.put(keys[j.id], f)
    raw = yaml.safe_load((ROOT / "config" / "budget.yaml").read_text(encoding="utf-8"))
    raw["paid_rendering_enabled"] = True
    raw["paid_scope"].update(episodes=[ep.slug], scenes=["all"], profiles=["final"], budget_eur=10.0)
    budget = tmp_path / "budget.yaml"
    budget.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    for k, v in {"RUNWAYML_API_SECRET": "key_" + "x" * 40, "ELEVENLABS_API_KEY": "sk_" + "x" * 40,
                 "ARTIFACT_PASSPHRASE": "correct horse battery staple",
                 "STORYHOUSE_APPROVAL": "ICH GEBE DIESEN RENDER FREI"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"version": 1, "entries": [
        {"run_id": "run-43758293707-1", "episode": ep.slug, "status": "finalized", "reserved_eur": 9.85,
         "spent_eur": 3.5755}]}), encoding="utf-8")
    args = ["--budget-config", str(budget), "--episode", str(EP), "--profile", "final", "--scenes", "all",
            "--stills-only", "--ledger", str(ledger), "--run-id", "sim-2"]
    assert main(["reserve", *args]) != 0                                      # ohne Cache: 3,58 + 9,85 > 10 €
    assert "13.43 €. Kein bezahlter Lauf" in capsys.readouterr().out
    assert main(["reserve", *args, "--cache", str(tmp_path / "cache")]) == 0
    entry = json.loads(ledger.read_text(encoding="utf-8"))["entries"][-1]
    assert entry["run_id"] == "sim-2" and 4.0 < entry["reserved_eur"] < 5.0   # nur Ton: 3,50 € + 25 %
