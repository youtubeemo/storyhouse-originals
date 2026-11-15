"""faster-whisper optional: Logik mit Attrappe testbar, ohne Paket und ohne Modell-Download."""
from types import SimpleNamespace as NS

from storyhouse import transkript as tk


class FakeModel:
    def transcribe(self, path, language, word_timestamps, vad_filter):
        assert language == "de" and word_timestamps
        words = [NS(word=" Wer", start=0.0, end=0.3), NS(word=" bist", start=0.3, end=0.6),
                 NS(word=" du?", start=0.6, end=0.9)]
        return [NS(words=words)], NS(language="de")


def test_transkription_mit_attrappe():
    w = tk.transkribieren("x.wav", _model=FakeModel())
    assert [x.text for x in w] == ["Wer", "bist", "du?"] and w[-1].ende == 0.9


def test_wortfehlerrate():
    assert tk.wortfehlerrate("Wer bist du?", "wer bist du") == 0.0
    assert tk.wortfehlerrate("Wer bist du", "wer bist") == round(1 / 3, 3)
    ok, msg = tk.dialog_pruefen("Wer bist du?", tk.transkribieren("x", _model=FakeModel()))
    assert ok and "0%" in msg
    assert not tk.dialog_pruefen("Ich bin dein Spiegelbild", [tk.Wort("Hallo", 0, 1)])[0]
