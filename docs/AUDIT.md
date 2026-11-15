# Audit StoryHouse Originals (Stand 15.11.2026)

Repository: `youtubeemo/storyhouse-originals` (öffentlich). Werkzeugübersicht: [`config/providers.yaml`](../config/providers.yaml) (`python -m storyhouse tools`).

## Was funktioniert bereits?
- **Vollautomatische Produktion** „Film produzieren“: Runway (Bild/Video) → ElevenLabs (Stimmen, SFX) → FFmpeg (Sync, Mischung −14 LUFS, Untertitel, Loop, StoryHouse-Outro) → verschlüsseltes Artefakt.
- **Erster echter Render** (Lauf #3, `test`/`S01`): erfolgreich, **0,65 € verbucht** (davon ElevenLabs 816 Credits, Free-Tarif). Ergebnis ist als privater, nicht kommerzieller Test gekennzeichnet.
- **iPhone-Player** (gemergt): entschlüsselt Ergebnisse im Browser, ohne Netzwerkzugriff.
- Sicherungen: Budget 25 €/Lauf, 200 € gesamt, Reservierung, Freigabe-Umfang (`test`/`S01`), Phrase + Deployment-Freigabe, ElevenLabs vor Runway, Overage-Sperre, Musik-Sperre.

## Vollständig integrierte Programme
Runway · ElevenLabs · FFmpeg · Python · GitHub Actions (öffentlich, kostenlos).

## Was fehlt / verbesserbar
| Ziel | Stand | Nächster Schritt |
|---|---|---|
| Konsistente Charaktere | Referenzbild `@maya` + Tags in jedem Keyframe | Mehr Referenzwinkel (Profil, Halbtotale) als zusätzliche Tags |
| Realistische Kamerabewegungen | in `motion_prompt` je Szene | Kamera-Vokabular als feste Bausteine pro Szene |
| Lippensync bei Dialog | bewusst Flüstern/Nahaufnahmen ohne sichtbare Lippen | **Hedra Character-3** für Dialog-Nahaufnahmen (vorbereitet, ~5 ¢/s bei 720p) |
| Exakte Untertitel | Zeit aus gemessener Sprechdauer | **faster-whisper** (kostenlos) für Wort-Zeitstempel |
| Musik | Eleven Music eingebaut, aber gesperrt | Lizenzfrage klären (Film/TV = Enterprise laut ElevenLabs) |
| Günstigeres Video | gen4.5 12 Credits/s | **Hailuo** A/B-Test (768p 6 s: 0,28 $) – nur mit Freigabe |
| Sichere MP4-Bereitstellung | verschlüsselt + iPhone-Player | PR #5 (unverschlüsselt nur bei privatem Repo) ist offen, braucht GitHub Pro |

## Schwachstellen
- **Free-Tarif ElevenLabs:** Material aus Lauf #3 darf nicht veröffentlicht werden (nicht kommerziell, Namensnennung).
- **Öffentliches Repo:** Drehbuch, Prompts, Logs und Ledger sind öffentlich (Ergebnisse nur verschlüsselt).
- **Runway-Guthaben** laut Preflight 1.000 Credits (= 10 $): reicht für Tests, nicht für den Final-Render des ganzen Films mit Nachdrehs.
- Preisdaten veralten nach 45 Tagen automatisch (Sperre) – nächste Prüfung spätestens 29.12.2026.

## Kosten (Hochrechnung aus dem echten Plan, `python -m storyhouse kosten`)
| Länge | Profil | Runway-Credits | ElevenLabs-Credits | Kosten inkl. USt. | mit Puffer | Läufe (≤ 25 €) | ElevenLabs-Tarif | im 200-€-Budget |
|---|---|---:|---:|---:|---:|---:|---|---|
| 1 min | test | 590 | 6.817 | 8.64 € | 10.80 € | 1 | Starter (6 $/Monat) | ja |
| 5 min | test | 2.948 | 34.083 | 43.19 € | 53.98 € | 3 | Creator (22 $/Monat) | ja |
| 10 min | test | 5.895 | 68.166 | 86.37 € | 107.97 € | 5 | Creator (22 $/Monat) | ja |
| 1 min | final | 1.422 | 6.817 | 18.54 € | 23.18 € | 1 | Starter (6 $/Monat) | ja |
| 5 min | final | 7.110 | 34.083 | 92.72 € | 115.90 € | 5 | Creator (22 $/Monat) | ja |
| 10 min | final | 14.220 | 68.166 | 185.44 € | 231.80 € | 10 | Creator (22 $/Monat) | NEIN |

Nachdrehquote Video 1.5×, Ton 1,2×; Basis: tos-ep01-the-reflection (40 s). ElevenLabs-Abo kommt zusätzlich; mit Abo sind die ElevenLabs-Credits im Monatsbetrag enthalten.

Lesart: „Kosten inkl. USt.“ enthält den Runway-Anteil und den ElevenLabs-Verbrauch zum konservativen Credit-Preis. Mit ElevenLabs-Abo ist der ElevenLabs-Anteil im Monatsbetrag enthalten; realistisch zahlst du dann **Runway-Anteil + Abo**:

| Länge | Runway (final, inkl. USt., 1,5× Nachdrehs) | Runway (test) | ElevenLabs-Abo |
|---|---:|---:|---|
| 1 Minute | ≈ 17 € | ≈ 7 € | Starter 6 $/Monat |
| 5 Minuten | ≈ 85 € | ≈ 35 € | Creator 22 $/Monat |
| 10 Minuten | ≈ 169 € | ≈ 70 € | Creator 22 $/Monat |

10 Minuten in Final-Qualität sprengen mit Puffer das 200-€-Gesamtbudget und brauchen ~10 Läufe (25-€-Grenze).

## Automatisierungen
Bereits vollautomatisch von `episode.yaml` bis MP4. Möglich: Hedra-Schritt für Dialog-Nahaufnahmen, Whisper-Untertitel, Rea-Video-Analyse von Referenzfilmen (nur zum Lernen).
