import shutil
from pathlib import Path

import pytest

from storyhouse.budget import BudgetConfig, Ledger, Pricing
from storyhouse.episode import load_episode

ROOT = Path(__file__).resolve().parent.parent
EPISODE_DIR = ROOT / "episodes" / "the-other-side" / "ep01-the-reflection"


@pytest.fixture
def episode():
    return load_episode(EPISODE_DIR)


@pytest.fixture
def pricing():
    return Pricing.load(ROOT / "config" / "pricing.yaml")


@pytest.fixture
def cfg():
    return BudgetConfig.load(ROOT / "config" / "budget.yaml")


@pytest.fixture
def enabled_cfg(cfg):
    """Konfiguration mit freigeschaltetem Rendern – nur für Tests."""
    from dataclasses import replace

    return replace(cfg, paid_rendering_enabled=True)


@pytest.fixture
def ledger(tmp_path):
    return Ledger.load(tmp_path / "ledger.json")


requires_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg nicht installiert"
)
