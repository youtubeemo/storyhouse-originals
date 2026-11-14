# Stimmen, Sounddesign & Untertitel – EP01 THE REFLECTION

Alle Werte (Zeiten, Pegel, Prompts) stehen in [`episode.yaml`](episode.yaml) und werden von der Pipeline automatisch verwendet.

## 1. Stimmen

| Figur | Engine | Preset | Regie | Post-FX |
|---|---|---|---|---|
| MAYA | ElevenLabs Text to Speech, Modell `eleven_v3` | „Sarah“ (`EXAVITQu4vr4xnSDxMaL`) | atemlos, ängstlich, nah am Mikro, US-Englisch | trocken, leichte Raumfärbung |
| THE REFLECTION | dito | „Sarah“ (gleiche Stimme!) | intimes, drängendes Flüstern | `mirror_voice`: Hochpass 180 Hz, Tiefpass 7 kHz, kurzes Glas-Echo – klingt „hinter Glas“ |
| THE OTHER | dito | „Sarah“ | sanft, warm, fast freundlich | `other_voice`: minimaler Raum – zu sauber, zu nah |

- **Eine Stimme für alle drei Figuren** ist dramaturgisch Absicht: Es ist immer „Maya“. Unterschiede entstehen durch Regie-Tags (`[whispers]`, `[softly]`) und Post-FX.
- `eleven_v3` unterstützt Ausdrucks-Tags wie `[whispers]`; der Untertitel nutzt den sauberen `text`, die TTS den `tts_text`. Sprache wird mit `language_code: "en"` fixiert, `stability: 0.5` (Natural), fester `seed` je Zeile. Kosten: 1 ElevenLabs-Credit je Zeichen.
- Der Preflight prüft kostenlos, ob die Stimme im Konto verfügbar ist. Ausweichweg über Runway: Preset `Maya` (`--audio-provider runway`).
- **Timing:** Jede Zeile wird automatisch von Stille befreit und in ihr Zeitfenster eingepasst (max. 1,25× schneller, sonst weich gekürzt).
- **Lippensynchronität:** Phase 1 nutzt Einstellungen, in denen Flüstern mit kaum sichtbaren Lippen glaubwürdig ist (Nahaufnahmen im Spiegel, Rücken, Profil). Phase 2 (optional, nach Freigabe): Runway *Act-Two* (`character_performance`, ca. 5 Credits/s) mit einer selbst gefilmten Handy-Performance für S01, S05 und S07.
- Umbesetzen: nur `voice_id` in `episode.yaml` ändern (jede Stimme aus „My Voices“ des Kontos).

## 2. Musik

- **Option A (empfohlen für Kinoqualität):** lizenzierte Trailer-Musik (Artlist, Epidemic Sound o. ä., Lizenz muss Social-Media-Nutzung abdecken) als `assets/music/ep01_score.wav` ablegen. Die Datei wird nicht automatisch geladen und nicht ins Repo eingecheckt, solange die Lizenz das nicht erlaubt.
- **Option B (Standard, automatisch):** Drone aus ElevenLabs Sound Effects (`loop: true`) als Musikbett.
- **Option C (automatisch, nach Freigabe):** Score mit Eleven Music (`music_v1`, instrumental, exakt Filmlänge). Erst aktiv, wenn `elevenlabs.music_license_confirmed: true` per PR gesetzt ist (siehe `docs/ANLEITUNG.md` 5.1). Die Musik wird an der Loop-Naht 0,4 s ein-/ausgeblendet; die Drone läuft durch.
- **Musikdramaturgie:** Drone von Frame 1 (Loop-Naht) → hoher Streicher-Flageolett (S02) → Halbtonbend (S03) → 60-BPM-Puls (S04/S05) → **harter Schnitt in Stille** (S06, 25,6–27,4 s) → Orchester-Sting auf dem Twist (29,5 s) → Drone kehrt auf exakt dem Pegel von 0:00 zurück (Loop).
- **Ducking:** Musik/Drone werden unter Dialog per Sidechain-Kompressor abgesenkt.

## 3. Soundeffekte

17 SFX + 1 Drone + 1 Outro-Sting, alle per Prompt definiert (ElevenLabs `sound-generation`, Modell `eleven_text_to_sound_v2`, 40 Credits pro Sekunde; die Dauer wird immer angegeben, 0,5–30 s). Wichtigste Akzente:

| Zeit | Effekt | Zweck |
|---|---|---|
| 0,0 s | Röhrenbrummen + Zähneputzen | sofort „echter Raum“, intim |
| 9,0 s | Türknarren nur auf Spiegelseite | erster Beweis: der Spiegel lügt |
| 13–16 s | Atem am Glas, Handabdruck | körperliches Unbehagen |
| 25,6–27,4 s | **absolute Stille** | falsche Erleichterung, maximaler Kontrast |
| 29,5 s | Sub-Drop + Orchester-Sting | Twist-Impact |
| 34,9 s | Lichtschalter | Punkt hinter dem Twist |

## 4. Mischung & Mastering (FFmpeg)

1. Dialog-Spuren zeitgenau platzieren (`adelay`), Figuren-FX anwenden.
2. SFX und Drone mit Pegeln aus `episode.yaml` platzieren.
3. Musikbett per `sidechaincompress` unter dem Dialog absenken.
4. Stille-Fenster hart stummschalten.
5. Master auf **−14 LUFS integriert, −1 dBTP** (`loudnorm`) – TikTok-Standard.
6. **Kein Fade-in, kein Fade-out** auf der Summe – sonst hört man die Loop-Naht.
7. Danach wird die Outro-Tonspur (Sting, 2,5 s, ausgeblendet) angehängt.

## 5. Untertitel

- Werden automatisch aus `dialogue` erzeugt: `.srt` (Sidecar, für TikTok-Captions) und `.ass` (gestylt, eingebrannt).
- Stil: Inter Display, 64 px, Weiß mit 4 px schwarzer Kontur, zentriert.
- **TikTok-Safe-Zone:** 560 px Abstand von unten (über Beschreibung/Buttons), 140 px seitlich.
- Mindestanzeigedauer 1,0 s, Lesegeschwindigkeit max. 15 Zeichen/s – wird in den Tests geprüft.
- Der Loop-Satz „Whatever you do—“ endet exakt bei 40,0 s.
