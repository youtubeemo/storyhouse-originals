"""GitHub-Schutzprüfungen: Sichtbarkeit (unverschlüsselter Upload) und Freigabenachweis (bezahlter Render).

Kein Test kontaktiert GitHub – der Transport ist simuliert.
"""
import os
import re
import shutil
import subprocess

import pytest
import yaml

from storyhouse import github_guard as gg
from tests.conftest import ROOT

REPO = "youtubeemo/storyhouse-originals"
TOKEN = "ghs_" + "x" * 36


def repo_api(private, visibility, status=200, full_name=REPO):
    def transport(url, headers):
        assert url == f"https://api.github.com/repos/{REPO}"
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        return status, ({"full_name": full_name, "private": private, "visibility": visibility}
                        if status == 200 else None)
    return transport


# ---------------------------------------------------------------- Sichtbarkeit
def test_private_repo_confirmed():
    ok, reason = gg.repo_is_private(REPO, TOKEN, repo_api(True, "private"))
    assert ok and "privat" in reason


@pytest.mark.parametrize("transport", [
    repo_api(False, "public"),                       # öffentlich (heutiger Stand)
    repo_api(True, "internal"),                      # widersprüchlich
    repo_api(True, None),                            # Feld fehlt
    repo_api("true", "private"),                     # falscher Typ
    repo_api(True, "private", status=403),           # keine Berechtigung
    repo_api(True, "private", status=500),           # Serverfehler
    repo_api(True, "private", full_name="evil/other"),  # falsches Repo
])
def test_anything_but_confirmed_private_counts_as_public(transport):
    ok, _ = gg.repo_is_private(REPO, TOKEN, transport)
    assert ok is False


@pytest.mark.parametrize("exc", [TimeoutError(), OSError("dns"), ValueError("bad json")])
def test_network_errors_count_as_public(exc):
    def boom(url, headers):
        raise exc
    assert gg.repo_is_private(REPO, TOKEN, boom)[0] is False


def test_invalid_repo_name_never_queried():
    def never(url, headers):
        raise AssertionError("kein Aufruf")
    assert gg.repo_is_private("", TOKEN, never)[0] is False
    assert gg.repo_is_private("a/b/../c", TOKEN, never)[0] is False


def test_cli_visibility_writes_output_and_never_prints_token(tmp_path, monkeypatch, capsys):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert gg.main(["visibility", "--github-output", str(out)], transport=repo_api(False, "public")) == 0
    assert out.read_text() == "private=false\n"
    assert gg.main(["visibility", "--github-output", str(out), "--require-private"],
                   transport=repo_api(False, "public")) == 3
    assert gg.main(["visibility", "--github-output", str(out), "--require-private"],
                   transport=repo_api(True, "private")) == 0
    assert out.read_text().splitlines()[-1] == "private=true"
    printed = capsys.readouterr()
    assert TOKEN not in printed.out + printed.err


# ---------------------------------------------------------------- Freigabenachweis
def approvals_api(reviews, status=200):
    def transport(url, headers):
        assert url == f"https://api.github.com/repos/{REPO}/actions/runs/123/approvals"
        return status, reviews
    return transport


def review(state="approved", user="youtubeemo", env="paid-render"):
    return {"state": state, "user": {"login": user}, "environments": [{"name": env}], "comment": ""}


def test_approval_by_owner_accepted():
    ok, reason = gg.run_was_approved(REPO, "123", "paid-render", {"youtubeemo"}, TOKEN, approvals_api([review()]))
    assert ok and "youtubeemo" in reason


@pytest.mark.parametrize("reviews,status", [
    ([], 200),                                        # privates Repo ohne Enterprise: kein Datensatz
    ([review(state="rejected")], 200),
    ([review(), review(state="rejected")], 200),
    ([review(user="someone-else")], 200),
    ([review(env="other-env")], 200),
    ([review()], 404),
    (None, 500),
    ({"unexpected": True}, 200),
])
def test_missing_or_wrong_approval_blocks_paid_render(reviews, status):
    ok, reason = gg.run_was_approved(REPO, "123", "paid-render", {"youtubeemo"}, TOKEN,
                                     approvals_api(reviews, status))
    assert ok is False


def test_no_approval_message_explains_private_repo_limit():
    _, reason = gg.run_was_approved(REPO, "123", "paid-render", {"youtubeemo"}, TOKEN, approvals_api([]))
    assert "Enterprise" in reason and "verweigert" in reason


def test_cli_approval_exit_codes(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "youtubeemo")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert gg.main(["approval"], transport=approvals_api([review()])) == 0
    assert gg.main(["approval"], transport=approvals_api([])) == 2


# ---------------------------------------------------------------- Workflows
def _wf(name):
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())


def test_paid_job_requires_proven_approval_before_any_paid_step():
    paid = _wf("test-video.yml")["jobs"]["paid-render"]
    names = [s.get("name", "") for s in paid["steps"]]
    gate = names.index("Freigabe nachweisen (Required Reviewers)")
    assert gate < names.index("Preflight (Freigabe, Preise, Budget, Guthaben)")
    assert gate < names.index("Budget reservieren und sichern (vor jeder Ausgabe)")
    assert gate < names.index("Rendern (kostenpflichtig)")
    step = paid["steps"][gate]
    assert "github_guard approval --environment paid-render" in step["run"]
    assert "if" not in step, "darf nicht übersprungen werden"
    assert paid["permissions"] == {"contents": "write", "actions": "read"}


def test_github_token_only_given_to_guard_steps():
    for name in ("test-video.yml", "film-bereitstellen.yml"):
        for job in _wf(name)["jobs"].values():
            assert "GITHUB_TOKEN" not in (job.get("env") or {})
            for step in job["steps"]:
                if "GITHUB_TOKEN" in (step.get("env") or {}):
                    assert "github_guard" in step.get("run", ""), step


def test_provide_workflow_is_free_manual_and_private_only():
    raw = (ROOT / ".github" / "workflows" / "film-bereitstellen.yml").read_text()
    wf = yaml.safe_load(raw)
    assert set(wf.get("on", wf.get(True))) == {"workflow_dispatch"}
    job = wf["jobs"]["provide"]
    assert "refs/heads/main" in job["if"] and "repository_owner" in job["if"]
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    for forbidden in ("RUNWAYML", "ELEVENLABS", "storyhouse render", "storyhouse preflight", "storyhouse reserve"):
        assert forbidden not in raw, forbidden
    names = [s.get("name", s.get("uses", "")) for s in job["steps"]]
    first_check = names.index("Sichtbarkeit prüfen (muss privat sein)")
    download = next(i for i, s in enumerate(job["steps"]) if "download-artifact" in str(s.get("uses")))
    decrypt = names.index("Entschlüsseln")
    recheck = names.index("Sichtbarkeit erneut prüfen (direkt vor dem Upload)")
    upload = next(i for i, s in enumerate(job["steps"]) if "upload-artifact" in str(s.get("uses")))
    assert first_check < download < decrypt < recheck == upload - 1
    for i in (first_check, recheck):
        assert "--require-private" in job["steps"][i]["run"] and "if" not in job["steps"][i]
    assert job["steps"][upload]["if"] == "${{ steps.visibility.outputs.private == 'true' }}"
    assert "secrets.ARTIFACT_PASSPHRASE" in str(job["steps"][decrypt]["env"])
    for line in raw.splitlines():
        if line.strip().startswith(("run:", "echo", "case", "bash", "python")):
            assert "${{ inputs." not in line, line


# ---------------------------------------------------------------- Entschlüsselung
@pytest.mark.skipif(not shutil.which("openssl"), reason="openssl fehlt")
def test_decrypt_roundtrip_and_passphrase_not_logged(tmp_path):
    src, enc, plain = tmp_path / "render", tmp_path / "enc", tmp_path / "plain"
    src.mkdir()
    (src / "film_test_PRIVAT.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"film" * 500)
    (src / "film.srt").write_text("1\n00:00:00,350 --> 00:00:01,950\nDon't turn around.\n")
    (src / "LIZENZ_PRIVATER_TEST.txt").write_text("PRIVATER TEST")
    secret = "correct horse battery staple"
    env = {**os.environ, "ARTIFACT_PASSPHRASE": secret}
    subprocess.run(["bash", str(ROOT / "scripts" / "encrypt_outputs.sh"), str(src), str(enc)], env=env, check=True,
                   capture_output=True)
    proc = subprocess.run(["bash", str(ROOT / "scripts" / "decrypt_outputs.sh"), str(enc), str(plain)], env=env,
                          check=True, capture_output=True, text=True)
    assert secret not in proc.stdout + proc.stderr
    for f in src.iterdir():
        assert (plain / f.name).read_bytes() == f.read_bytes()
    wrong = subprocess.run(["bash", str(ROOT / "scripts" / "decrypt_outputs.sh"), str(enc), str(tmp_path / "x")],
                           env={**os.environ, "ARTIFACT_PASSPHRASE": "falsche passphrase 123"}, capture_output=True)
    assert wrong.returncode != 0
    missing = subprocess.run(["bash", str(ROOT / "scripts" / "decrypt_outputs.sh"), str(enc), str(tmp_path / "y")],
                             env={k: v for k, v in os.environ.items() if k != "ARTIFACT_PASSPHRASE"},
                             capture_output=True, text=True)
    assert missing.returncode != 0 and "fehlt" in missing.stderr


def test_guard_module_only_reads():
    src = (ROOT / "storyhouse" / "github_guard.py").read_text()
    assert set(re.findall(r'method="([A-Z]+)"', src)) == {"GET"}
    assert not re.search(r"print\([^)]*token", src, re.I)
