# Storyboard & Videoprompts – EP01 THE REFLECTION

Bildformat 9:16. Generierung bei Runway in `720:1280`, Upscale auf 1080 × 1920 in FFmpeg.
Alle Prompts stehen maschinenlesbar in [`episode.yaml`](episode.yaml) und werden von der Pipeline direkt verwendet.

## Prompt-System

Jede Einstellung entsteht in zwei Stufen, damit die KI-Schauspielerin über alle Shots gleich aussieht:

1. **Referenzen** (einmalig): Casting-Porträt `@maya` und Set `@bathroom` mit `gen4_image`.
2. **Keyframe** pro Shot: `gen4_image` mit `referenceImages` (`@maya`, `@bathroom`) + `keyframe_prompt` + globaler Look.
3. **Bewegung**: `image_to_video` (`gen4.5` final, `gen4_turbo` für Tests) mit `motion_prompt` – beschreibt nur Bewegung und Kamera, nicht erneut das Aussehen.

Prompt-Regeln (Runway): positiv formulieren (es gibt keine Negativprompts), ein klarer Hauptvorgang pro Shot, Kamera explizit benennen, „realistic, subtle motion“ gegen Overacting. Text im Bild (z. B. Schrift im beschlagenen Glas) wird nicht generiert, sondern bei Bedarf in der Post gesetzt.

**Globaler Look** (wird jedem Keyframe-Prompt angehängt):
> Photorealistic cinematic film still, shot on ARRI Alexa 35 with a 40mm anamorphic lens, shallow depth of field, natural skin texture with pores, subtle film grain, low-key lighting, cold teal fluorescent light mixed with a faint warm sodium streetlight, deep shadows, high dynamic range, psychological thriller mood, no text, no watermark.

---

## Shotliste

| # | Zeit | Dauer | Einstellung | Kamera | Ton |
|---|---|---|---|---|---|
| S01 | 0.0–3.0 | 3.0 s | ECU Spiegel: Spiegelbild starrt in die Linse, echte Maya unscharf vorne | statisch, minimaler Push-in | **„Don't turn around.“** · Röhrenbrummen · Tropfen · Drone (Loop-Naht) |
| S02 | 3.0–8.0 | 5.0 s | MCU Over-Shoulder in den Spiegel | Handkamera, kaum Bewegung | Herzschlag · Atem · **„...Hello?“** |
| S03 | 8.0–12.5 | 4.5 s | Spiegel-Insert: Tür im Spiegel offen | Rack-Focus Gesicht → Türöffnung | Türknarren (nur Spiegelseite) · Flacker-Zap |
| S04 | 12.5–18.0 | 5.5 s | CU Spiegelbild bewegt sich allein, Atem beschlägt Glas von innen | statisch | Atem am Glas · Handflächen-Quietschen · Sub-Hit |
| S05 | 18.0–24.0 | 6.0 s | Two-Shot durch den Spiegel, Blick „hinter dich“ | langsame Seitwärtsfahrt | **„It's not me you should be afraid of.“ / „Then who—“** |
| S06 | 24.0–29.5 | 5.5 s | MS: Maya dreht sich um – nichts | langsamer Orbit | **harte Stille** · erleichtertes Ausatmen |
| S07 | 29.5–35.5 | 6.0 s | **TWIST** – Reverse aus dem Spiegel: Maya gefangen, The Other lächelt | Pull-back aus dem Spiegel | Orchester-Sting · dumpfes Hämmern · **„Thank you for turning around.“** · Lichtschalter |
| S08 | 35.5–40.0 | 4.5 s | **LOOP** – identisch zu S01, jetzt ist unsere Maya das Spiegelbild | wie S01 | Flackern an · **„Whatever you do—“** → S01 |

---

## Prompts je Shot

### S01 – THE HOOK (0:00–0:03)
**Keyframe:** @maya's reflection in a bathroom mirror, extreme close-up, the reflection leans close to the glass staring straight into the lens with wet urgent eyes, toothbrush frozen at her lips, while in the soft-focus foreground the real woman's shoulder and hair face the mirror, cold flickering fluorescent light, inside @bathroom.
**Motion:** The reflection stays perfectly still and stares into the lens while the blurred real woman in the foreground keeps brushing her teeth. The reflection's lips slowly whisper three words. The fluorescent light flickers once. Very slow push-in. Realistic, subtle motion.
**Hook-Regeln:** Gesicht + Blick in die Linse ab Frame 1 · erste Silbe bei 0,35 s · Untertitel ab Frame 1 · kein Logo, kein Titel.

### S02 – FROZEN (0:03–0:08)
**Keyframe:** Over-the-shoulder shot of @maya standing at the sink in @bathroom, looking up at her own reflection in the medicine-cabinet mirror, toothbrush in hand, face pale and frozen with fear, foam on her lip, cold teal fluorescent light from above, shallow depth of field.
**Motion:** The woman slowly lowers her toothbrush and raises trembling fingertips toward the mirror; her reflection mirrors her perfectly. Her breathing is shallow, her chest rises. Subtle handheld camera sway. Realistic.

### S03 – THE DOOR (0:08–0:12.5)
**Keyframe:** Bathroom mirror reflection showing @maya's frightened face in the foreground and, behind her in the reflection, the bathroom door standing wide open onto a pitch-black hallway, @bathroom, flickering cold fluorescent light, deep shadows, eerie stillness.
**Motion:** Slow rack focus from the woman's face to the open black doorway behind her in the reflection. The fluorescent tube flickers twice. Darkness in the doorway seems to breathe. No sudden movement, realistic.

### S04 – CLOSER (0:12.5–0:18)
**Keyframe:** Close-up of @maya's reflection inside a bathroom mirror, the reflection pressed close to the glass, a patch of condensation fog on the inside of the mirror around her lips, her palm flat against the glass from the inside, terrified urgent eyes, @bathroom, cold teal light.
**Motion:** The reflection leans closer by itself, her breath fogs the glass from the inside and a palm print slowly forms in the condensation. Tiny, unsettling, realistic movements. Locked-off camera.

### S05 – THE WARNING (0:18–0:24)
**Keyframe:** Two-shot through a bathroom mirror, @maya's back and shoulder soft in the foreground, her reflection sharp in the mirror looking past her shoulder at something behind her with pure terror, @bathroom, flickering cold fluorescent light, cinematic shadows.
**Motion:** The reflection whispers urgently, then her eyes slowly slide past the woman's shoulder toward the darkness behind her, widening in terror. Slow lateral camera slide. Realistic micro-expressions.

### S06 – TURN AROUND (0:24–0:29.5)
**Keyframe:** Medium shot of @maya in @bathroom slowly turning her back to the mirror to look behind her at a closed white door, her face in tense profile, cold fluorescent light, the mirror behind her, cinematic stillness.
**Motion:** The woman slowly turns around to face the closed door behind her. Nothing is there. Her shoulders drop and she lets out a shaky breath, almost a relieved laugh. Slow camera orbit. Realistic.

### S07 – THE SWAP / TWIST (0:29.5–0:35.5)
**Keyframe:** View from inside a bathroom mirror looking out: @maya trapped behind the glass pressing both palms against it, screaming silently, in the real bathroom outside an identical woman with the scar on her right eyebrow stands calmly with a faint smile, @bathroom, cold fluorescent light, inverted perspective, cinematic horror reveal.
**Motion:** Slow pull-back from inside the mirror. The trapped woman pounds silently on the glass. The calm woman outside tilts her head, smiles softly, and reaches up to switch off the light. Realistic, controlled motion.
**Hinweis:** Zwei identische Figuren in einem Bild ist der schwierigste Shot. Fallback: zwei getrennte Generierungen (gefangene Maya / lächelnde Maya) und Split-Screen-Composite in FFmpeg.

### S08 – THE LOOP (0:35.5–0:40)
**Keyframe:** wie S01, „another woman's shoulder“ im Vordergrund, „Identical composition to the opening shot.“
**Motion:** Darkness, then the fluorescent light flickers on. The reflection leans close to the glass and her lips begin to whisper while the blurred woman in the foreground brushes her teeth. Very slow push-in. Realistic.
**Loop-Technik:** Für S08 wird zusätzlich der *erste Frame von S01* als End-Keyframe übergeben (`promptImage` mit `position: last`), sodass der letzte Frame von S08 bildgleich in S01 übergeht. Audio: Drone läuft ohne Fade über die Naht, Satz „Whatever you do—“ endet bei 40,0 s und wird von „—don't turn around.“ bei 0,35 s fortgesetzt.
