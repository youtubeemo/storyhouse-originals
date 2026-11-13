"""Leitplanken: keine Schlüssel im Repo, keine Veröffentlichung, Workflows nur manuell."""
import re
import subprocess
from pathlib import Path

import yaml

from storyhouse.cli import main
from tests.conftest import ROOT

SECRET_PATTERNS = [
    re.compile(r"key_[0-9a-f]{32,}"),                                   # Runway-Schlüsselformat
    re.compile(r"RUNWAYML_API_SECRET[ \t]*[:=][ \t]*['\"]?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|secret)\s*[:=]\s*['\"][A-Za-z0-9_\-]{24,}['\"]"),
]
TEXT_SUFFIXES = {".py", ".yml", ".yaml", ".md", ".json", ".toml", ".txt", ".sh", ".example", ""}


def _repo_files():
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
        return [ROOT / f for f in out]
    except Exception:
        return [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]


def test_no_secrets_committed():
    hits = []
    for f in _repo_files():
        if not f.is_file() or f.suffix not in TEXT_SUFFIXES or "tests" in f.parts:
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                hits.append(f"{f.relative_to(ROOT)}: {pat.pattern}")
    assert not hits, hits


def test_env_file_is_ignored():
    gi = (ROOT / ".gitignore").read_text()
    assert ".env" in gi and "output/" in gi


def _wf(name):
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())


def test_test_video_workflow_is_manual_only():
    wf = _wf("test-video.yml")
    triggers = wf.get("on", wf.get(True))  # PyYAML liest 'on' als True
    assert set(triggers) == {"workflow_dispatch"}
    assert triggers["workflow_dispatch"]["inputs"]["mode"]["default"] == "dry-run"


def test_paid_job_is_gated():
    wf = _wf("test-video.yml")
    paid = wf["jobs"]["paid-render"]
    assert paid["environment"]["name"] == "paid-render"
    assert "paid-render" in paid["if"]
    assert paid["concurrency"]["cancel-in-progress"] is False
    dry = wf["jobs"]["dry-run"]
    text = yaml.safe_dump(dry)
    assert "RUNWAYML_API_SECRET" not in text, "Dry-Run darf den Schlüssel nie sehen"


def test_workflow_inputs_not_interpolated_into_shell():
    raw = (ROOT / ".github" / "workflows" / "test-video.yml").read_text()
    for line in raw.splitlines():
        if line.strip().startswith(("run:", "python", "bash", "echo")):
            assert "${{ inputs." not in line and "${{ github.event.inputs" not in line, line


def test_nothing_publishes_videos():
    forbidden = re.compile(r"(?i)(tiktok.*(upload|publish|post)|open\.tiktokapis|video/upload|youtube.*upload)")
    for f in (ROOT / ".github").rglob("*.yml"):
        assert not forbidden.search(f.read_text()), f
    for f in (ROOT / "storyhouse").rglob("*.py"):
        assert not forbidden.search(f.read_text()), f


def test_cli_preflight_refuses_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYHOUSE_APPROVAL", "ICH GEBE DIESEN RENDER FREI")
    monkeypatch.setenv("RUNWAYML_API_SECRET", "dummy")
    rc = main(["preflight", "--ledger", str(tmp_path / "l.json")])
    assert rc == 2


def test_cli_render_refuses_without_reservation(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYHOUSE_APPROVAL", "ICH GEBE DIESEN RENDER FREI")
    monkeypatch.setenv("RUNWAYML_API_SECRET", "dummy")
    rc = main(["render", "--ledger", str(tmp_path / "l.json"), "--run-id", "nope"])
    assert rc == 2
