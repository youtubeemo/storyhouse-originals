# Abgleich mit der offiziellen Runway-API (Stand 14.11.2026)

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
