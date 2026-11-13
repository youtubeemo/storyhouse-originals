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

    def run_job(self, job, outputs):
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
    dest.write_bytes(b"\x89PNG fake")
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

    e = finalize("sim-1", guard.spent_credits, pricing, cfg, ledger)
    assert e["status"] == "finalized"
    assert remaining_eur(cfg, ledger) == pytest.approx(cfg.total_budget_eur - e["spent_eur"])


def test_simulated_flow_aborts_when_reservation_too_small(episode, pricing, enabled_cfg, tmp_path):
    plan = build_plan(episode, pricing, profile="final", scenes="S01-S02", include_audio=False)
    guard = SpendGuard(limit_eur=0.50, pricing=pricing, cfg=enabled_cfg)
    client = FakeClient(guard)
    with pytest.raises(BudgetError):
        paid_render(episode, plan, client, tmp_path, scenes="S01-S02",
                    downloader=lambda u, d: d, log=lambda *_: None)
    assert guard.spent_eur <= 0.50
    assert len(client.submitted) < len(plan.jobs)
