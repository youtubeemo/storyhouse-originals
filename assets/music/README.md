# Musik

Lizenzierte Score-Datei hier als `ep01_score.wav` ablegen (48 kHz, Stereo, ≥ 40 s, ohne Fade am Anfang/Ende).
Die Datei wird von git ignoriert – nur einchecken, wenn die Lizenz das ausdrücklich erlaubt.
Fehlt sie, generiert die Pipeline den Score mit Eleven Music – aber erst, wenn
`elevenlabs.music_license_confirmed: true` in `config/budget.yaml` gesetzt wurde.
Bis dahin dient die per ElevenLabs generierte Drone als Musikbett.
