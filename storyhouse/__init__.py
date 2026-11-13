"""STORYHOUSE ORIGINALS – Produktionspipeline für KI-Kurzfilme.

Module:
    episode    – Laden & Validieren der Episode (episode.yaml)
    budget     – Preise, Kostenschätzung, Ledger, Budget-Wächter
    plan       – Übersetzt eine Episode in konkrete API-Jobs
    runway     – Runway-API-Client (nur mit Budget-Freigabe)
    subtitles  – SRT/ASS-Erzeugung
    ffmpeg     – Schnitt, Grading, Mischung, Untertitel, Export
    pipeline   – Orchestrierung (Dry-Run & bezahlter Render)
"""

__version__ = "0.1.0"
