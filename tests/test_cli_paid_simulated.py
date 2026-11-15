"""Kompletter bezahlter Ablauf über die Kommandozeile – gegen simulierte Server.

preflight -> reserve -> render -> finalize mit beiden Anbietern. urllib ist ersetzt:
Es geht keine einzige echte Anfrage raus, es entstehen keine Kosten.
"""
import io
import json
import urllib.error
from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml

from storyhouse import ffmpeg as ff
from storyhouse.budget import Ledger
from storyhouse.cli import main
from tests.conftest import ROOT, requires_ffmpeg

RUNWAY_KEY = "key_" + "ab12" * 10
EL_KEY = "sk_" + "cd34" * 12
PASSPHRASE = "correct horse battery staple"


REFLECTION_S01 = {"profiles": ["test"], "scenes": ["S01"], "audio_providers": ["elevenlabs"]}


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, headers: dict | None = None):
        super().__init__(body)
        self.status = 200
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeInternet:
    """Antwortet wie Runway + ElevenLabs. Zählt alles mit."""

    def __init__(self, assets: dict[str, bytes], el_used: int = 1000):
        self.assets = assets
        self.el_used = el_used
        self.log: list[tuple[str, str, str]] = []
        self.el_posts: list[dict] = []
        self.tasks: dict[str, str] = {}

    def __call__(self, req, timeout=None):
        if isinstance(req, str):  # Download eines Runway-Ergebnisses
            self.log.append(("GET", urlparse(req).netloc, urlparse(req).path))
            return FakeResponse(self.assets[Path(urlparse(req).path).suffix])
        url, method = urlparse(req.full_url), req.get_method()
        self.log.append((method, url.netloc, url.path))
        headers = {k.lower(): v for k, v in req.header_items()}
        body = json.loads(req.data) if req.data else None
        if url.netloc == "api.dev.runwayml.com":
            assert headers["authorization"] == f"Bearer {RUNWAY_KEY}"
            if url.path == "/v1/organization":
                return FakeResponse(json.dumps({"creditBalance": 5000}).encode())
            if method == "POST":
                task = f"t{len(self.tasks)}"
                self.tasks[task] = ".mp4" if url.path == "/v1/image_to_video" else ".png"
                return FakeResponse(json.dumps({"id": task}).encode())
            task = url.path.rsplit("/", 1)[-1]
            return FakeResponse(json.dumps({"id": task, "status": "SUCCEEDED",
                                            "output": [f"https://cdn.fake.example/{task}{self.tasks[task]}"]}).encode())
        if url.netloc == "api.elevenlabs.io":
            assert headers["xi-api-key"] == EL_KEY and "authorization" not in headers
            if url.path == "/v1/user/subscription":
                return FakeResponse(json.dumps({
                    "tier": "starter", "status": "active", "character_count": self.el_used,
                    "character_limit": 30000, "max_credit_limit_extension": 0,
                    "current_overage": {"amount": "0", "currency": "usd"}, "has_open_invoices": False}).encode())
            if url.path.startswith("/v1/voices/"):
                return FakeResponse(json.dumps({"voice_id": url.path.rsplit("/", 1)[-1], "name": "Sarah"}).encode())
            assert method == "POST" and url.query == "output_format=mp3_44100_128"
            self.el_posts.append({"path": url.path, **body})
            cost = len(body["text"]) if "text" in body and "duration_seconds" not in body else None
            if cost is not None:
                self.el_used += cost
            return FakeResponse(self.assets[".mp3"], {"character-cost": str(cost)} if cost is not None else {})
        raise AssertionError(f"Unerwarteter Host: {url.netloc}")


@pytest.fixture
def assets(tmp_path):
    src = tmp_path / "assets"
    src.mkdir()
    ff.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=144x256:r=12:d=5",
            "-pix_fmt", "yuv420p", str(src / "v.mp4")])
    ff.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=270x480", "-frames:v", "1",
            str(src / "i.png")])
    ff.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=330:duration=1.5",
            "-c:a", "libmp3lame", "-b:a", "128k", str(src / "a.mp3")])
    return {".mp4": (src / "v.mp4").read_bytes(), ".png": (src / "i.png").read_bytes(),
            ".mp3": (src / "a.mp3").read_bytes()}


@requires_ffmpeg
def test_full_paid_flow_via_cli_with_both_providers(assets, tmp_path, monkeypatch, capsys):
    net = FakeInternet(assets)
    monkeypatch.setattr("urllib.request.urlopen", net)
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setenv("RUNWAYML_API_SECRET", RUNWAY_KEY)
    monkeypatch.setenv("ELEVENLABS_API_KEY", EL_KEY)
    monkeypatch.setenv("ARTIFACT_PASSPHRASE", PASSPHRASE)
    monkeypatch.setenv("STORYHOUSE_APPROVAL", "ICH GEBE DIESEN RENDER FREI")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    # Musiklizenz nur in einer Testkopie bestätigen – im Repo bleibt Musik gesperrt.
    raw = yaml.safe_load((ROOT / "config" / "budget.yaml").read_text())
    assert raw["elevenlabs"]["music_license_confirmed"] is False
    raw["paid_rendering_enabled"] = True
    raw["paid_scope"] = dict(REFLECTION_S01)
    raw["elevenlabs"]["music_license_confirmed"] = True
    budget = tmp_path / "budget.yaml"
    budget.write_text(yaml.safe_dump(raw, allow_unicode=True))

    ledger, out = tmp_path / "ledger.json", tmp_path / "render"
    common = ["--budget-config", str(budget), "--profile", "test", "--scenes", "S01",
              "--ledger", str(ledger), "--run-id", "sim-cli", "--out", str(out)]

    assert main(["preflight", *common]) == 0
    assert not net.el_posts and not net.tasks, "Preflight generiert nichts"
    assert main(["reserve", *common]) == 0
    assert main(["render", *common]) == 0
    assert main(["finalize", *common]) == 0

    # Film: S01 (3 s) + Outro, vertikal, mit Ton; dazu Loop-Fassung und Untertitel
    manifest = json.loads((out / "manifest.json").read_text())
    probe = ff.probe(Path(manifest["video"]))
    assert (probe["width"], probe["height"]) == (1080, 1920)
    assert probe["duration"] == pytest.approx(3.0 + 2.5, abs=0.1)
    assert probe["video_codec"] == "h264" and probe["audio_codec"] == "aac"
    assert manifest["loop_probe"]["duration"] == pytest.approx(3.0, abs=0.1)
    assert manifest["music_source"] == "generated" and manifest["av_drift_s"] <= 0.1
    assert "Don't turn around." in Path(manifest["srt"]).read_text()

    # ElevenLabs: 1 Dialog, Drone + 3 SFX + Outro-Sting, 1 Musik – jeder genau einmal
    paths = [p["path"] for p in net.el_posts]
    assert paths.count("/v1/text-to-speech/EXAVITQu4vr4xnSDxMaL") == 1
    assert paths.count("/v1/sound-generation") == 5 and paths.count("/v1/music") == 1
    assert all(p.get("duration_seconds") for p in net.el_posts if p["path"] == "/v1/sound-generation")
    hosts = {h for _, h, _ in net.log}
    assert hosts == {"api.dev.runwayml.com", "api.elevenlabs.io", "cdn.fake.example"}

    # Budget: beide Anbieter im Ledger, innerhalb der Reservierung
    entry = Ledger.load(ledger).entry("sim-cli")
    assert entry["status"] == "finalized" and not entry.get("overrun")
    assert entry["elevenlabs_credits"] > 0 and entry["spent_credits"] > 0
    assert 0 < entry["spent_eur"] <= entry["reserved_eur"]
    el_log = json.loads((out / "spend_elevenlabs.json").read_text())
    assert el_log["provider"] == "elevenlabs" and el_log["stopped"] is None

    # Schlüssel tauchen nirgends auf: nicht in der Ausgabe, nicht in erzeugten Dateien
    printed = capsys.readouterr()
    for secret in (RUNWAY_KEY, EL_KEY, PASSPHRASE):
        assert secret not in printed.out + printed.err
        for f in list(out.glob("*.json")) + [ledger]:
            assert secret not in f.read_text(), f


@requires_ffmpeg
def test_cli_refuses_when_account_has_overage_enabled(assets, tmp_path, monkeypatch):
    """Automatische Nachberechnung im ElevenLabs-Konto an -> Preflight bricht ab, nichts wird generiert."""
    net = FakeInternet(assets)
    real = net.__call__

    def overage(req, timeout=None):
        resp = real(req, timeout)
        if not isinstance(req, str) and req.full_url.endswith("/v1/user/subscription"):
            data = json.loads(resp.getvalue())
            data["max_credit_limit_extension"] = "unlimited"
            return FakeResponse(json.dumps(data).encode())
        return resp

    monkeypatch.setattr("urllib.request.urlopen", overage)
    monkeypatch.setenv("RUNWAYML_API_SECRET", RUNWAY_KEY)
    monkeypatch.setenv("ELEVENLABS_API_KEY", EL_KEY)
    monkeypatch.setenv("ARTIFACT_PASSPHRASE", PASSPHRASE)
    monkeypatch.setenv("STORYHOUSE_APPROVAL", "ICH GEBE DIESEN RENDER FREI")
    raw = yaml.safe_load((ROOT / "config" / "budget.yaml").read_text())
    raw["paid_rendering_enabled"] = True
    raw["paid_scope"] = dict(REFLECTION_S01)
    budget = tmp_path / "budget.yaml"
    budget.write_text(yaml.safe_dump(raw, allow_unicode=True))
    rc = main(["preflight", "--budget-config", str(budget), "--scenes", "S01", "--ledger", str(tmp_path / "l.json")])
    assert rc == 2
    assert not net.el_posts and not net.tasks


def _paid_env(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setenv("RUNWAYML_API_SECRET", RUNWAY_KEY)
    monkeypatch.setenv("ELEVENLABS_API_KEY", EL_KEY)
    monkeypatch.setenv("ARTIFACT_PASSPHRASE", PASSPHRASE)
    monkeypatch.setenv("STORYHOUSE_APPROVAL", "ICH GEBE DIESEN RENDER FREI")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


def _repo_budget_freigeschaltet(tmp_path):
    """config/budget.yaml des Repos mit dem früheren Umfang „The Reflection, test, S01“.
    (Im Repo ist seit 15.11.2026 nur ETAGE 0 freigegeben; diese Tests simulieren weiter den S01-Ablauf.)"""
    raw = yaml.safe_load((ROOT / "config" / "budget.yaml").read_text())
    raw["paid_scope"] = dict(REFLECTION_S01)
    raw["paid_rendering_enabled"] = True
    raw["paid_scope"] = dict(REFLECTION_S01)
    path = tmp_path / "budget_repo_freigeschaltet.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True))
    return ["--budget-config", str(path)]


@requires_ffmpeg
def test_first_render_with_repo_config_test_s01(assets, tmp_path, monkeypatch):
    """Exakt der freigegebene erste Lauf – mit der echten config/budget.yaml des Repos."""
    net = FakeInternet(assets)
    monkeypatch.setattr("urllib.request.urlopen", net)
    _paid_env(monkeypatch)
    ledger, out = tmp_path / "ledger.json", tmp_path / "render"
    common = ["--profile", "test", "--scenes", "S01", "--ledger", str(ledger), "--run-id", "first", "--out", str(out),
              *_repo_budget_freigeschaltet(tmp_path)]
    for cmd in ("preflight", "reserve", "render", "finalize"):
        assert main([cmd, *common]) == 0, cmd
    assert not any(p["path"] == "/v1/music" for p in net.el_posts), "Musik bleibt gesperrt"
    # ElevenLabs läuft vor Runway
    posts = [(h, p) for m, h, p in net.log if m == "POST"]
    first_runway = next(i for i, (h, _) in enumerate(posts) if h == "api.dev.runwayml.com")
    assert all(h == "api.elevenlabs.io" for h, _ in posts[:first_runway]) and first_runway == 6
    entry = Ledger.load(ledger).entry("first")
    assert entry["reserved_eur"] <= 1.10 and entry["spent_eur"] <= entry["reserved_eur"]
    probe = ff.probe(Path(json.loads((out / "manifest.json").read_text())["video"]))
    assert probe["duration"] == pytest.approx(5.5, abs=0.1)


@pytest.mark.parametrize("where", ["generate", "subscription"])
def test_missing_elevenlabs_permission_stops_before_runway_spends(assets, tmp_path, monkeypatch, where):
    """Fehlende ElevenLabs-Berechtigung: kein einziger Runway-Task, Ledger korrekt abgeschlossen."""
    net = FakeInternet(assets)
    real = net.__call__

    def deny(req, timeout=None):
        if not isinstance(req, str) and "api.elevenlabs.io" in req.full_url:
            is_sub = req.full_url.endswith("/v1/user/subscription")
            if (where == "subscription" and is_sub) or (where == "generate" and req.get_method() == "POST"):
                net.log.append((req.get_method(), "api.elevenlabs.io", "DENIED"))
                body = json.dumps({"detail": {"status": "missing_permissions",
                                              "message": "The API key is missing the permission"}}).encode()
                raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(body))
        return real(req, timeout)

    monkeypatch.setattr("urllib.request.urlopen", deny)
    _paid_env(monkeypatch)
    ledger, out = tmp_path / "ledger.json", tmp_path / "render"
    common = ["--profile", "test", "--scenes", "S01", "--ledger", str(ledger), "--run-id", "deny", "--out", str(out),
              *_repo_budget_freigeschaltet(tmp_path)]
    if where == "subscription":
        assert main(["preflight", *common]) == 2
        assert not net.tasks and not net.el_posts
        return
    assert main(["preflight", *common]) == 0
    assert main(["reserve", *common]) == 0
    assert main(["render", *common]) == 2
    assert not net.tasks, "Runway darf nach fehlender ElevenLabs-Berechtigung nichts generieren"
    assert sum(1 for _, _, p in net.log if p == "DENIED") == 1, "genau ein abgelehnter Versuch, keine Wiederholung"
    assert main(["finalize", *common]) == 0
    entry = Ledger.load(ledger).entry("deny")
    assert entry["status"] == "finalized" and entry["spent_eur"] == 0
    assert "permission" in entry.get("note", "")


@requires_ffmpeg
def test_private_test_with_free_tier_account(assets, tmp_path, monkeypatch):
    """Free-Konto + Repo-Konfiguration: Lauf gelingt, Ergebnis ist überall als privater Test markiert."""
    net = FakeInternet(assets)
    real = net.__call__

    def free(req, timeout=None):
        resp = real(req, timeout)
        if not isinstance(req, str) and req.full_url.endswith("/v1/user/subscription"):
            data = json.loads(resp.getvalue())
            data.update({"tier": "free", "status": "free", "character_limit": 10000})
            return FakeResponse(json.dumps(data).encode())
        return resp

    monkeypatch.setattr("urllib.request.urlopen", free)
    _paid_env(monkeypatch)
    ledger, out = tmp_path / "ledger.json", tmp_path / "render"
    common = ["--profile", "test", "--scenes", "S01", "--ledger", str(ledger), "--run-id", "priv", "--out", str(out),
              *_repo_budget_freigeschaltet(tmp_path)]
    for cmd in ("preflight", "reserve", "render", "finalize"):
        assert main([cmd, *common]) == 0, cmd

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["license"] == {"elevenlabs_commercial": False, "usage": "privat, nicht kommerziell"}
    video, loop = Path(manifest["video"]), Path(manifest["loop_video"])
    assert video.name == "tos-ep01-the-reflection_test_PRIVAT.mp4"
    assert loop.name == "tos-ep01-the-reflection_test_PRIVAT_loop.mp4"
    import subprocess
    for f in (video, loop):
        tags = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json",
                                          str(f)], capture_output=True, text=True, check=True).stdout)
        assert "PRIVATER TEST" in tags["format"]["tags"]["comment"]
    assert "Nicht hochladen" in (out / "LIZENZ_PRIVATER_TEST.txt").read_text()
    assert not any(p["path"] == "/v1/music" for p in net.el_posts)

    # Verschlüsselung erfasst Film, Loop-Fassung und Lizenzhinweis
    import os
    enc = tmp_path / "enc"
    subprocess.run(["bash", str(ROOT / "scripts" / "encrypt_outputs.sh"), str(out), str(enc)],
                   env={**os.environ, "ARTIFACT_PASSPHRASE": PASSPHRASE}, check=True, capture_output=True)
    names = {p.name for p in enc.iterdir()}
    assert {"tos-ep01-the-reflection_test_PRIVAT.mp4.enc", "tos-ep01-the-reflection_test_PRIVAT_loop.mp4.enc",
            "LIZENZ_PRIVATER_TEST.txt.enc"} <= names
    assert not any(n.endswith(".mp4") for n in names), "kein unverschlüsseltes Video"


def test_free_tier_refused_beyond_private_scope(tmp_path, monkeypatch):
    """Szenen/Profile außerhalb des privaten Testumfangs: Abbruch ohne Netz."""
    import socket

    def no_network(*a, **k):
        raise AssertionError("keine Verbindung erlaubt")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    _paid_env(monkeypatch)
    for extra in (["--profile", "final", "--scenes", "S01"], ["--profile", "test", "--scenes", "S01-S02"]):
        assert main(["preflight", *extra, "--ledger", str(tmp_path / "l.json")]) == 2
