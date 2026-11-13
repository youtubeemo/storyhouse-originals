# Anleitung – STORYHOUSE ORIGINALS Pipeline

Diese Anleitung führt vom kostenlosen Testvideo bis zum ersten freigegebenen Render.
**Bis Schritt 4 entstehen keinerlei Kosten.**

---

## 1. Wie die Pipeline arbeitet

```
episode.yaml ──► Plan (Jobs + Preise) ──► Budget-Prüfung ──► Runway API ──► FFmpeg ──► MP4 + SRT
                                              │                               │
                                       Ledger (budget-ledger)        Schnitt · Grading · Mischung
                                                                     Untertitel · −14 LUFS · Loop
```

1. **Referenzen:** Casting-Porträt von Maya und das Badezimmer-Set (`gen4_image`) – damit die KI-Schauspielerin in allen Shots gleich aussieht.
2. **Keyframes:** ein Standbild pro Szene mit den Referenzen.
3. **Bewegung:** `image_to_video` pro Szene (Test: `gen4_turbo`, Final: `gen4.5`), vertikal `720:1280`.
4. **Ton:** Dialoge per Runway-TTS (`eleven_v3`, Preset „Maya“), 17 SFX + Drone per `sound_effect`.
5. **Post (FFmpeg):** Upscale auf 1080 × 1920, exakter Schnitt auf Szenenlängen, Teal/Amber-Grading mit Korn, Figuren-Stimmeffekte, Ducking, Stille-Fenster, Loudness −14 LUFS, eingebrannte Untertitel in der TikTok-Safe-Zone, SRT-Sidecar.

## 2. Lokal testen (kostenlos)

Voraussetzungen: Python ≥ 3.10, FFmpeg mit libass.

```bash
pip install -r requirements.txt
python -m storyhouse validate                       # Timing, Prompts, Referenzen
python -m storyhouse estimate --profile test        # Kosten Testlauf
python -m storyhouse estimate --profile final       # Kosten Endproduktion
python -m storyhouse dry-run --preview              # schnelles Vorschauvideo (360×640)
python -m storyhouse dry-run                        # volle 1080×1920
python -m storyhouse dry-run --scenes S06-S08       # nur Twist + Loop
pytest -q                                           # 53 Tests
```

Ergebnis in `output/dry-run/`: MP4, SRT, ASS, `estimate.md` (was ein echter Lauf kosten würde), `plan.json` (alle API-Aufrufe, die gesendet würden).

## 3. Testvideo über GitHub Actions (kostenlos)

1. GitHub → **Actions** → **Testvideo** → **Run workflow**
2. `mode = dry-run` (Standard), Profil/Szenen nach Wunsch → **Run**
3. Nach ca. 3–5 Minuten unter dem Lauf → **Artifacts** → `dryrun-…` herunterladen.

Der Dry-Run-Job hat keinen Zugriff auf den API-Schlüssel.

## 4. Einmalige Einrichtung für bezahlte Läufe (noch kostenlos)

1. **Runway-Konto:** Im Developer Portal (dev.runwayml.com) einen API-Schlüssel erzeugen.
   **Empfehlung:** Nur so viele Credits aufladen, wie das Budget erlaubt (z. B. zunächst 25 USD). Das ist die zusätzliche, externe Obergrenze.
2. **GitHub-Umgebung:** Settings → Environments → **New environment** → Name `paid-render`
   - **Required reviewers:** dich selbst eintragen (jeder bezahlte Lauf wartet dann auf deinen Klick „Approve“).
   - **Deployment branches:** nur `main`.
   - **Environment secret:** `RUNWAYML_API_SECRET` = dein Schlüssel. *Nicht* als Repository-Secret anlegen.
3. **Branch-Schutz für `main`:** Pull Request + Review vorschreiben (damit `config/budget.yaml` nur per Review geändert wird).

## 5. Vor dem ersten bezahlten Render – Checkliste

- [ ] Preise in `config/pricing.yaml` mit der aktuellen Runway-Doku abgleichen (insbesondere `eleven_v3` und `sound_effect`).
- [ ] Prüfen, ob `gen4.5` Start- **und** End-Keyframe akzeptiert (für den Loop, `LAST_FRAME_MODELS` in `storyhouse/plan.py`).
- [ ] Prüfen, dass `gen4_image` das Seitenverhältnis `1080:1920` akzeptiert.
- [ ] Stimm-Preset „Maya“ anhören; Alternativen: Serene, Leslie, Eleanor.
- [ ] Dry-Run abgenommen.
- [ ] **Freigabe durch die Produktion** (siehe 6.).

## 6. Bezahlten Lauf freigeben und starten

Ein bezahlter Lauf startet nur, wenn **alle** vier Sperren offen sind:

| Sperre | Wo | Wer |
|---|---|---|
| `paid_rendering_enabled: true` | `config/budget.yaml` per Pull Request auf `main` | Produktion |
| Bestätigungsphrase `ICH GEBE DIESEN RENDER FREI` | Workflow-Eingabe `approval` | wer den Lauf startet |
| Environment-Freigabe `paid-render` | Klick „Approve“ im Workflow | Required Reviewer |
| Budget | automatisch: Schätzung + 25 % muss ≤ 25 € pro Lauf und ≤ Restbudget sein | Pipeline |

Ablauf im Workflow (`mode = paid-render`):
1. Tests → Ledger holen → Budgetstand → **Preflight**
2. **Reservierung** wird im Ledger gespeichert und auf den Branch `budget-ledger` gepusht – *bevor* ein Cent ausgegeben wird. Schlägt das Pushen fehl, bricht der Lauf ab.
3. **Render:** Vor jedem einzelnen API-Aufruf prüft der Wächter, ob er noch in die Reservierung passt. Wenn nicht: sofortiger Stopp. Gescheiterte Tasks werden nicht automatisch wiederholt.
4. **Verbuchung** (läuft immer, auch bei Fehlern): tatsächliche Kosten = Maximum aus Schätzung und Runway-Kontostand-Differenz. Fehlt jeder Nachweis, wird die volle Reservierung verbucht.
5. Video als Artefakt `render-…` – **keine Veröffentlichung**.

Empfohlene Reihenfolge:
1. `profile=test`, `scenes=S01` (ca. 0,65 € inkl. Puffer) – Look und Schauspielerin prüfen
2. `profile=test`, `scenes=all` (ca. 4 € inkl. Puffer)
3. `profile=final`, `scenes=all` (ca. 9 € inkl. Puffer)

## 7. Budget im Blick

```bash
git fetch origin budget-ledger && git show origin/budget-ledger:ledger.json
```
oder im Workflow-Log Schritt „Budgetstand“. Ab 75 % Verbrauch erscheint eine Warnung.
Das Gesamtbudget ist im Code auf **höchstens 200 €** gedeckelt – auch eine Änderung der Konfiguration kann es nicht anheben.

## 8. Eine Szene ändern

Alles steht in `episode.yaml`. Nach jeder Änderung: `python -m storyhouse validate && pytest -q`.
Die Validierung prüft u. a. lückenlose Timeline von exakt 40,0 s, Hook-Dialog in der ersten Sekunde, Promptlängen ≤ 1000 Zeichen, definierte SFX und Referenz-Tags.

## 9. Fehlerbehebung

| Meldung | Bedeutung |
|---|---|
| `Bezahltes Rendern ist gesperrt` | `paid_rendering_enabled` ist `false` – gewollt bis zur Freigabe. |
| `Bestätigungsphrase fehlt oder ist falsch` | Workflow-Eingabe `approval` exakt wie in `budget.yaml`. |
| `… > Lauf-Limit` | Weniger Szenen oder `profile=test` wählen. |
| `… > verbleibendes Gesamtbudget` | Budget erschöpft – nur per Entscheidung der Produktion erweiterbar (max. 200 €). |
| `Kein Preis für … hinterlegt` | Modell fehlt in `pricing.yaml` – unbekannte Modelle werden nie mit 0 € bewertet. |
| `Task … FAILED` | Runway hat abgelehnt (z. B. Moderation). Prompt anpassen und neuen Lauf starten. |
