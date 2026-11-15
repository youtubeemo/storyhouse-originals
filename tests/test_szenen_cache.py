"""Zwischenergebnisse: bezahlte Aufträge nur einmal; nach einem Fehler nur der Rest neu."""
import pytest

from storyhouse.budget import BudgetError, SpendGuard
from storyhouse.pipeline import paid_render
from storyhouse.plan import build_plan
from storyhouse.szenen_cache import SzenenCache, job_keys
from tests.conftest import requires_ffmpeg
from tests.test_paid_flow_simulated import FakeClient, FakeElevenLabs, fake_downloader


def test_schluessel_aendern_sich_mit_prompt_und_abhaengigkeit(episode, pricing):
    plan = build_plan(episode, pricing, profile="test", scenes="S07-S08", include_music=False)
    k1 = job_keys(plan.jobs)
    img = next(j for j in plan.jobs if j.kind == "image")
    abhaengige = [j.id for j in plan.jobs if img.id in j.depends]
    img.payload = {**img.payload, "promptText": img.payload.get("promptText", "") + " (neu)"}
    k2 = job_keys(plan.jobs)
    assert k1[img.id] != k2[img.id]
    assert abhaengige and all(k1[a] != k2[a] for a in abhaengige)       # Folgeaufträge werden mit erneuert
    unberuehrt = [j.id for j in plan.jobs if j.id != img.id and j.id not in abhaengige and not j.depends]
    assert all(k1[u] == k2[u] for u in unberuehrt)


def test_cache_nimmt_keine_leeren_dateien(tmp_path):
    c = SzenenCache(tmp_path / "c")
    leer = tmp_path / "x.mp4"
    leer.write_bytes(b"")
    with pytest.raises(ValueError):
        c.put("k", leer)
    assert c.get("k") is None


@requires_ffmpeg
def test_zweiter_lauf_kostet_nichts_und_nach_fehler_nur_rest(episode, pricing, enabled_cfg, tmp_path):
    cfg = enabled_cfg
    plan = build_plan(episode, pricing, profile="test", scenes="S07-S08", include_music=False)
    cache = SzenenCache(tmp_path / "cache")

    # 1. Lauf scheitert mittendrin (Budget zu klein) – bis dahin Bezahltes landet im Cache
    g = SpendGuard(limit_eur=0.40, pricing=pricing, cfg=cfg, log_path=tmp_path / "s1.json")
    eg = SpendGuard(limit_eur=0.40, pricing=pricing, cfg=cfg, provider="elevenlabs", log_path=tmp_path / "e1.json")
    g.link(eg)
    c1, e1 = FakeClient(g), FakeElevenLabs(eg)
    with pytest.raises(BudgetError):
        paid_render(episode, plan, {"runway": c1, "elevenlabs": e1}, tmp_path / "r1", scenes="S07-S08",
                    downloader=fake_downloader, log=lambda *_: None, cache=cache)
    erledigt = set(c1.submitted) | set(e1.submitted)
    hit, rest = cache.coverage(plan.jobs)
    assert set(hit) == erledigt and rest

    # 2. Lauf: nur noch der Rest wird bezahlt
    g2 = SpendGuard(limit_eur=20, pricing=pricing, cfg=cfg, log_path=tmp_path / "s2.json")
    eg2 = SpendGuard(limit_eur=20, pricing=pricing, cfg=cfg, provider="elevenlabs", log_path=tmp_path / "e2.json")
    g2.link(eg2)
    c2, e2 = FakeClient(g2), FakeElevenLabs(eg2)
    m = paid_render(episode, plan, {"runway": c2, "elevenlabs": e2}, tmp_path / "r2", scenes="S07-S08",
                    downloader=fake_downloader, log=lambda *_: None, cache=cache)
    assert set(c2.submitted) | set(e2.submitted) == set(rest)
    assert set(m["reused_from_cache"]) == erledigt
    assert m["paid_api_calls"] == len(rest)

    # 3. Lauf: alles im Cache – kein einziger bezahlter Aufruf
    c3, e3 = FakeClient(g2), FakeElevenLabs(eg2)
    m3 = paid_render(episode, plan, {"runway": c3, "elevenlabs": e3}, tmp_path / "r3", scenes="S07-S08",
                     downloader=fake_downloader, log=lambda *_: None, cache=cache)
    assert c3.submitted == [] and e3.submitted == [] and m3["paid_api_calls"] == 0
