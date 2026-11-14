"""STORYHOUSE ORIGINALS – Produktionspipeline für KI-Kurzfilme.

Module:
    episode    – Laden & Validieren der Episode (episode.yaml)
    budget     – Preise, Kostenschätzung, Ledger, Budget-Wächter
    plan       – Übersetzt eine Episode in konkrete API-Jobs
    runway     – Runway-API-Client (Bild/Video, nur mit Budget-Freigabe)
    elevenlabs – ElevenLabs-API-Client (Dialog/SFX/Musik, nur mit Budget-Freigabe)
    subtitles  – SRT/ASS-Erzeugung
    ffmpeg     – Schnitt, Sync, Grading, Mischung, Untertitel, Outro, Export
    pipeline   – Orchestrierung (Dry-Run & bezahlter Render)
"""

__version__ = "0.1.0"
