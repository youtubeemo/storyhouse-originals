# Anleitung – STORYHOUSE ORIGINALS Pipeline

Vom kostenlosen Testvideo bis zum fertigen Film – ohne selbst zu schneiden.
**Bis einschließlich Schritt 4 entstehen keinerlei Kosten.**
API-Abgleich und Restrisiken: [`API_ABGLEICH.md`](API_ABGLEICH.md).

> **Kurzfassung – so startest du die Produktion**
> 1. Actions → **Film produzieren** → `mode = dry-run` → Ergebnis ansehen (0 €).
> 2. Einmalig einrichten: Environment `paid-render` mit `RUNWAYML_API_SECRET`, `ELEVENLABS_API_KEY`, `ARTIFACT_PASSPHRASE` (Schritt 4).
> 3. Freigabe-PR: `paid_rendering_enabled: true` (Schritt 5).
> 4. Actions → **Film produzieren** auf `main` → `mode = paid-render`, `profile = final`, `scenes = all`, Phrase eingeben → **Approve** (Schritt 6).
> 5. Artefakt laden und entschlüsseln: fertiges MP4 (1080 × 1920, Untertitel, Outro) + Loop-Fassung + SRT.

---

## 1. Wie die Pipeline arbeitet

```
episode.yaml ─► Plan (Jobs, Preise, Spezifikationsprüfung) ─► Budget ─┬► Runway API (Bild, Video) ──┐
                                                                │      └► ElevenLabs API (Ton) ─────┤
                                                  Ledger (Branch budget-ledger)                     ▼
                                                                                  FFmpeg: Schnitt · Sync · Grading · Mix
                                                                                  Untertitel · −14 LUFS · Loop-Naht · Outro
                                                                                                    ▼
                                                                                  MP4 (Film + Outro) · MP4 (Loop) · SRT
```

1. **Referenzen:** Casting-Porträt von Maya und das Badezimmer-Set (`gen4_image`, Tags `@maya`, `@bathroom`).
2. **Keyframes:** ein Standbild pro Szene mit bis zu 3 Referenzen. Bilder werden als JPEG-Data-URI weitergegeben (keine Abhängigkeit von ablaufenden Runway-URLs).
3. **Bewegung:** `image_to_video` (Test `gen4_turbo`, Final `gen4.5`), `720:1280`, danach Upscale auf 1080 × 1920.
4. **Ton (ElevenLabs direkt, Secret `ELEVENLABS_API_KEY`):**
   - **Dialoge:** 6 englische Zeilen mit `eleven_v3` (Stimme „Sarah“, Regie-Tags wie `[whispers]`).
   - **Soundeffekte:** 17 Horror-SFX + Drone + Outro-Sting mit `eleven_text_to_sound_v2`, immer mit fester Dauer.
   - **Musik:** Score mit Eleven Music (`music_v1`, instrumental, 40 s) – **erst nach Lizenzfreigabe** (Schritt 5.1). Bis dahin trägt die Drone als Musikbett; alternativ eine lizenzierte Datei in `assets/music/`.
   - Ausweichweg ohne ElevenLabs-Konto: `--audio-provider runway` (Ton über Runways Proxy, ohne Musik).
5. **Automatische Synchronisation (FFmpeg):** Jede Szene wird bildgenau auf ihre Dauer geschnitten. Jede Dialogzeile wird von Stille befreit, exakt auf ihren Timecode gesetzt und – falls die Stimme länger spricht als Platz ist – tonhöhenneutral bis 1,25× beschleunigt, notfalls weich gekürzt. Untertitel richten sich nach der tatsächlichen Sprechdauer. Am Ende prüft die Pipeline, dass Bild- und Tonspur gleich lang sind (Abweichung > 0,1 s = Abbruch). Der Bericht steht in `manifest.json` (`sync`, `av_drift_s`).
6. **Outro:** StoryHouse-Karte (2,5 s, in `episode.yaml` unter `outro` einstellbar) wird angehängt. Zusätzlich entsteht eine **Loop-Fassung ohne Outro** (letzter Frame = erster Frame).
7. **Loop:** `gen4.5` kann laut API kein End-Keyframe. Die Pipeline blendet daher die letzten 0,5 s bildgenau in den ersten Frame über (`ffmpeg_seam`). Optional: S08 mit `veo3.1_fast` und End-Keyframe (`strategy: keyframe_last` in `episode.yaml`; in der Finalfassung gleich teuer wie gen4.5 – je 60 Credits –, im Testprofil +35 Credits).

## 2. Kostenlos testen – lokal

Voraussetzungen: Python ≥ 3.10, FFmpeg mit libass, openssl.

```bash
pip install -r requirements.txt
python -m storyhouse validate                       # Timing, Prompts, Referenzen, Loop
python -m storyhouse estimate --profile test        # was ein Testlauf kosten würde
python -m storyhouse estimate --profile final
python -m storyhouse dry-run --preview              # schnelles Vorschauvideo (360×640)
python -m storyhouse dry-run                        # volle 1080×1920, ca. 2 Min.
python -m storyhouse dry-run --scenes S06-S08       # nur Twist + Loop
pytest -q
```

Der Dry-Run öffnet **keine einzige Netzwerkverbindung** (per Test abgesichert) und braucht keinen Schlüssel. Ergebnis in `output/dry-run/`:

| Datei | Inhalt |
|---|---|
| `…_dryrun.mp4` | Film + Outro (42,5 s), Untertitel eingebrannt |
| `…_dryrun_loop.mp4` | Loop-Fassung ohne Outro (40,0 s) |
| `….srt` / `….ass` | Untertitel (Sidecar / gestylt) |
| `manifest.json` | Sync-Bericht je Dialogzeile, Bild/Ton-Abweichung, Musikquelle |
| `estimate.md`, `plan.json` | Kosten und alle API-Aufrufe, die ein bezahlter Lauf senden würde |

Stimmen, SFX und Musik sind im Dry-Run Platzhaltertöne – Schnitt, Timing, Mischung, Untertitel und Outro sind echt.

## 3. Kostenlos testen – GitHub Actions

1. **Actions** → **Film produzieren** → **Run workflow** → `mode = dry-run` → **Run**.
2. Nach ca. 3–5 Minuten: Lauf öffnen → **Artifacts** → `dryrun-…`.

Der Dry-Run-Job sieht keine Secrets und keine Umgebung. Hinweis: Im öffentlichen Repo kann jede angemeldete GitHub-Person dieses Artefakt laden – es enthält aber nur, was ohnehin im Repo steht.

> **Empfehlung:** Erst diesen Schritt ausführen und das Video abnehmen, bevor irgendetwas anderes eingerichtet wird.

## 4. Einmalige Einrichtung für bezahlte Läufe (kostenlos)

### 4.1 Runway
1. Developer Portal (dev.runwayml.com) → Projekt → API-Key erzeugen.
2. **Nur so viel Guthaben kaufen, wie gebraucht wird** (Mindestaufladung beachten; für Test- und Finalfassung reichen ca. 15 USD). Das Guthaben ist die äußere, unabhängige Obergrenze. Die Pipeline warnt, wenn das Guthaben deutlich über dem Restbudget liegt.
3. API-Key nur für **dieses eine Projekt** verwenden – fremde Ausgaben würden den Kontostand-Abgleich verfälschen (die Pipeline würde dann eher zu viel als zu wenig verbuchen).

### 4.2 ElevenLabs
1. Der Schlüssel liegt bereits als Environment-Secret `ELEVENLABS_API_KEY` in `paid-render`. Er braucht die Berechtigungen **Text to Speech, Sound Effects, Music** sowie lesend **User** und **Voices** (für die kostenlose Kontoprüfung).
2. **Tarif:** Die Pipeline schließt kein Abo ab und kauft kein Guthaben. Der Free-Tarif enthält **keine kommerzielle Lizenz** (Namensnennung nötig, keine Monetarisierung) – der Preflight verweigert ihn deshalb, solange `elevenlabs.require_commercial_license: true` gilt. Für eine Veröffentlichung braucht ihr mindestens **Starter** (Stand 14.11.2026: 6 USD/Monat). Ob und wann ihr das bucht, entscheidet ihr.
3. **Nutzungsbasierte Abrechnung / Overage / automatisches Aufladen im ElevenLabs-Konto AUS lassen.** Der Preflight liest die Einstellung aus (`max_credit_limit_extension` muss 0 sein) und bricht sonst ab. Ein eventuelles automatisches Aufladen von „Pay as you go“-Guthaben ist über die API nicht sichtbar – das bitte einmal von Hand in den Abrechnungseinstellungen prüfen.
4. Bedarf: ein kompletter Lauf verbraucht rund 3.200 Credits (mit Musik rund 3.800). Test S01 + Test komplett + Final komplett ≈ 8.600 Credits.
5. Stimme vorher im ElevenLabs-Dashboard anhören: „Sarah“ (`EXAVITQu4vr4xnSDxMaL`). Umbesetzen = `voice_id` in `episode.yaml` ändern.

### 4.3 GitHub (dein Repo ist öffentlich → Required Reviewers sind in jedem Tarif verfügbar)
1. **Settings → Environments → New environment** → `paid-render`
   - **Required reviewers:** dich selbst. „Prevent self-review“ **aus** lassen, sonst kannst du als einzige Person nie freigeben.
   - **Deployment branches and tags:** „Selected branches“ → nur `main`. (Wichtig: Sonst könnte ein Lauf auf einem anderen Branch mit verändertem Workflow an die Secrets.)
   - **Environment secrets:**
     - `RUNWAYML_API_SECRET` – dein Runway-Key
     - `ELEVENLABS_API_KEY` – bereits hinterlegt
     - `ARTIFACT_PASSPHRASE` – langes Zufallspasswort (≥ 16 Zeichen) zur Verschlüsselung der Ergebnisse. Sicher aufbewahren (Passwortmanager).
   - **Nicht** als Repository-Secret anlegen.
2. **Settings → Branches → Regel für `main`:** Pull Request vor dem Merge verlangen. (Als Admin kannst du sie umgehen – bei einer Ein-Personen-Produktion in Ordnung, schützt aber vor versehentlichen Direkt-Pushes.)
3. **Settings → Actions → General:** Freigabe für Workflows aus Fork-PRs externer Mitwirkender verlangen (Standard bei öffentlichen Repos).
4. Wird das Repo später **privat**, funktionieren Required Reviewers im Free/Pro-Tarif nicht mehr. Dann bleiben als Sperren: Konfigurationsschalter, Bestätigungsphrase, Budget – oder ein Upgrade auf Enterprise.

## 5. Vor dem ersten bezahlten Render – Checkliste

- [ ] Dry-Run (Schritt 3) angesehen und abgenommen
- [ ] Preise in `config/pricing.yaml` noch aktuell (Preisseite öffnen; `verified_on` älter als 45 Tage = Sperre)
- [ ] ElevenLabs-Stimme „Sarah“ angehört
- [ ] Environment `paid-render` inkl. aller drei Secrets eingerichtet, nur `main` erlaubt
- [ ] Runway-Guthaben aufgeladen (Preflight prüft, dass es reicht)
- [ ] ElevenLabs: Tarif mit kommerzieller Lizenz, Overage/automatisches Aufladen aus, genug Credits (Preflight prüft Tarif, Overage, Kontingent, Stimme)
- [ ] **Freigabe durch die Produktion:** eigener PR, der `paid_rendering_enabled: true` setzt

### 5.1 Musik freigeben (optional, eigener PR)

Eleven Music ist laut ElevenLabs in bezahlten Tarifen für „broad commercial use“ lizenziert; **Film-, TV- und große Spielproduktionen brauchen laut ElevenLabs einen Enterprise-Tarif.** Ob ein Social-Media-Kurzfilm unter „Film“ fällt, konnte nicht abschließend geprüft werden (die Seite mit den Music Terms war beim Abgleich nicht abrufbar). Deshalb ist Musik gesperrt, bis ihr die Bedingungen (elevenlabs.io/music-terms, elevenlabs.io/eleven-music-v1-terms) selbst gelesen habt und per PR `elevenlabs.music_license_confirmed: true` in `config/budget.yaml` setzt. Im Prompt stehen keine Künstler-, Songnamen oder Songtexte (laut ElevenLabs unzulässig).

Ohne Freigabe entsteht trotzdem ein vollständiger Film – mit Drone als Musikbett. Rechtssichere Alternative: lizenzierte Datei als `assets/music/ep01_score.wav`.

## 6. Bezahlten Lauf starten

**Actions → Film produzieren → Run workflow** auf Branch `main`, `mode = paid-render`, `approval = ICH GEBE DIESEN RENDER FREI`, dann im Lauf **Review deployments → Approve**.

| Schritt | Kosten | Prüft |
|---|---|---|
| Tests + **Generalprobe** (Dry-Run der gewählten Szenen) | 0 € | Pipeline funktioniert |
| Ledger holen, Budgetstand | 0 € | — |
| **Preflight** | 0 € | Schalter, Phrase, alle Secrets vorhanden, Preisalter, ungeprüfte Modelle, offene Reservierungen, Lauf- und Gesamtbudget, **Runway-Guthaben**, **ElevenLabs-Konto** (Tarif/Lizenz, Kontingent, Overage aus, Stimme vorhanden) – alles kostenlose Abfragen |
| **Reservierung** auf `budget-ledger` gepusht | 0 € | Push muss gelingen, sonst Abbruch |
| **Render** | ✔ | vor jedem Aufruf: Spezifikation + Budget (ein gemeinsames Euro-Limit für Runway und ElevenLabs); danach: gemeldete Kosten vs. Preistabelle (Abweichung → Stopp); dann Schnitt, Sync, Untertitel, Outro |
| **Verbuchung** (läuft immer) | 0 € | Runway: max(Task-Kosten, Kontostand-Differenz) · ElevenLabs: max(eigenes Protokoll, Verbrauchs-Differenz des Kontos) |
| **Verschlüsselung + Upload** | 0 € | nur `.enc`-Dateien + Kostenprotokoll |

Empfohlene Reihenfolge (inkl. 19 % USt. und 25 % Puffer): 1) `test`, `S01` (≈ 1,04 €) · 2) `test`, `all` (≈ 4,85 €) · 3) `final`, `all` (≈ 10,35 €, mit Musik ≈ 10,53 €). Der ElevenLabs-Anteil ist dabei der konservativ bewertete Credit-Verbrauch; die Abo-Gebühr kommt separat dazu.

**Im Artefakt (verschlüsselt):** `…_final.mp4` (Film + Outro), `…_final_loop.mp4` (Loop-Fassung), `.srt`, `.ass`; unverschlüsselt nur Kostenprotokolle und `manifest.json`. Es wird nichts veröffentlicht – Hochladen zu TikTok & Co. bleibt euer Schritt.

**Ergebnis entschlüsseln:**
```bash
gh run download <RUN_ID> -n render-<RUN_ID>-encrypted
openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 \
  -in tos-ep01-the-reflection_final.mp4.enc -out film.mp4 -pass pass:'DEINE_PASSPHRASE'
```

## 7. Budget im Blick

```bash
git fetch origin budget-ledger && git show origin/budget-ledger:ledger.json
```

**Fehlerfälle und was die Pipeline tut:**

| Fall | Verhalten |
|---|---|
| Verbindungsabbruch / 5xx beim **Anlegen** eines Tasks | Kosten bleiben verbucht, **keine Wiederholung** (die API hat keinen Idempotency-Key), Lauf stoppt |
| Rate-Limit (429) | Warten und erneut senden – Task wurde sicher nicht angelegt |
| ElevenLabs: Verbindungsabbruch / 5xx bei einer Generierung | Kosten bleiben verbucht, **keine Wiederholung**, Lauf stoppt (auch für Runway) |
| ElevenLabs lehnt ab (4xx: Kontingent, Tarif, Stimme) | 0 Credits, Lauf stoppt – es wird **nichts nachgekauft** |
| ElevenLabs meldet deutlich höhere Kosten als die Tabelle | Lauf stoppt, Preise prüfen |
| Stimme spricht länger als ihr Zeitfenster | automatisch beschleunigt (max. 1,25×), notfalls gekürzt; steht im Sync-Bericht |
| Validierungsfehler (4xx) | 0 Credits, Lauf stoppt |
| Runway meldet höhere Kosten als die Tabelle | Task wird storniert, Lauf stoppt, Preise prüfen |
| Task FAILED | Kosten laut Runway (`cost.credits`), keine automatische Neueinreichung |
| Statusabfrage gestört | kostenlos → bis zu 6 Wiederholungen mit Wartezeit |
| Zeitüberschreitung | Task wird storniert, Kosten bleiben verbucht |
| Lauf abgebrochen („Cancel“) | Verbuchung läuft trotzdem |
| Runner abgestürzt | Reservierung bleibt offen und zählt voll; **neue Läufe sind gesperrt**, bis sie abgeschlossen ist (unten) |
| Zwei Läufe gleichzeitig | Concurrency-Gruppe + Sperre bei offener Reservierung |
| Ledger beschädigt | Abbruch (fail-closed) |

**Hängende Reservierung abschließen** (Verbrauch vorher im Runway Developer Portal unter *Usage* ablesen):
```bash
bash scripts/ledger.sh pull
python -m storyhouse close-run --ledger .ledger/ledger.json --run-id run-123-1 \
  --spent-credits 250 --reason "Runner abgestürzt, Usage geprüft"
bash scripts/ledger.sh push "close run-123-1"
```

Das Gesamtbudget ist im Code auf **höchstens 200 €** gedeckelt.

## 8. Eine Szene ändern

Alles steht in `episode.yaml`. Danach: `python -m storyhouse validate && pytest -q`.

## 9. Fehlerbehebung

| Meldung | Bedeutung |
|---|---|
| `Bezahltes Rendern ist gesperrt` | gewollt bis zur Freigabe per PR |
| `Bestätigungsphrase …` | Eingabe `approval` exakt wie in `budget.yaml` |
| `ARTIFACT_PASSPHRASE fehlt` | Environment-Secret anlegen (≥ 16 Zeichen) |
| `Preistabelle ist … Tage alt` | Preise prüfen, `verified_on` per PR aktualisieren |
| `Ungeprüfte Preise für …` | Modell hat `verified: false` |
| `Offene Reservierung(en)` | vorherigen Lauf mit `finalize`/`close-run` abschließen |
| `Runway-Guthaben … < benötigt` | Guthaben aufladen |
| `ELEVENLABS_API_KEY ist nicht gesetzt` | Secret im Environment `paid-render` prüfen |
| `ElevenLabs-Konto ist im Free-Tarif` | keine kommerzielle Lizenz – Tarifentscheidung der Produktion (4.2) |
| `nutzungsbasierte Abrechnung (Overage) ist aktiv` | im ElevenLabs-Konto deaktivieren |
| `ElevenLabs-Kontingent reicht nicht` | weniger Szenen oder nächsten Abrechnungszeitraum abwarten – es wird nicht nachgekauft |
| `ElevenLabs-Stimme … nicht verfügbar` | `voice_id` in `episode.yaml` anpassen |
| `Bild und Ton laufen … auseinander` | Export verworfen – Fehlerbericht an die Technik |
| `… > Lauf-Limit` / `… Gesamtbudget` | weniger Szenen, `profile=test`, oder Budgetentscheidung |
| `SpecError …` | Payload würde von Runway abgelehnt – `episode.yaml`/Profil anpassen |
| Paid-Job wird übersprungen | Lauf nicht auf `main` gestartet |
