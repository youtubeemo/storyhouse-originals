# Anleitung – STORYHOUSE ORIGINALS Pipeline

Vom kostenlosen Testvideo bis zum ersten freigegebenen Render.
**Bis einschließlich Schritt 4 entstehen keinerlei Kosten.**
API-Abgleich und Restrisiken: [`API_ABGLEICH.md`](API_ABGLEICH.md).

---

## 1. Wie die Pipeline arbeitet

```
episode.yaml ─► Plan (Jobs, Preise, Spezifikationsprüfung) ─► Budget ─► Runway API ─► FFmpeg ─► MP4 + SRT
                                                                │                       │
                                                  Ledger (Branch budget-ledger)   Schnitt · Grading · Mix
                                                                                  Untertitel · −14 LUFS · Loop-Naht
```

1. **Referenzen:** Casting-Porträt von Maya und das Badezimmer-Set (`gen4_image`, Tags `@maya`, `@bathroom`).
2. **Keyframes:** ein Standbild pro Szene mit bis zu 3 Referenzen. Bilder werden als JPEG-Data-URI weitergegeben (keine Abhängigkeit von ablaufenden Runway-URLs).
3. **Bewegung:** `image_to_video` (Test `gen4_turbo`, Final `gen4.5`), `720:1280`, danach Upscale auf 1080 × 1920.
4. **Ton:** Dialoge mit `eleven_v3` (Preset „Maya“, Englisch), 17 SFX + Drone mit `eleven_text_to_sound_v2`.
5. **Loop:** `gen4.5` kann laut API kein End-Keyframe. Die Pipeline blendet daher die letzten 0,5 s bildgenau in den ersten Frame über (`ffmpeg_seam`). Optional: S08 mit `veo3.1_fast` und End-Keyframe (`strategy: keyframe_last` in `episode.yaml`; in der Finalfassung gleich teuer wie gen4.5 – je 60 Credits –, im Testprofil +35 Credits).

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

Der Dry-Run öffnet **keine einzige Netzwerkverbindung** (per Test abgesichert). Ergebnis in `output/dry-run/`: MP4, SRT, ASS, `estimate.md`, `plan.json` (alle API-Aufrufe, die gesendet würden).

## 3. Kostenlos testen – GitHub Actions

1. **Actions** → **Testvideo** → **Run workflow** → `mode = dry-run` → **Run**.
2. Nach ca. 3–5 Minuten: Lauf öffnen → **Artifacts** → `dryrun-…`.

Der Dry-Run-Job sieht keine Secrets und keine Umgebung. Hinweis: Im öffentlichen Repo kann jede angemeldete GitHub-Person dieses Artefakt laden – es enthält aber nur, was ohnehin im Repo steht.

> **Empfehlung:** Erst diesen Schritt ausführen und das Video abnehmen, bevor irgendetwas anderes eingerichtet wird.

## 4. Einmalige Einrichtung für bezahlte Läufe (kostenlos)

### 4.1 Runway
1. Developer Portal (dev.runwayml.com) → Projekt → API-Key erzeugen.
2. **Nur so viel Guthaben kaufen, wie gebraucht wird** (Mindestaufladung beachten; für Test- und Finalfassung reichen ca. 15 USD). Das Guthaben ist die äußere, unabhängige Obergrenze. Die Pipeline warnt, wenn das Guthaben deutlich über dem Restbudget liegt.
3. API-Key nur für **dieses eine Projekt** verwenden – fremde Ausgaben würden den Kontostand-Abgleich verfälschen (die Pipeline würde dann eher zu viel als zu wenig verbuchen).

### 4.2 GitHub (dein Repo ist öffentlich → Required Reviewers sind in jedem Tarif verfügbar)
1. **Settings → Environments → New environment** → `paid-render`
   - **Required reviewers:** dich selbst. „Prevent self-review“ **aus** lassen, sonst kannst du als einzige Person nie freigeben.
   - **Deployment branches and tags:** „Selected branches“ → nur `main`. (Wichtig: Sonst könnte ein Lauf auf einem anderen Branch mit verändertem Workflow an die Secrets.)
   - **Environment secrets:**
     - `RUNWAYML_API_SECRET` – dein Runway-Key
     - `ARTIFACT_PASSPHRASE` – langes Zufallspasswort (≥ 16 Zeichen) zur Verschlüsselung der Ergebnisse. Sicher aufbewahren (Passwortmanager).
   - **Nicht** als Repository-Secret anlegen.
2. **Settings → Branches → Regel für `main`:** Pull Request vor dem Merge verlangen. (Als Admin kannst du sie umgehen – bei einer Ein-Personen-Produktion in Ordnung, schützt aber vor versehentlichen Direkt-Pushes.)
3. **Settings → Actions → General:** Freigabe für Workflows aus Fork-PRs externer Mitwirkender verlangen (Standard bei öffentlichen Repos).
4. Wird das Repo später **privat**, funktionieren Required Reviewers im Free/Pro-Tarif nicht mehr. Dann bleiben als Sperren: Konfigurationsschalter, Bestätigungsphrase, Budget – oder ein Upgrade auf Enterprise.

## 5. Vor dem ersten bezahlten Render – Checkliste

- [ ] Dry-Run (Schritt 3) angesehen und abgenommen
- [ ] Preise in `config/pricing.yaml` noch aktuell (Preisseite öffnen; `verified_on` älter als 45 Tage = Sperre)
- [ ] Stimm-Preset „Maya“ in der Runway-Konsole angehört (Alternativen: Serene, Leslie, Eleanor)
- [ ] Environment `paid-render` inkl. beider Secrets eingerichtet, nur `main` erlaubt
- [ ] Runway-Guthaben aufgeladen (Preflight prüft, dass es reicht)
- [ ] **Freigabe durch die Produktion:** eigener PR, der `paid_rendering_enabled: true` setzt

## 6. Bezahlten Lauf starten

**Actions → Testvideo → Run workflow** auf Branch `main`, `mode = paid-render`, `approval = ICH GEBE DIESEN RENDER FREI`, dann im Lauf **Review deployments → Approve**.

| Schritt | Kosten | Prüft |
|---|---|---|
| Tests + **Generalprobe** (Dry-Run der gewählten Szenen) | 0 € | Pipeline funktioniert |
| Ledger holen, Budgetstand | 0 € | — |
| **Preflight** | 0 € | Schalter, Phrase, Secrets, Preisalter, ungeprüfte Modelle, offene Reservierungen, Lauf- und Gesamtbudget, **Runway-Guthaben** (kostenlose Abfrage) |
| **Reservierung** auf `budget-ledger` gepusht | 0 € | Push muss gelingen, sonst Abbruch |
| **Render** | ✔ | vor jedem Aufruf: Spezifikation + Budget; nach Anlage: Runway-Kostenangabe vs. Preistabelle (Abweichung → Storno + Stopp); Endkosten laut Runway |
| **Verbuchung** (läuft immer) | 0 € | max(Summe der Task-Kosten, Kontostand-Differenz) |
| **Verschlüsselung + Upload** | 0 € | nur `.enc`-Dateien + Kostenprotokoll |

Empfohlene Reihenfolge (inkl. 19 % USt. und 25 % Puffer): 1) `test`, `S01` (≈ 1,13 €) · 2) `test`, `all` (≈ 5,09 €) · 3) `final`, `all` (≈ 10,59 €).

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
| `… > Lauf-Limit` / `… Gesamtbudget` | weniger Szenen, `profile=test`, oder Budgetentscheidung |
| `SpecError …` | Payload würde von Runway abgelehnt – `episode.yaml`/Profil anpassen |
| Paid-Job wird übersprungen | Lauf nicht auf `main` gestartet |
