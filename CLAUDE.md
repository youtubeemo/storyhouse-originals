# storyhouse-originals

@AGENTS.md

<!-- skill-config:start v1.0.0 — zentral gepflegt in claude-config, nicht hier von Hand ändern -->
## Skills automatisch auswählen

Prüfe bei jeder Aufgabe selbst, welche verfügbaren Skills passen, und nutze nur diese – ohne dass der Nutzer sie nennen muss. Kleine Aufgaben (Tippfehler, Ein-Zeilen-Fix, Frage) brauchen keinen Skill-Ablauf.

| Aufgabe | Zuständig | Nicht zusätzlich |
|---|---|---|
| Neue Funktion, größere Änderung planen und umsetzen | Superpowers: `brainstorming` → `writing-plans` → `executing-plans` | Matt `implement` |
| Fehler, fehlschlagender Test, unerwartetes Verhalten | Superpowers: `systematic-debugging` | Matt `diagnosing-bugs` |
| Tests zuerst schreiben | Superpowers: `test-driven-development` | Matt `tdd` |
| Abschluss prüfen, bevor „fertig“ gemeldet wird | Superpowers: `verification-before-completion` | – |
| Spezifikation, Anforderungen klären, Tickets | Matt Pocock: `to-spec`, `grill-with-docs`, `to-tickets` | – |
| Architektur, Modulzuschnitt, Codequalität, Review | Matt Pocock: `improve-codebase-architecture`, `codebase-design`, `code-review` | Superpowers `requesting-code-review` |
| Umfang einer Lösung, „geht das einfacher?“ | Ponytail: Grundhaltung immer; `ponytail-review` nur auf Wunsch | – |
| Website, App-Oberfläche, UI-Design | Taste-Skill: `design-taste-frontend` (nur wo installiert) | – |
| Neuer Film/Animation in einem Opuscar-Stil | Lemo Opuscar (nur wo installiert, siehe unten) | – |

Vorrang bei Widersprüchen: 1. Regeln dieser Datei und `AGENTS.md`, 2. projekteigene Skills in `.claude/skills/`, 3. Ponytail (so wenig Code wie möglich) vor Superpowers-Zeremonie: Für kleine Aufgaben keine Brainstorming-/Plan-Dokumente anlegen.

Lemo Opuscar: Nur für neue, ausdrücklich gewünschte Stil-Filme oder Prototypen. Bestehende Produktionspipelines und Workflows dieses Repos haben Vorrang und werden dafür nicht umgebaut. Seine Laufzeitabhängigkeiten (`setup.sh deps …`, Stimmen, Musik) nur nach Rückfrage installieren.

Claude-Mem und FreeLLMAPI sind nicht eingerichtet. Nicht installieren, nicht aufrufen, keine Daten dorthin senden.
<!-- skill-config:end -->
