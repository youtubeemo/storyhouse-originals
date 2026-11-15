# Review offener PRs, Produktionsarchitektur und zusätzliche Anbieter (Stand 15.11.2026)

## 1. Review PR #5 – Sichere MP4-Bereitstellung

| Prüfpunkt | Befund |
|---|---|
| Zugriffsrechte | `permissions` minimal (`contents: read`, `actions: read` nur wo nötig); „Film bereitstellen“ nur auf `main` und nur durch den Repo-Eigentümer |
| Freigaben | `github_guard approval` liest `/actions/runs/{id}/approvals` und lässt nur eine Freigabe des Eigentümers für `paid-render` gelten. Eine Ablehnung schlägt durch. Bei Fehlern wird gesperrt (fail-closed) |
| Verschlüsselung | Das verschlüsselte Artefakt bleibt immer erhalten. Unverschlüsselt nur, wenn die API direkt vor dem Upload „privat“ meldet (`private: true` **und** `visibility: private` **und** passender `full_name`) |
| Datenlecks | Passphrase nur über `env:` und `-pass env:`, nie in Argumenten oder Logs. Lauf-ID wird vor der Verwendung auf Ziffern geprüft. Keine Workflow-Eingabe landet direkt in der Shell |
| Restrisiko | Zwischen Sichtbarkeitsprüfung und Upload liegen Sekunden. Wird das Repo genau dann öffentlich, wäre das Video öffentlich. Das ist praktisch vernachlässigbar |
| Wichtig | **Das Repo ist derzeit öffentlich.** PR #5 ändert daher heute nichts am Verhalten: kein unverschlüsseltes Video, Abspielen über den iPhone-Player aus PR #6. Wird das Repo auf GitHub Free privat, sperrt der Freigabe-Nachweis jeden bezahlten Render, weil es dort keine Required Reviewers gibt. Das ist beabsichtigt |
| Konflikt | Mit `main` (nach PR #6) gab es einen Konflikt in `docs/ANLEITUNG.md`. Er ist gelöst, beide Abschnitte (6.1 und 6.2) sind erhalten, alle 184 Tests grün |

**Empfehlung:** sicher, kann nach deiner Freigabe gemergt werden. Nötig ist er nur, wenn du das Repo später privat machen willst. Bis dahin genügt der iPhone-Player.

## 2. Review PR #7 – Kostenrechner und Werkzeugkatalog

- Die Rechnung nutzt den echten, spezifikationsgeprüften Plan und dieselbe Preistabelle wie der Preflight. Es gibt keine zweite Preisquelle.
- **Konservativ**, Abweichungen gehen nur nach oben:
  - Alle Kosten werden linear mit der Filmlänge skaliert, auch die Figuren-Referenzbilder, obwohl diese pro Film nur einmal anfallen.
  - ElevenLabs-Credits werden zusätzlich zum Abo bewertet.
  - Bereits verbuchte Ausgaben im Ledger (0,65 €) werden beim „passt ins 200-€-Budget“ nicht abgezogen. Das ist vernachlässigbar.
- Die Nachdrehquote (1,5× Video, 1,2× Ton) ist eine Annahme und als solche gekennzeichnet.
- Es gibt keinen API-Aufruf und keinen Schalter. Der Katalog ist nur eine Anzeige.

**Empfehlung:** sicher, kann nach deiner Freigabe gemergt werden. Dieser PR baut darauf auf.

## 3. Hauptschalter

`paid_rendering_enabled` stand auf `main` noch auf `true` (Umfang test/S01, aus PR #3). Deine Vorgabe lautet: „Bezahlte Filmproduktion bleibt deaktiviert, bis ich ausdrücklich zustimme.“ Deshalb setzt dieser PR den Schalter auf **`false`**.

Für den nächsten S01-Test genügt ein PR, der ihn wieder auf `true` setzt. Die Tests simulieren den freigegebenen S01-Lauf weiterhin, mit einer Kopie der Konfiguration, in der nur der Schalter an ist.

## 4. Produktionsarchitektur

```
Idee/Drehbuch (episode.yaml)
 → Plan + Kosten (plan.py, budget.py: Preflight, Reservierung, SpendGuard, 25 €/Lauf, 200 € gesamt)
 → Charakter-/Szenenbilder   Runway gen4_image        | vorbereitet: Gemini „Nano Banana“
 → Video                     Runway gen4_turbo/gen4.5 | vorbereitet: Hailuo-02
 → Sprache                   ElevenLabs               | Prüfung: faster-whisper (optional)
 → Lippensynchronisation     –                        | vorbereitet: Hedra Character-3
 → Soundeffekte / Musik      ElevenLabs (Musik gesperrt bis Lizenzbestätigung)
 → Schnitt, Untertitel, Lautheit, Outro, Loop   ffmpeg.py
 → Qualitätsprüfung          scripts/videoqc.py (im Workflow)
 → MP4                       verschlüsseltes Artefakt + iPhone-Player
```

- **Zwischenergebnisse** (`storyhouse/szenen_cache.py`, `render --cache DIR`): Jeder bezahlte Auftrag bekommt einen Schlüssel aus Modell, Anfrage und vorgelagerten Aufträgen.
  - Unveränderte Aufträge kosten beim nächsten Lauf 0 €.
  - Nach einem Fehler wird nur der Rest erzeugt.
  - Ändert sich ein Bild, werden die davon abhängigen Videos automatisch neu erzeugt.
  - Getestet: 1. Lauf scheitert am Budget, 2. Lauf bezahlt nur den Rest, 3. Lauf bezahlt nichts.
  - *Noch offen:* den Cache verschlüsselt zwischen Workflow-Läufen sichern und wieder laden (eigener PR).
- **Sicherer Abbruch:** ElevenLabs läuft zuerst. Fehlende Rechte stoppen den Lauf, bevor Runway Geld kostet. Der SpendGuard bricht vor dem Überschreiten der Reservierung ab.
- **Protokolle und Berichte:** `manifest.json` (jetzt mit `reused_from_cache`), Ausgabenprotokolle je Anbieter, Ledger und der Qualitätsbericht auf der Laufseite.
- **Gemeinsame Module:**
  - `videoqc.py`, `secret_scan.py` und `providers.py` liegen als identische Kopien in allen drei Repos, mit Versionsnummer in der Datei.
  - Es gibt bewusst kein gemeinsames Paket und keinen Submodule, damit jedes Projekt allein lauffähig bleibt.

## 5. Zusätzliche Anbieter (nur vorbereitet, `storyhouse/zusatz_anbieter.py`)

Alle sind in `config/budget.yaml → zusatz_anbieter` **aus**. Ohne Schalter **und** Secret gibt es keinen Netzaufruf. Vor jedem Auftrag wird der Preis geprüft. Getestet ist alles mit Attrappen.

Kosten nur für die Videosekunden von „The Reflection“: 8 Einstellungen, 46 s, Listenpreis ohne Nachdrehs.

| Modell | Qualität (Einschätzung) | ≈ Kosten 46 s | Status |
|---|---:|---:|---|
| Runway gen4_turbo (Testprofil) | 3 | 2,30 $ | **freigegeben** |
| Runway gen4.5 (Finalprofil) | 5 | 5,52 $ | **freigegeben** |
| Hailuo-02 768P | 4 | 2,15 $ | vorbereitet, Secret `MINIMAX_API_KEY` fehlt |
| Hailuo-02 1080P (nur 6-s-Clips) | 4 | 3,76 $ | vorbereitet |
| Hedra Character-3 720p (Dialog-Nahaufnahmen) | 4 | 0,05 $/s | vorbereitet; Endpunkte noch gegen die Doku prüfen |
| Gemini 2.5 Flash Image (Referenzbilder) | 4 | 0,039 $/Bild | vorbereitet, Secret `GEMINI_API_KEY` fehlt |

Die Qualitätswerte sind eine Einschätzung, kein eigener Test. Sinnvoller nächster Schritt mit deiner Freigabe: dieselbe Szene S01 einmal mit Hailuo-02 768P (≈ 0,28 $) gegen gen4.5 (≈ 0,72 $) vergleichen.

`waehle()` sucht das günstigste Modell, das Mindestqualität, Budgetgrenze, Freigabe und Mindestauflösung erfüllt. Ohne zusätzliche Freigaben wählt es immer Runway.

## 6. Qualitätsprüfung im Workflow „Film produzieren“

- **Dry-Run:** blockierende Prüfung mit Profil `shorts` (bei `--preview`: `vorschau`). Gemessen am Dry-Run: 1080×1920, 24 fps, −13,2 LUFS, True Peak −1,0 dBTP, 6 gültige Untertitel.
- **Bezahlter Lauf:** dieselbe Prüfung, aber nicht blockierend. Das Geld ist dann schon ausgegeben, das Ergebnis wird in jedem Fall verschlüsselt gesichert.

## 7. GitHub Actions

Das Repo ist öffentlich, Standard-Runner sind damit kostenlos und zählen nicht gegen das Kontingent. In den letzten 30 Tagen waren es 37 Minuten. Es gibt keinen Handlungsbedarf.
