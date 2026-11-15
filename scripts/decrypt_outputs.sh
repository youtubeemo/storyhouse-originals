#!/usr/bin/env bash
# Entschlüsselt Ergebnisse von encrypt_outputs.sh (nur .mp4/.srt/.ass/LIZENZ_*.txt).
#   ARTIFACT_PASSPHRASE=... scripts/decrypt_outputs.sh output/encrypted output/plain
# Die Passphrase kommt nur aus der Umgebung und wird nie ausgegeben.
set -euo pipefail

src="${1:?Quellordner}"
dst="${2:?Zielordner}"
pass="${ARTIFACT_PASSPHRASE:-}"
if [ "${#pass}" -lt 16 ]; then
  echo "ARTIFACT_PASSPHRASE fehlt oder ist zu kurz (Environment-Secret nicht verfügbar?)" >&2
  exit 1
fi

mkdir -p "$dst"
shopt -s nullglob
count=0
for f in "$src"/*.mp4.enc "$src"/*.srt.enc "$src"/*.ass.enc "$src"/LIZENZ_*.txt.enc; do
  name="$(basename "$f" .enc)"
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 \
    -in "$f" -out "$dst/$name" -pass env:ARTIFACT_PASSPHRASE
  count=$((count + 1))
done
if [ "$count" -eq 0 ]; then
  echo "Keine verschlüsselten Dateien in $src gefunden" >&2
  exit 1
fi
echo "$count Datei(en) entschlüsselt"
