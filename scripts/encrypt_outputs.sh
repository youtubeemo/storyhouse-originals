#!/usr/bin/env bash
# Verschlüsselt Renderergebnisse, bevor sie als Workflow-Artefakt hochgeladen werden.
# Grund: Das Repository ist öffentlich – Artefakte kann jede angemeldete GitHub-Person laden.
#
#   ARTIFACT_PASSPHRASE=... scripts/encrypt_outputs.sh output/render output/encrypted
#
# Entschlüsseln (lokal):
#   openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -in DATEI.enc -out DATEI -pass env:ARTIFACT_PASSPHRASE
set -euo pipefail

src="${1:?Quellordner}"
dst="${2:?Zielordner}"
: "${ARTIFACT_PASSPHRASE:?ARTIFACT_PASSPHRASE fehlt}"

mkdir -p "$dst"
shopt -s nullglob
count=0
for f in "$src"/*.mp4 "$src"/*.srt "$src"/*.ass; do
  openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -salt \
    -in "$f" -out "$dst/$(basename "$f").enc" -pass env:ARTIFACT_PASSPHRASE
  count=$((count + 1))
done
# Unkritische Protokolle (keine Inhalte, keine URLs) unverschlüsselt
for f in "$src"/spend.json "$src"/balance.json; do
  [ -f "$f" ] && cp "$f" "$dst/"
done
echo "$count Datei(en) verschlüsselt nach $dst"
