# ETAGE 0 – Der verbotene Knopf: Lizenzprüfung für die kommerzielle Veröffentlichung

Stand: 15.11.2026. Keine Rechtsberatung – die Tabelle hält fest, was geprüft wurde und worauf sich die
Freigabe stützt. Vor der Veröffentlichung die verlinkten Bedingungen selbst gegenlesen.

| Bestandteil | Quelle | Ergebnis | Absicherung in der Pipeline |
|---|---|---|---|
| Stimmen (ElevenLabs, eleven_v3) | Starter-Tarif | Kommerzielle Lizenz ab Starter; gilt für Audio, das während des aktiven Abos erzeugt wird | `require_commercial_license: true` – Free-Konto bricht im Preflight und direkt vor dem Render ab |
| Stimmen „George“, „Callum“ | ElevenLabs-Standardstimmen | Standardstimmen, keine Stimmen einzelner Voice-Library-Anbieter (die eigene Zusatzbedingungen haben können) | Preflight prüft, dass genau diese IDs im Konto verfügbar sind |
| Soundeffekte (ElevenLabs) | Starter-Tarif | Im Film kommerziell nutzbar; nicht als einzelne Sounddateien weiterverkaufen | Effekte werden nur gemischt im Film ausgegeben |
| Musik | – | **Keine Eleven-Music-Nutzung.** Spannungsbett = Soundeffekt-Drone | `music_license_confirmed: false`, Musikjob wird nie geplant (Test) |
| Bilder und Video (Runway API) | Runway Terms of Use, Abschnitt 4.4 | Runway beansprucht kein Eigentum an Outputs und beschränkt deren kommerzielle Nutzung nicht (bei Einhaltung der Bedingungen) | nur verifizierte Modelle; Outputs tragen C2PA-Herkunftsdaten |
| Drehbuch, Figuren, Prompts | eigenes Werk | keine realen Personen, keine Marken, keine fremden Figuren in den Prompts | – |
| Schrift Untertitel/Titel | Inter (SIL OFL 1.1), Ersatz DejaVu (freie Lizenz) | kommerziell nutzbar, auch eingebrannt | – |
| Schnitt/Encoding | FFmpeg | Ausgabe frei nutzbar | – |

## Offene Punkte, die nur du entscheiden kannst
1. **Neu erzeugt unter Starter:** Für ETAGE 0 wurde bisher nichts generiert (kein bezahlter Lauf, Ledger enthält nur
   den Free-Testlauf von „The Reflection“). Der Zwischenspeicher wird zwischen Läufen nicht übernommen. Alle Stimmen
   und Soundeffekte entstehen also erstmals in diesem Lauf unter dem Starter-Abo.
2. **Nutzungsbasierte Abrechnung (Overage) bei ElevenLabs muss aus sein** – sonst bricht der Preflight ab.
3. **Urheberrechtsschutz von KI-Material** ist rechtlich nicht abschließend geklärt (du darfst es nutzen; ob
   Dritte es übernehmen dürften, ist offen).
4. **Kennzeichnung auf der Plattform:** stilisierte Animation; ob du beim Upload „veränderte/synthetische Inhalte“
   angibst, entscheidest du nach den aktuellen Plattformregeln. Die Pipeline lädt nichts hoch.
5. **„The Reflection“-Material aus dem Free-Tarif** bleibt privat und darf nicht veröffentlicht werden.

## Quellen
- ElevenLabs Preise/Tarife: https://www.elevenlabs.io/pricing
- Runway Terms of Use: https://runwayml.com/terms-of-use
