"""Smoke-Test: rendern alle Seiten gegen die echten Exports?

Bei ueber 80 Routen faellt niemandem auf, wenn eine Aenderung an Seite A
still Seite B zerlegt. Genau das ist passiert: eine Umleitung auf die
Export-Detailseite liess die 2D-Map verschwinden, weil niemand nachgesehen
hat, ob die Zielseite sie ueberhaupt besitzt.

Der Test rendert jede parameterlose GET-Route und verlangt, dass sie nicht
mit einem Serverfehler antwortet. Ohne konfigurierten Vault wird
uebersprungen statt zu scheitern.
"""

from __future__ import annotations

import pytest

from cs2_coach.web.app import create_app, load_config


@pytest.fixture(scope="module")
def client():
    app = create_app()
    # Bewusst ohne TESTING: Ausnahmen sollen zu 500 werden statt zu
    # propagieren, damit ein Lauf ALLE defekten Seiten meldet und nicht
    # nur die erste.
    app.config["TESTING"] = False
    app.config["PROPAGATE_EXCEPTIONS"] = False
    return app.test_client()


@pytest.fixture(scope="module")
def app_obj():
    return create_app()


def simple_get_routes(app) -> list[str]:
    """Alle GET-Routen ohne Pfadparameter."""
    out = []
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.arguments:
            continue
        if rule.endpoint == "static":
            continue
        out.append(str(rule.rule))
    return sorted(out)


def test_every_page_renders(app_obj, client):
    """Keine Seite darf mit 5xx antworten."""
    broken = []
    for path in simple_get_routes(app_obj):
        resp = client.get(path)
        if resp.status_code >= 500:
            broken.append((path, resp.status_code))
    assert not broken, f"Seiten mit Serverfehler: {broken}"


def test_export_detail_renders_for_every_export(client):
    """Jeder gespeicherte Export muss seine Detailseite rendern.

    Faengt Schema-Drift ab: aeltere Exports ohne neu hinzugefuegte Felder
    duerfen die Seite nicht sprengen.
    """
    cfg = load_config()
    from pathlib import Path

    vault = cfg.get("obsidian_vault_path", "")
    if not vault:
        pytest.skip("kein Vault konfiguriert")
    export_dir = Path(vault) / cfg.get("coach_subfolder", "CS2-Coach") / "exports"
    files = sorted(export_dir.glob("*_coach.json")) if export_dir.exists() else []
    if not files:
        pytest.skip("keine Exports vorhanden")

    broken = []
    for f in files:
        resp = client.get(f"/export/{f.name}")
        if resp.status_code != 200:
            broken.append((f.name, resp.status_code))
    assert not broken, f"Export-Detailseiten defekt: {broken}"


def test_export_detail_contains_kill_map(client):
    """Regression: die 2D-Map war nach einer Umleitung verschwunden."""
    cfg = load_config()
    from pathlib import Path

    vault = cfg.get("obsidian_vault_path", "")
    if not vault:
        pytest.skip("kein Vault konfiguriert")
    export_dir = Path(vault) / cfg.get("coach_subfolder", "CS2-Coach") / "exports"
    files = sorted(export_dir.glob("*_coach.json")) if export_dir.exists() else []
    if not files:
        pytest.skip("keine Exports vorhanden")

    html = client.get(f"/export/{files[-1].name}").get_data(as_text=True)
    assert 'id="kill-map"' in html, "Kill-Map-Canvas fehlt"
    assert "var mapData" in html, "Kill-Map-Daten fehlen"


def test_coaching_page_lists_findings(client):
    resp = client.get("/coaching")
    assert resp.status_code == 200


def test_map3d_downloads_answer_404_without_export(client):
    """Geometrie-Routen (Kollision, texturierte Map) ohne Export: sauberes 404, kein 5xx."""
    for path in ("/api/momente/geo/nomap", "/api/momente/render/nomap", "/api/momente/models/weapon_nix.glb.gz",
                 "/api/momente/models/..%2Fconfig.yaml"):
        assert client.get(path).status_code == 404


def test_moment_clip_cache_roundtrip(tmp_path, monkeypatch):
    """Gecachte Szene wird unveraendert gelesen; nur Erfolge landen im Cache."""
    from cs2_coach.web import moments_page as mp
    calls = []

    def fake(cfg, cfg_path, filename, r, t, kind, with3d=True):
        calls.append(r)
        return ({"map": "mirage", "x": r}, 200) if r == 1 else ({"error": "x"}, 404)
    monkeypatch.setattr(mp, "build_clip", fake)
    cfgp = tmp_path / "config.yaml"
    assert mp.cached_clip({}, cfgp, "a.json", 1, 5, "kill") == ({"map": "mirage", "x": 1}, 200)
    assert mp.cached_clip({}, cfgp, "a.json", 1, 5, "kill")[0]["x"] == 1 and calls == [1]
    assert mp.cached_clip({}, cfgp, "a.json", 2, 5, "kill")[1] == 404
    assert not mp._cache_file(cfgp, "a.json", 2, 5, "kill").exists()


def test_cached_parser_reads_events_once(monkeypatch, tmp_path):
    """demo_cache: ein Durchlauf fuer alle Events, danach aus dem Speicher; preload bedient Teilmengen."""
    import pandas as pd
    from cs2_coach import demo_cache
    calls = {"events": 0, "ticks": 0}

    class Fake:
        def __init__(self, path): pass
        def parse_events(self, names):
            calls["events"] += 1
            return [("player_hurt", pd.DataFrame({"tick": [1]}))]
        def parse_ticks(self, fields, ticks=None):
            calls["ticks"] += 1
            return pd.DataFrame([{"tick": t, "steamid": "1", **{f: 0 for f in fields}} for t in ticks])
    import demoparser2
    monkeypatch.setattr(demoparser2, "DemoParser", Fake)
    demo = tmp_path / "a.dem"
    demo.write_bytes(b"x")
    demo_cache.clear()
    p = demo_cache.cached_parser(demo)
    assert len(p.parse_event("player_hurt")) == 1 and p.parse_event("weapon_fire").empty
    assert len(demo_cache.cached_parser(demo).parse_event("player_hurt")) == 1 and calls["events"] == 1
    p.preload_ticks(["X", "Y"], [1, 2, 3])
    df = p.parse_ticks(["X"], ticks=[2, 3])
    assert list(df["tick"]) == [2, 3] and "Y" not in df.columns and calls["ticks"] == 1
