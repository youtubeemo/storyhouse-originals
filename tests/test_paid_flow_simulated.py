"""Simulierter bezahlter Lauf: reserve -> render (Fake-API) -> finalize.

Es wird keine echte API kontaktiert. Geprüft wird, dass das Zusammenspiel
von Reservierung, Laufzeit-Wächter, Schnitt und Verbuchung stimmt.
"""
import pytest

from storyhouse import ffmpeg as ff
from storyhouse.budget import BudgetError, SpendGuard, finalize, remaining_eur, reserve
from storyhouse.pipeline import paid_render
from storyhouse.plan import build_plan
from tests.conftest import requires_ffmpeg


class FakeClient:
    def __init__(self, guard):
        self.guard = guard
        self.submitted = []
        self.payloads = {}

    def run_job(self, job, outputs):
        from storyhouse.runway import resolve_placeholders

        self.payloads[job.id] = resolve_placeholders(job.payload, outputs)
        self.guard.authorize(job.id, job.credits)
        self.submitted.append(job.id)
        return f"https://fake.runway/{job.id}.{job.ext}"


def fake_downloader(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.suffix == ".mp4":
        ff.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                "testsrc2=s=144x256:r=12:d=7", "-pix_fmt", "yuv420p", str(dest)])
        return dest
    if dest.suffix == ".mp3":
        return ff.placeholder_tone(dest.with_suffix(".wav"), 1.0)
    ff.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=1080x1920",
            "-frames:v", "1", str(dest)])
    return dest


@requires_ffmpeg
def test_simulated_paid_flow(episode, pricing, enabled_cfg, ledger, tmp_path):
    cfg = enabled_cfg
    plan = build_plan(episode, pricing, profile="test", scenes="S07-S08")
    entry = reserve("sim-1", plan.total_eur(pricing, cfg), cfg, ledger)
    guard = SpendGuard(limit_eur=entry["reserved_eur"], pricing=pricing, cfg=cfg, log_path=tmp_path / "spend.json")
    client = FakeClient(guard)

    manifest = paid_render(episode, plan, client, tmp_path, scenes="S07-S08", downloader=fake_downloader,
                           log=lambda *_: None)

    assert client.submitted == [j.id for j in plan.jobs]
    assert manifest["probe"]["duration"] == pytest.approx(10.5, abs=0.1)
    assert manifest["probe"]["width"] == 1080 and manifest["probe"]["height"] == 1920
    assert guard.spent_eur <= entry["reserved_eur"]
    # Keyframes/Referenzen werden als JPEG-Data-URI (< 5 MB) weitergegeben, nicht als ablaufende URL
    kf_ref = client.payloads["KF_S07"]["referenceImages"][0]["uri"]
    assert kf_ref.startswith("data:image/jpeg;base64,") and len(kf_ref) < 5 * 1024 * 1024
    assert client.payloads["VID_S07"]["promptImage"].startswith("data:image/jpeg;base64,")

    e = finalize("sim-1", guard.spent_credits, pricing, cfg, ledger)
    assert e["status"] == "finalized"
    assert remaining_eur(cfg, ledger) == pytest.approx(cfg.total_budget_eur - e["spent_eur"])


@requires_ffmpeg
def test_simulated_flow_aborts_when_reservation_too_small(episode, pricing, enabled_cfg, tmp_path):
    plan = build_plan(episode, pricing, profile="final", scenes="S01-S02", include_audio=False)
    guard = SpendGuard(limit_eur=0.50, pricing=pricing, cfg=enabled_cfg)
    client = FakeClient(guard)
    with pytest.raises(BudgetError):
        paid_render(episode, plan, client, tmp_path, scenes="S01-S02",
                    downloader=fake_downloader, log=lambda *_: None)
    assert guard.spent_eur <= 0.50
    assert len(client.submitted) < len(plan.jobs)


def test_finalize_without_evidence_books_full_reservation(pricing, enabled_cfg, tmp_path, monkeypatch):
    """Runner abgestürzt, kein Ausgabenprotokoll, kein Kontostand -> volle Reservierung zählt."""
    from storyhouse.budget import BudgetConfig, Ledger
    from storyhouse.cli import main

    ledger_path = tmp_path / "ledger.json"
    led = Ledger.load(ledger_path)
    entry = reserve("crash-1", 5.0, enabled_cfg, led)
    rc = main(["finalize", "--ledger", str(ledger_path), "--run-id", "crash-1", "--out", str(tmp_path / "none")])
    assert rc == 0
    e = Ledger.load(ledger_path).entry("crash-1")
    assert e["status"] == "finalized"
    assert e["spent_eur"] == pytest.approx(entry["reserved_eur"], abs=0.01)


def test_finalize_uncertain_without_balance_books_full_reservation(pricing, enabled_cfg, tmp_path):
    import json

    from storyhouse.budget import Ledger
    from storyhouse.cli import main

    ledger_path = tmp_path / "ledger.json"
    entry = reserve("u-1", 5.0, enabled_cfg, Ledger.load(ledger_path))
    out = tmp_path / "out"
    out.mkdir()
    (out / "spend.json").write_text(json.dumps({"spent_credits": 10, "stopped": "uncertain"}))
    main(["finalize", "--ledger", str(ledger_path), "--run-id", "u-1", "--out", str(out)])
    e = Ledger.load(ledger_path).entry("u-1")
    assert e["spent_eur"] == pytest.approx(entry["reserved_eur"], abs=0.01)


def test_close_run_manually(pricing, enabled_cfg, tmp_path):
    from storyhouse.budget import Ledger
    from storyhouse.cli import main

    ledger_path = tmp_path / "ledger.json"
    reserve("stale", 5.0, enabled_cfg, Ledger.load(ledger_path))
    rc = main(["close-run", "--ledger", str(ledger_path), "--run-id", "stale",
               "--spent-credits", "120", "--reason", "Runner abgestürzt, Usage geprüft"])
    assert rc == 0
    e = Ledger.load(ledger_path).entry("stale")
    assert e["status"] == "finalized" and e["spent_credits"] == 120


@requires_ffmpeg
def test_loop_seam_last_frame_equals_first(tmp_path):
    import subprocess

    src = tmp_path / "src.mp4"
    ff.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=180x320:r=12:d=3",
            "-pix_fmt", "yuv420p", str(src)])
    out = ff.loop_seam(src, tmp_path / "loop.mp4", 3.0, 0.5, fps=12)
    first, last = tmp_path / "a.png", tmp_path / "b.png"
    ff.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out), "-frames:v", "1", str(first)])
    ff.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out), "-vf", "select=eq(n\\,35)",
            "-frames:v", "1", str(last)])
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(first), "-i", str(last), "-lavfi", "psnr",
                           "-f", "null", "-"], capture_output=True, text=True)
    psnr = float(proc.stderr.split("average:")[1].split()[0])
    assert psnr > 35, f"letzter Frame weicht sichtbar vom ersten ab (PSNR {psnr} dB)"
