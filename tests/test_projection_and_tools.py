"""Kostenhochrechnung und Werkzeug-Katalog – reine Rechnung, keine API."""
import pytest

from storyhouse.cli import main
from storyhouse.projection import project
from storyhouse.providers import load
from tests.conftest import ROOT


def test_projection_scales_linearly_and_applies_retakes(episode, pricing, cfg):
    one = project(episode, pricing, cfg, 1, "final", retake_factor=1.0, audio_retake=1.0, music=True)
    ten = project(episode, pricing, cfg, 10, "final", retake_factor=1.0, audio_retake=1.0, music=True)
    assert ten.runway_credits == pytest.approx(10 * one.runway_credits, rel=0.01)
    # 40-s-Episode final = 632 Runway-Credits -> 1 Minute = 948
    assert one.runway_credits == pytest.approx(632 * 60 / 40, abs=1)
    with_retakes = project(episode, pricing, cfg, 1, "final", retake_factor=1.5)
    assert with_retakes.runway_credits == pytest.approx(1.5 * one.runway_credits, abs=1)


def test_projection_respects_run_and_total_limits(episode, pricing, cfg):
    p = project(episode, pricing, cfg, 10, "final")
    assert p.runs_needed >= 10, "25-€-Laufgrenze erzwingt Aufteilung"
    assert p.fits_total_budget is False, "10 min Final sprengt das 200-€-Budget"
    assert project(episode, pricing, cfg, 1, "test").fits_total_budget


def test_projection_picks_elevenlabs_plan(episode, pricing, cfg):
    assert project(episode, pricing, cfg, 1).el_plan == "Starter"
    assert project(episode, pricing, cfg, 10).el_plan == "Creator"
    with pytest.raises(ValueError):
        project(episode, pricing, cfg, 1, retake_factor=0.5)


def test_cli_kosten_and_tools_make_no_network_calls(monkeypatch, capsys):
    import socket

    def no_network(*a, **k):
        raise AssertionError("keine Verbindung")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    assert main(["kosten", "--minuten", "1", "5"]) == 0
    assert main(["tools"]) == 0
    out = capsys.readouterr().out
    assert "| 5 min | final |" in out and "Hedra" in out


def test_provider_catalog_complete_and_paid_tools_gated():
    tools = {t["name"]: t for t in load(ROOT / "config" / "providers.yaml")}
    for name in ("Runway", "ElevenLabs", "FFmpeg", "Python", "GitHub Actions", "Rea Video", "OpenMontage",
                 "Hailuo AI / MiniMax Video", "MiniMax Speech", "Hedra", "Nano Banana (Gemini Image)", "Lovart",
                 "Kilo Code", "OpenCode", "Consensus", "Obsidian"):
        assert name in tools, name
    for t in tools.values():
        if t["kostenpflichtig"] and t["status"] == "vorhanden":
            assert t["name"] in ("Runway", "ElevenLabs"), "nur bereits freigegebene Anbieter"
        if t["kostenpflichtig"]:
            assert t["freigabe_noetig"] is True
