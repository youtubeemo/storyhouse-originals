# Abgleich mit den offiziellen APIs: Runway und ElevenLabs (Stand 14.11.2026)

Teil A (Abschnitte 1–5): Runway – Bild und Video. Teil B (Abschnitte 6–10): ElevenLabs – Stimmen, Soundeffekte, Musik.

**Quellen**
- Offizielles Python-SDK `runwayml/sdk-python`, Commit `949ac4e` vom 12.11.2026 – wird automatisch aus Runways OpenAPI-Spezifikation generiert (`src/runwayml/types/*_create_params.py`, `task_retrieve_response.py`, `organization_retrieve_response.py`, `resources/tasks.py`).
- Preisseite https://docs.dev.runwayml.com/guides/pricing/
- GitHub-Doku zu Environments: https://docs.github.com/actions/managing-workflow-runs-and-deployments/managing-deployments/managing-environments-for-deployment

Die geprüften Grenzen sind im Code hinterlegt (`storyhouse/runway_spec.py`). Jeder Payload wird **vor** dem Senden dagegen validiert; Tests prüfen jeden geplanten Job.

## 1. Endpunkte & Header

| Zweck | Endpunkt | Status |
|---|---|---|
| Basis-URL | `https://api.dev.runwayml.com`, Header `Authorization: Bearer …`, `X-Runway-Version: 2024-11-06` | ✅ korrekt |
| Bild | `POST /v1/text_to_image` | ✅ |
| Video | `POST /v1/image_to_video` | ✅ |
| Stimme | `POST /v1/text_to_speech` | ✅ |
| Soundeffekt | `POST /v1/sound_effect` | ✅ |
| Musik | `POST /v1/text_to_music` (`eleven_music_v2_5`) | ℹ️ existiert, Preis nicht auf der Preisseite → **gesperrt** |
| Task-Status | `GET /v1/tasks/{id}` – `PENDING`, `THROTTLED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED` | ✅ |
| Stornieren | `DELETE /v1/tasks/{id}` | ➕ neu genutzt |
| Guthaben | `GET /v1/organization` → `creditBalance` (kostenlos) | ✅ |

## 2. Modelle, Formate, Payloads

| Thema | Offizielle Spezifikation | Vorher im PR | Jetzt |
|---|---|---|---|
| `gen4.5` Dauer | Ganzzahl 2–10 s | ✅ | ✅ |
| `gen4.5`/`gen4_turbo` Seitenverhältnis | `720:1280` u. a. (kein 1080:1920) | ✅ 720:1280 | ✅ + Upscale in FFmpeg |
| **Start-/End-Keyframe** | `gen4.5` und `gen4_turbo`: **nur `position: "first"`**. `veo3.1`/`veo3.1_fast`: `first` + `last` | ❌ Loop nahm End-Keyframe bei gen4.5 an | ✅ **Ersatz:** FFmpeg-Loop-Naht (Standard) oder optional `veo3.1_fast` für S08 |
| `veo3.1*` | Dauer nur 4/6/8 s, `audio` ist standardmäßig an (teurer) | – | ✅ Dauer wird gerundet, `audio: false` erzwungen |
| `gen4_image` Seitenverhältnis | `1080:1920` erlaubt | ✅ | ✅ |
| **Bildreferenzen** | `referenceImages`: max. 3, je `{uri, tag}`; Tag 3–16 Zeichen, Buchstabe zuerst, nur A-Z/0-9/_ | ✅ (`maya`, `bathroom`) | ✅ wird validiert |
| `gen4_image_turbo` | Referenzbilder **Pflicht** (1–3) | ✅ Referenzporträts mit `gen4_image` | ✅ wird validiert |
| Bild-Eingaben | HTTPS-URL, Upload-URI oder Data-URI ≤ 5 MB; Task-Outputs sind **ephemere** URLs | ⚠️ ablaufende Output-URLs weitergereicht | ✅ **Ersatz:** Bild wird geladen und als JPEG-Data-URI (≤ 3,5 MB) weitergegeben |
| **Text-to-Speech** | `eleven_v3`, `voice: {type: "runway-preset", presetId}`; 49 Presets inkl. `Maya`; Tags wie `[whispers]`; optional `languageCode` | ✅ | ✅ Preset-Liste validiert, `languageCode: "en"` |
| **Soundeffekte** | `eleven_text_to_sound_v2`, `duration` 0,5–30 s, `loop` | ✅ | ✅ Dauer wird immer gesetzt (billiger und deterministisch) |
| Task-Kosten | Anlage liefert `estimatedCost.credits` (Maximalkosten), Endstatus liefert `cost.credits` (Erstattungen = 0) | ❌ nicht genutzt | ✅ Preisabweichung → Task storniert, Lauf stoppt; Endkosten werden übernommen |
| Idempotenz | Kein Idempotency-Key | ✅ keine Wiederholung | ✅ explizit: unklare Anlage = Stopp |
| Stornieren | Erstattung nur, wenn Task noch nicht `RUNNING`; keine Garantie | – | ✅ wird genutzt, Kosten bleiben vorsichtshalber verbucht |

## 3. Preise (Credits, 1 Credit = 0,01 USD)

| Modell | Offiziell | Vorher | Jetzt |
|---|---|---|---|
| gen4.5 | 12 / s | 12 | 12 ✅ |
| gen4_turbo | 5 / s | 5 | 5 ✅ |
| veo3.1_fast (ohne Audio) | 10 / s | 15 | 10 ✅ |
| veo3.1 (ohne Audio) | 20 / s | 40 | 20 ✅ |
| gen4_image (1080p) | 8 | 8 | 8 ✅ |
| gen4_image_turbo | 2 | 2 | 2 ✅ |
| eleven_v3 | 1 Credit je 50 Zeichen, min. 1 | 0,05/Zeichen (zu hoch) | 1/50 ✅ |
| eleven_text_to_sound_v2 | 1 / s mit Dauer, 2 ohne | pauschal 3 | 1/s (aufgerundet) ✅ |
| eleven_music_v2_5 | nicht gelistet | – | gesperrt (`verified: false`) |

**Neu:** Umsatzsteuer (19 %) wird auf alle Schätzungen aufgeschlagen; Preise, die älter als 45 Tage sind, oder ungeprüfte Modelle blockieren bezahlte Läufe.

**Kosten EP01 (inkl. 19 % USt., Wechselkurs 1:1):**

| Lauf | Schätzung | mit 25 % Puffer |
|---|---:|---:|
| Test, nur S01 | 0,90 € | 1,13 € |
| Test, alle Szenen | 4,07 € | 5,09 € |
| Final, alle Szenen | 8,47 € | 10,59 € |

## 4. GitHub (öffentliches Repo, persönliches Konto)

- Environments, Environment-Secrets und Required Reviewers funktionieren bei **öffentlichen Repos in allen GitHub-Tarifen** (auch Free). Bei **privaten** Repos gibt es Required Reviewers für Free/Pro/Team **nicht** – sie werden beim Umschalten auf privat sogar ignoriert.
- **Folge des öffentlichen Repos:** Drehbuch, Prompts, Workflow-Logs, Ledger und **Artefakte** sind für alle sichtbar bzw. für angemeldete Nutzer herunterladbar. Deshalb werden bezahlte Renderergebnisse jetzt **AES-256-verschlüsselt** hochgeladen (`ARTIFACT_PASSPHRASE`), Runway-URLs werden nicht hochgeladen.
- `main` ist aktuell **nicht geschützt** – siehe Einrichtung in `ANLEITUNG.md`.

## 5. Noch nicht verifizierbar (Restrisiken)

- Ob Runway die von `text_to_image` gelieferten Bilder als Data-URI in genau dieser Größe ohne Qualitätsverlust akzeptiert, zeigt erst der erste echte Lauf (Spezifikation: bis 5 MB erlaubt; wir senden ≤ 3,5 MB).
- Erstattung gescheiterter Tasks ist auf der Preisseite nicht dokumentiert; die API meldet aber `cost.credits` je Task, das wird verbucht.
- Rundung bei `eleven_text_to_sound_v2` (Sekundenbruchteile) ist nicht dokumentiert → wir runden auf.

---

# Teil B – ElevenLabs (Ton)

**Quellen (abgerufen am 14.11.2026)**
- API-Referenz: `elevenlabs.io/docs/api-reference/text-to-speech/convert`, `…/text-to-sound-effects/convert`, `…/music/compose`, `…/user/subscription/get`, `…/introduction`
- Modelle: `elevenlabs.io/docs/overview/models` · Soundeffekte: `elevenlabs.io/docs/overview/capabilities/sound-effects`
- Preise: `elevenlabs.io/pricing/api` · Musik: `elevenlabs.io/music-api`, Cookbook `…/guides/cookbooks/music`

Die geprüften Grenzen stehen in `storyhouse/elevenlabs_spec.py`; jeder Request wird **vor** dem Senden dagegen validiert.

## 6. Endpunkte & Header

| Zweck | Endpunkt | Status |
|---|---|---|
| Basis-URL | `https://api.elevenlabs.io`, Header `xi-api-key: …`, `Content-Type: application/json` | ✅ |
| Stimme | `POST /v1/text-to-speech/{voice_id}?output_format=mp3_44100_128` → Audiodatei | ✅ |
| Soundeffekt | `POST /v1/sound-generation?output_format=mp3_44100_128` → Audiodatei | ✅ |
| Musik | `POST /v1/music?output_format=mp3_44100_128` → Audiodatei | ✅ (nur bezahlte Tarife) |
| Konto | `GET /v1/user/subscription` – `tier`, `status`, `character_count`, `character_limit`, `max_credit_limit_extension`, `current_overage` | ✅ kostenlos |
| Stimme vorhanden? | `GET /v1/voices/{voice_id}` | ✅ kostenlos |
| Kosten je Aufruf | Antwort-Header `character-cost` (dazu `request-id`) | ✅ wird ausgewertet, falls vorhanden |

Alle Generierungen sind **synchron** (kein Task, kein Polling, keine URL): Die Antwort ist die Audiodatei.
Der Code enthält keinen Endpunkt, der ein Abo ändert oder Guthaben kauft (per Test abgesichert).

## 7. Modelle & Parameter

| Thema | Offizielle Spezifikation | Umsetzung |
|---|---|---|
| `eleven_v3` | 5.000 Zeichen je Anfrage, 70+ Sprachen, Regie-Tags | ✅ Limit validiert; Tags nur mit v3 |
| `language_code` | ISO 639-1; wird ignoriert, wenn das Modell ihn nicht kennt; nicht für `multilingual_v2` | ✅ `"en"` nur bei v3 |
| `voice_settings.stability` | Zahl, Standard 0,5 | ✅ 0,5; für v3 bewusst auf 0,0 / 0,5 / 1,0 beschränkt (v3 kennt drei Stufen – eigene, engere Regel) |
| `seed` (TTS) | 0–4294967295, „best effort“ | ✅ fester Seed je Zeile |
| `output_format` | Standard `mp3_44100_128`; `mp3_44100_192` ab Creator, PCM/WAV 44,1 kHz ab Pro | ✅ `mp3_44100_128` (in allen Tarifen) – FFmpeg wandelt nach 48 kHz |
| SFX `duration_seconds` | 0,5–30; ohne Angabe schätzt das Modell | ✅ **immer gesetzt** (Kosten und Länge bestimmt) |
| SFX `loop` | nur `eleven_text_to_sound_v2` | ✅ nur v2 erlaubt (`…_v3` existiert, ist hier nicht bepreist → gesperrt) |
| SFX `prompt_influence` | 0–1, Standard 0,3 | ✅ 0,3 |
| Musik `music_length_ms` | 3.000–600.000, nur mit `prompt` | ✅ exakt Filmlänge (40.000), Minimum 3.000 |
| Musik `model_id` | `music_v1` (Standard), `music_v2`, `music_v2_5` | ✅ `music_v1` |
| Musik `force_instrumental` | nur mit `prompt` | ✅ immer `true` |
| Musik `seed` | **nicht** zusammen mit `prompt` | ✅ wird nicht gesendet (validiert) |
| Wiederholung | kein Idempotency-Key dokumentiert | ✅ nie blind wiederholen; nur bei 429 |

## 8. Preise & Kostenberechnung

ElevenLabs nennt zwei Preismodelle. Die Pipeline rechnet mit dem **teureren** (Credits), bewertet mit dem teuersten Credit aller Tarife (Starter: 6 USD / 30.000 Credits = 0,0002 USD):

| Produkt | Credits (Abo-Kontingent) | = USD in der Pipeline | Offizieller API-Listenpreis | Quelle Credits |
|---|---|---|---|---|
| Sprache `eleven_v3` | 1 je Zeichen | 0,20 $ / 1.000 Zeichen | 0,08 $ / 1.000 Zeichen | ElevenLabs-Hilfe |
| Soundeffekte | 40 je Sekunde (mit Dauer) | 0,48 $ / min | 0,12 $ / min | ElevenLabs-Doku |
| Musik | 15 je Sekunde (900 / min) | 0,18 $ / min | 0,15 $ / min | Tarifübersichten Dritter |

Preise verstehen sich laut ElevenLabs **ohne Steuern** – die Pipeline schlägt 19 % USt. und 25 % Puffer auf.

**Kosten EP01 mit ElevenLabs-Ton (inkl. 19 % USt., Wechselkurs 1:1):**

| Lauf | Runway | ElevenLabs | Summe | mit 25 % Puffer | EL-Credits |
|---|---:|---:|---:|---:|---:|
| Test, nur S01 | 0,45 € | 0,38 € | 0,84 € | 1,04 € | 1.612 |
| Test, alle Szenen | 3,12 € | 0,76 € | 3,88 € | 4,85 € | 3.187 |
| Final, alle Szenen | 7,52 € | 0,76 € | 8,28 € | 10,35 € | 3.187 |
| Final + Musik | 7,52 € | 0,90 € | 8,42 € | 10,53 € | 3.787 |

Die Tabellen in Abschnitt 3 gelten für den Ausweichweg `--audio-provider runway`.

**Budgetgrenzen (unverändert, jetzt anbieterübergreifend):** 200 € gesamt (im Code gedeckelt), 25 € je Lauf, Reservierung vor jeder Ausgabe, nur ein offener Lauf. Runway und ElevenLabs teilen sich **ein** Euro-Limit je Lauf; stoppt ein Anbieter (Preisabweichung, unklarer Aufruf), stoppt der ganze Lauf. Verbucht wird je Anbieter das Maximum aus eigenem Protokoll und Konto-Differenz.

**Nicht im Ledger:** die ElevenLabs-Abo-Gebühr. Sie wird direkt von ElevenLabs berechnet und muss von Hand ins Budget.

## 9. Tarif & Recht

| Punkt | Stand laut ElevenLabs | Folge in der Pipeline |
|---|---|---|
| Free-Tarif | 10.000 Credits/Monat, **keine kommerzielle Lizenz**, Namensnennung nötig | Preflight verweigert Free (`require_commercial_license: true`) |
| Starter und höher | kommerzielle Lizenz für Sprache; Soundeffekte „royalty-free“ | erlaubt |
| Eleven Music | API nur für bezahlte Tarife; „broad commercial use“ in bezahlten Tarifen; **Film/TV/große Spiele: Enterprise** | Musik gesperrt bis `music_license_confirmed: true`; bei Free immer verweigert |
| Musik-Prompts | keine Künstlernamen, Songtitel, Songtexte | Prompt enthält keine |
| Nutzungsbasierte Abrechnung | `max_credit_limit_extension`: `0` = aus, sonst an bzw. `"unlimited"` | ≠ 0 oder laufende Overage → Abbruch. Nicht konfigurierbar. |
| Abo / Guthaben kaufen | — | macht die Pipeline nie; reicht das Kontingent nicht, bricht sie ab |

## 10. Noch nicht verifizierbar (Restrisiken ElevenLabs)

- **Es wurde kein einziger echter ElevenLabs-Aufruf gemacht** (weder kostenpflichtig noch kostenlos; der Schlüssel liegt nur im Environment). Alle Tests laufen gegen einen simulierten Transport. Der erste echte Lauf sollte deshalb `test` / `S01` sein.
- Die **Music Terms** (`elevenlabs.io/music-terms`, `…/eleven-music-v1-terms`) waren beim Abgleich nicht abrufbar; die Aussagen zur Musiklizenz stammen von der Produktseite. Daher die manuelle Freigabe.
- Ob `character-cost` auch bei Soundeffekten und Musik gesendet wird, ist nicht dokumentiert. Fehlt der Header, bleibt die (konservative) Schätzung stehen; zusätzlich zählt die Verbrauchs-Differenz des Kontos.
- Der Credit-Verbrauch von Musik (900/min) ist nicht offiziell dokumentiert, nur der USD-Listenpreis.
- Konten mit reinem „Pay as you go“-Guthaben: Ob `character_limit` dort das Guthaben abbildet, ist nicht dokumentiert. Meldet die API zu wenig Kontingent, bricht der Preflight ab (fail-closed). Ein automatisches Aufladen solcher Guthaben ist über die API nicht sichtbar.
- Die Stimme „Sarah“ (`EXAVITQu4vr4xnSDxMaL`) ist eine ElevenLabs-Standardstimme; ob sie in eurem Konto verfügbar ist, prüft der Preflight vor dem ersten Aufruf.
- Die Beschränkung der v3-`stability` auf drei Stufen ist eine eigene Vorsichtsregel, nicht aus der API-Referenz.
