# storyhouse-originals – Regeln für KI-Agenten

KI-Filmstudio für vertikale Kurzfilme (Paket `storyhouse/`, Web-Player in `player/`). Budgetregeln in `config/budget.yaml`.

- Tests: `pytest -q`; kostenlos prüfen: `python -m storyhouse validate`, `python -m storyhouse dry-run --preview`
- Runway und ElevenLabs sind kostenpflichtig: nur Trockenläufe ohne Freigabe.

<!-- agent-rules:start v1.0.0 — zentral gepflegt in claude-config, nicht hier von Hand ändern -->
## Kosten und Sicherheit (gilt für jeden KI-Agenten)

- Keine kostenpflichtigen API-Aufrufe oder -Tests ohne ausdrückliche Freigabe im Chat. Nur kostenlose Prüfungen (lokale Tests, Lint, Trockenläufe).
- Keine Zahlungen, Guthabenaufladungen, Abonnements, Testphasen mit Zahlungsmittel.
- API-Schlüssel, Secrets, Budgets und Produktionsgrenzen nicht ändern. `.env` niemals committen.
- Keine Secrets, Kundendaten oder personenbezogenen Daten an fremde Dienste senden.
- Keine ungeprüften Installationsskripte ausführen (`curl … | sh`, `npx <paket>@latest` u. ä.).
- Laufende Produktions-Workflows (GitHub Actions, Zeitpläne, Uploads) nicht unterbrechen, deaktivieren oder auslösen, außer auf ausdrücklichen Wunsch.
- Änderungen über einen eigenen Branch und Pull Request, nicht direkt auf `main`.
<!-- agent-rules:end -->
