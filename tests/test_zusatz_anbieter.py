"""Zusätzliche Anbieter: nur vorbereitet – ohne Freigabe + Secret kein einziger Netzaufruf."""
import pytest

from storyhouse import zusatz_anbieter as za


@pytest.fixture
def frei(tmp_path):
    def mk(**an):
        p = tmp_path / "budget.yaml"
        p.write_text("zusatz_anbieter:\n" + "".join(f"  {k}: {str(v).lower()}\n" for k, v in an.items()))
        return p
    return mk


class Attrappe:
    def __init__(self, antwort):
        self.antwort, self.aufrufe = antwort, []

    def __call__(self, method, url, headers, body):
        self.aufrufe.append((method, url, dict(headers)))
        return self.antwort


def test_repo_konfiguration_hat_alles_aus():
    assert za.freigaben() == {"minimax": False, "hedra": False, "gemini": False}


def test_ohne_freigabe_oder_secret_kein_aufruf(frei, monkeypatch):
    t = Attrappe((200, {"task_id": "t1"}))
    h = za.HailuoVideo(transport=t, budget_yaml=frei(minimax=False))
    body = h.anfrage("Ein Spiegel im Flur", 6, "768P")
    monkeypatch.setenv("MINIMAX_API_KEY", "geheim-123")
    with pytest.raises(za.NichtFreigegeben):
        h.submit(body, max_usd=5)
    monkeypatch.delenv("MINIMAX_API_KEY")
    with pytest.raises(za.NichtFreigegeben):
        za.HailuoVideo(transport=t, budget_yaml=frei(minimax=True)).submit(body, max_usd=5)
    assert t.aufrufe == []


def test_budgetgrenze_vor_dem_aufruf(frei, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "geheim-123")
    t = Attrappe((200, {"task_id": "t1"}))
    h = za.HailuoVideo(transport=t, budget_yaml=frei(minimax=True))
    body = h.anfrage("x", 6, "1080P")
    assert h.kosten(body) == pytest.approx(0.49)
    with pytest.raises(za.NichtFreigegeben):
        h.submit(body, max_usd=0.30)
    assert t.aufrufe == []
    assert h.submit(body, max_usd=1.0) == "t1"
    method, url, headers = t.aufrufe[0]
    assert url.endswith("/v1/video_generation") and headers["Authorization"] == "Bearer geheim-123"
    assert "geheim" not in repr(h)


def test_hailuo_regeln():
    h = za.HailuoVideo(transport=Attrappe((200, {})))
    with pytest.raises(ValueError):
        h.anfrage("x", 8)
    with pytest.raises(ValueError):
        h.anfrage("x", 10, "1080P")


def test_gemini_anfrage_und_antwort(frei, monkeypatch, tmp_path):
    import base64
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"\x89PNG fake")
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    bild = base64.b64encode(b"BILD").decode()
    t = Attrappe((200, {"candidates": [{"content": {"parts": [{"inlineData": {"data": bild}}]}}]}))
    g = za.GeminiBild(transport=t, budget_yaml=frei(gemini=True))
    body = g.anfrage("Mara, 30, dunkle Haare – Modellblatt", [ref])
    assert body["contents"][0]["parts"][1]["inline_data"]["mime_type"] == "image/png"
    assert g.submit(body, max_usd=0.10) == b"BILD"
    assert t.aufrufe[0][1].endswith("gemini-2.5-flash-image:generateContent")
    assert t.aufrufe[0][2]["x-goog-api-key"] == "g-key"


def test_hedra_bleibt_gesperrt_bis_endpunkte_geprueft(frei, monkeypatch, tmp_path):
    monkeypatch.setenv("HEDRA_API_KEY", "h")
    (tmp_path / "b.png").write_bytes(b"x")
    (tmp_path / "t.wav").write_bytes(b"x")
    h = za.HedraSprecher(transport=Attrappe((200, {})), budget_yaml=frei(hedra=True))
    body = h.anfrage(tmp_path / "b.png", tmp_path / "t.wav", 8.0, "720p")
    assert h.kosten(body) == pytest.approx(0.40)
    with pytest.raises(za.NichtFreigegeben, match="Endpunkte"):
        h.submit(body, max_usd=5)


def test_modellauswahl_nach_kosten_qualitaet_freigabe():
    # nur Runway freigegeben → günstigstes Runway-Modell mit Qualität ≥ 3
    a = za.waehle("video", sekunden=40, mindest_qualitaet=3, freigegeben={"runway"})
    assert a.modell.anbieter == "runway" and a.modell.name == "gen4_turbo"
    assert a.kosten_usd == pytest.approx(40 * 0.05)
    # mit Hailuo freigegeben und Qualität ≥ 4 → Hailuo 512P ist billiger als Runway gen4.5
    b = za.waehle("video", sekunden=40, mindest_qualitaet=4, freigegeben={"runway", "minimax"})
    assert b.modell.anbieter == "minimax" and b.kosten_usd < 40 * 0.12
    # Mindestauflösung 1080 → Hailuo 1080P
    c = za.waehle("video", sekunden=6, mindest_qualitaet=4, freigegeben={"minimax"}, aufloesung_min=1080)
    assert c.modell.aufloesung == "1080P"
    # nichts passt
    assert za.waehle("video", sekunden=40, mindest_qualitaet=5, max_usd=0.5).modell is None


def test_vergleichstabelle():
    t = za.vergleich_tabelle(40, bilder=10)
    assert "runway / gen4_turbo" in t and "minimax / MiniMax-Hailuo-02 768P" in t and "gemini" in t
