# Pipeline-Analyse nach Renderlauf 43724968662 und Plan für ETAGE 0 – S01E01

Der Lauf war technisch „grün“, der Film wurde nicht abgenommen: zu kurz, Handlung unverständlich, Ton
schwach, Szenen ohne Zusammenhang. Unten steht je Bereich, woran es lag und was geändert wurde.

## Schwächen und Änderungen

| Bereich | Schwäche im alten Lauf | Änderung in diesem PR |
|---|---|---|
| Ton-Mastering | Dynamische Normalisierung zog Stille hoch und drückte Schläge; True Peak im fertigen Film −0,4 statt −1 dBTP | `master_audio`: messen, ein fester Pegel, Limiter mit Reserve für AAC. Stille bleibt still. Test mit AAC-Kodierung. |
| Mischung | Ein durchgehendes Spannungsbett, keine Trennung nach Ort, keine geplante Stille | Spuren haben Anfang, Ende und Blenden je Akt; „Stille-Fenster“ schalten alles stumm (vor dem Ruck, nach dem Aufprall) |
| Musik und Lizenz | Lizenz war sauber (siehe LIZENZEN.md der Testfolge), aber nur ein einziger Drone – keine Musik, die der Handlung folgt | Sechs Musikflächen (M1–M6), je Akt eine, mit Ein- und Ausblenden auf die Schnitte. Weiter als Klangflächen über die Geräusch-Schnittstelle, kein Eleven-Music-Job, Lizenzlage unverändert. |
| Bild-Ton-Synchronität | Dialog wurde beschleunigt oder abgeschnitten, wenn er nicht passte | `strict_timing`: Jede Zeile muss ungekürzt in ihr Zeitfenster passen, sonst scheitert `validate` (kostenlos) |
| Dialog und Lippen | Englische Sprachkennung für deutschen Text; sichtbare Münder ohne Synchronität | `language: de` an jedem Sprach-Job. Auflösung ohne sichtbaren Mund beim Sprechen (siehe DREHBUCH.md, „Lippen“). |
| Szenenkonsistenz | Jede Szene wurde frei erzeugt – Gesichter, Kleidung, Räume wechselten | Feste Referenzbilder für Figuren, Orte, Requisiten; Keyframe der vorherigen Einstellung als Anschlussreferenz; abgenommene Referenzen als Dateien im Repo werden nie neu erzeugt |
| Storyboard und Prompts | Kein Storyboard; Einstellungsgröße, Kamera und Handlung waren kein Pflichtfeld | Jede Einstellung braucht Akt, Ort, Einstellung, Kamera, Handlung, Übergang. `STORYBOARD.md` wird aus `episode.yaml` erzeugt; ein Test hält beides gleich. |
| Übergänge und Schnitt | Übergänge waren nicht geplant, Szenen-Blenden gab es nicht | Auf-/Abblenden innerhalb der Szenendauer; Übergang je Einstellung beschrieben; Schnitte auf Geräusch und Blickachse |
| Einblendungen | Text im Bild von der Bild-KI erzeugt (unlesbar) | Uhrzeit, Handy-Nachricht, Etagenanzeige werden in der Nachbearbeitung eingesetzt |
| Qualitätskontrolle | Lauf grün trotz Grenzwertverletzung | Der Lauf wird rot, wenn die technische Prüfung scheitert – nachdem Kosten verbucht und Ergebnisse gesichert sind |

Unverändert weiterverwendet: Budget- und Freigabeprüfung, Ledger, Szenen-Cache, Anbieter-Clients,
Untertitel, Verschlüsselung der Ergebnisse, Player.

## Abnahme

Automatische Tests prüfen Länge, Format, Pegel, Untertitel und Schwarzbilder. Sie prüfen nicht, ob der
Film funktioniert. Deshalb gibt es drei Stufen, jede mit eurer Freigabe durch Ansehen und Anhören:

| Stufe | Was ihr bekommt | Kosten | Freigabe für |
|---|---|---:|---|
| 1 – Animatic | Texttafeln je Einstellung im echten Timing, Einblendungen, Untertitel, Platzhaltertöne | 0 € | Geschichte, Tempo, Dialog |
| 2 – Bild-Storyboard | Alle Referenz- und Keyframe-Bilder als Standbildfilm mit echten Stimmen und Geräuschen | ca. 8 € (10 € mit Aufschlag) | Look, Figuren, Stimmen, Ton |
| 3 – Finaler Film | Bewegte Einstellungen; Bilder und Ton aus Stufe 2 kommen aus dem Cache | ca. 24 € zusätzlich | Veröffentlichung |

Zusammen ca. 32 € (40 € mit 25 % Sicherheitsaufschlag), inkl. USt., Preisstand 14.11.2026. Realistisch
kommen Wiederholungen einzelner Einstellungen dazu – rechnet mit 45–60 € für die erste Folge. Folgende
Episoden sind günstiger, weil die Referenzbilder stehen. Das ElevenLabs-Abo selbst ist nicht enthalten;
der Verbrauch (ca. 16.000 Credits) passt in ein Starter-Monatskontingent.

Vor Stufe 2 nötig, jeweils per eigenem PR: `paid_rendering_enabled: true`, die Episode in
`paid_scope.episodes`, Budget festlegen. Für Stufe 3 liegt das Lauf-Limit (25 €) unter der Schätzung –
entweder Limit anheben oder in zwei Läufen nach Akten rendern.

## Bekannte Grenzen

- Bewegungen und Mimik hängen am Videomodell. Stufe 2 zeigt nur Standbilder; ob eine Einstellung
  natürlich wirkt, sieht man erst in Stufe 3. Einzelne Einstellungen lassen sich gezielt wiederholen.
- Die Stimmen sind ElevenLabs-Standardstimmen. Besetzung in Stufe 2 anhören und bei Bedarf tauschen.
- Echte Lippensynchronisation ist nicht eingeplant (eigenes Werkzeug, eigene Kosten).
