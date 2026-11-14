"""iPhone-Player: Entschlüsselung im Browser, keine Netzwerkzugriffe, Pages veröffentlicht nur den Player."""
import json
import os
import re
import shutil
import subprocess

import pytest
import yaml

from tests.conftest import ROOT

PLAYER = ROOT / "player"
PASS = "ein langes testpasswort äöü 42"


def _node(script: str, cwd):
    return subprocess.run(["node", "-e", script], cwd=cwd, capture_output=True, text=True, timeout=120)


@pytest.mark.skipif(not (shutil.which("node") and shutil.which("openssl") and shutil.which("ffmpeg")),
                    reason="node/openssl/ffmpeg fehlt")
def test_browser_decryption_matches_encrypt_outputs(tmp_path):
    src, enc = tmp_path / "render", tmp_path / "enc"
    src.mkdir()
    film = src / "tos-ep01-the-reflection_test_PRIVAT.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=180x320:r=12:d=2",
                    "-pix_fmt", "yuv420p", str(film)], check=True)
    subprocess.run(["bash", str(ROOT / "scripts" / "encrypt_outputs.sh"), str(src), str(enc)],
                   env={**os.environ, "ARTIFACT_PASSPHRASE": PASS}, check=True, capture_output=True)
    script = f"""
const fs = require('fs'); eval(fs.readFileSync({json.dumps(str(PLAYER / 'player.js'))}, 'utf8'));
(async () => {{
  const P = globalThis.StoryhousePlayer;
  const enc = fs.readFileSync({json.dumps(str(enc / (film.name + '.enc')))});
  const out = await P.decryptOpenSSL(enc, {json.dumps(PASS)});
  let wrong = 'akzeptiert';
  try {{ await P.decryptOpenSSL(enc, 'falsche passphrase 1234'); }} catch (e) {{ wrong = e.message; }}
  let notenc = 'akzeptiert';
  try {{ await P.decryptOpenSSL(Buffer.from('kein openssl format'.repeat(4)), {json.dumps(PASS)}); }}
  catch (e) {{ notenc = e.message; }}
  console.log(JSON.stringify({{
    same: Buffer.compare(Buffer.from(out), fs.readFileSync({json.dumps(str(film))})) === 0,
    mp4: P.looksLikeMp4(out), name: P.plainName({json.dumps(film.name + '.enc')}), wrong, notenc }}));
}})().catch(e => {{ console.error(e); process.exit(1); }});
"""
    proc = _node(script, tmp_path)
    assert proc.returncode == 0, proc.stderr
    res = json.loads(proc.stdout)
    assert res["same"] and res["mp4"], "bitgenau wie openssl"
    assert res["name"] == film.name
    assert "Passphrase falsch" in res["wrong"]
    assert "Keine StoryHouse-Datei" in res["notenc"]
    assert PASS not in proc.stdout + proc.stderr


def test_player_has_no_network_access_and_no_storage():
    html = (PLAYER / "index.html").read_text()
    csp = re.search(r'Content-Security-Policy" content="([^"]+)"', html).group(1)
    rules = dict(r.strip().split(" ", 1) for r in csp.split(";"))
    assert rules["default-src"] == "'none'" and rules["connect-src"] == "'none'"
    assert rules["script-src"] == "'self'" and rules["media-src"] == "blob:"
    assert "http" not in csp
    js = (PLAYER / "player.js").read_text()
    for forbidden in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "localStorage", "sessionStorage",
                      "indexedDB", "document.cookie", "console.log", "eval(", "http://", "https://"):
        assert forbidden not in js, forbidden
    assert not re.search(r"<script[^>]+src=\"https?:", html), "keine fremden Skripte"


def test_pages_publishes_only_the_player():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "player-pages.yml").read_text())
    job = wf["jobs"]["deploy"]
    upload = next(s for s in job["steps"] if "upload-pages-artifact" in str(s.get("uses")))
    assert upload["with"]["path"] == "player"
    raw = (ROOT / ".github" / "workflows" / "player-pages.yml").read_text()
    assert "secrets." not in raw and "download-artifact" not in raw and "output/" not in raw
    assert job["permissions"] == {"contents": "read", "pages": "write", "id-token": "write"}
    files = [p for p in PLAYER.rglob("*") if p.is_file()]
    assert files and all(p.suffix in {".html", ".css", ".js"} for p in files), "keine Medien im Player-Ordner"
