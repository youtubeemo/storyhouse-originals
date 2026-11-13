# STORYHOUSE ORIGINALS

KI-Filmstudio für vertikale Kurzfilme. Erste Produktion:

**THE OTHER SIDE – Episode 01: THE REFLECTION**
40 Sekunden Psychothriller · 9:16 · 1080 × 1920 · englische Dialoge · Hook in Sekunde 1 · Twist · nahtloser Loop

## Inhalt

| Pfad | Was |
|---|---|
| [`episodes/the-other-side/ep01-the-reflection/DREHBUCH.md`](episodes/the-other-side/ep01-the-reflection/DREHBUCH.md) | Drehbuch, Figuren, Dramaturgie |
| [`…/STORYBOARD.md`](episodes/the-other-side/ep01-the-reflection/STORYBOARD.md) | Shotliste + Bild- und Bewegungsprompts je Szene |
| [`…/SOUND_STIMMEN_UNTERTITEL.md`](episodes/the-other-side/ep01-the-reflection/SOUND_STIMMEN_UNTERTITEL.md) | Stimmen, Musik, SFX, Mischung, Untertitel |
| [`…/episode.yaml`](episodes/the-other-side/ep01-the-reflection/episode.yaml) | **Single Source of Truth** – alles maschinenlesbar |
| [`config/budget.yaml`](config/budget.yaml) | Verbindliche Budgetregeln (200 € gesamt) |
| [`config/pricing.yaml`](config/pricing.yaml) | Preisannahmen Runway API |
| [`storyhouse/`](storyhouse) | Pipeline (Python, Runway API + FFmpeg) |
| [`.github/workflows/test-video.yml`](.github/workflows/test-video.yml) | Manueller Workflow für Testvideos |
| [`docs/ANLEITUNG.md`](docs/ANLEITUNG.md) | **Schritt-für-Schritt-Anleitung** |
| [`docs/API_ABGLEICH.md`](docs/API_ABGLEICH.md) | Abgleich mit der offiziellen Runway-API, Preise, Restrisiken |

## Schnellstart (kostenlos)

```bash
pip install -r requirements.txt          # + ffmpeg installieren
python -m storyhouse validate            # Episode prüfen
python -m storyhouse estimate --profile final
python -m storyhouse dry-run --preview   # Platzhalter-Testvideo, 0 €
pytest -q
```

## Sicherheitsprinzipien

- **Bezahltes Rendern ist gesperrt** (`paid_rendering_enabled: false`). Freischaltung nur per PR.
- **Budget:** max. 200 € gesamt (hart im Code gedeckelt), max. 25 € pro Lauf, +25 % Puffer, 19 % USt., Reservierung vor jeder Ausgabe, nur ein offener Lauf, Abgleich mit Runways eigener Kostenangabe pro Task, Stopp bei Preisabweichung oder unklaren Anfragen.
- **API-konform:** Jeder Payload wird vor dem Senden gegen die offizielle Spezifikation geprüft (`storyhouse/runway_spec.py`).
- **Keine API-Schlüssel im Code** – nur als GitHub-Secret der Umgebung `paid-render`. Ein Test schlägt fehl, wenn ein Schlüssel im Repo auftaucht.
- **Keine Veröffentlichung** – Videos landen ausschließlich als Workflow-Artefakt; bezahlte Ergebnisse nur verschlüsselt (das Repo ist öffentlich).
