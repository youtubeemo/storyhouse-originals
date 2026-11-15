#!/usr/bin/env bash
# Zwischenspeicher bezahlter Bild-/Tonaufträge zwischen Workflow-Läufen sichern – immer verschlüsselt
# (das Repository ist öffentlich; der Actions-Cache ist für andere Läufe lesbar).
#
#   szenen_cache.sh pack   <Ordner> <Datei.enc>   Ordner -> verschlüsseltes Archiv (nichts zu tun, wenn leer)
#   szenen_cache.sh unpack <Datei.enc> <Ordner>   Archiv -> Ordner (nichts zu tun, wenn Datei fehlt)
#
# Gleiche Verschlüsselung wie scripts/encrypt_outputs.sh (AES-256-CBC, PBKDF2, 600000 Runden).
set -euo pipefail

mode="${1:?pack|unpack}"
: "${ARTIFACT_PASSPHRASE:?ARTIFACT_PASSPHRASE fehlt}"
enc=(openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -pass env:ARTIFACT_PASSPHRASE)

case "$mode" in
  pack)
    dir="${2:?Ordner}"; file="${3:?Datei}"
    if [ ! -f "$dir/index.json" ]; then echo "Zwischenspeicher leer – nichts zu sichern"; exit 0; fi
    mkdir -p "$(dirname "$file")"
    tar -C "$dir" -cf - . | "${enc[@]}" -salt -out "$file"
    echo "Zwischenspeicher gesichert: $(find "$dir" -type f ! -name index.json | wc -l) Datei(en), verschlüsselt"
    ;;
  unpack)
    file="${2:?Datei}"; dir="${3:?Ordner}"
    mkdir -p "$dir"
    if [ ! -f "$file" ]; then echo "Kein Zwischenspeicher vorhanden – alles wird neu erzeugt"; exit 0; fi
    "${enc[@]}" -d -in "$file" | tar -C "$dir" -xf -
    echo "Zwischenspeicher geladen: $(find "$dir" -type f ! -name index.json | wc -l) Datei(en)"
    ;;
  *) echo "pack|unpack" >&2; exit 2 ;;
esac
