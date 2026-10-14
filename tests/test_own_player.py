"""Die Analyse eines Mitspielers darf den eigenen Export nicht ueberschreiben.

Der Dateiname eines Exports nennt Datum, Map, Score und Uhrzeit, aber nicht
den Spieler. Der Knopf "Analysieren" in der Scoreboard-Tabelle schrieb die
Analyse eines Mitspielers deshalb ueber den eigenen Export zum selben Match.
Auf dem Server standen so in zwei Matches die Werte eines Mitspielers in der
eigenen Auswertung.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cs2_coach.obsidian import is_own_player

ME = "76561198019262528"


def stats(steam_id, name):
    return SimpleNamespace(steam_id=steam_id, name=name)


def test_steam_id_entscheidet():
    cfg = {"steam_id": ME, "player_name": "deej0t"}
    assert is_own_player(stats(ME, "deej0t"), cfg)
    assert not is_own_player(stats("7656119999", "Łυкαѕ"), cfg)


def test_alter_name_mit_gleicher_id_bleibt_eigener_spieler():
    """Aus 'deej0ttv' wurde 'deej0t' - die ID ist dieselbe."""
    assert is_own_player(stats(ME, "deej0ttv"), {"steam_id": ME, "player_name": "deej0t"})


def test_ohne_steam_id_entscheidet_der_name():
    cfg = {"player_name": "deej0t"}
    assert is_own_player(stats("1", "deej0t"), cfg)
    assert not is_own_player(stats("2", "Łυкαѕ"), cfg)


def test_ohne_jede_angabe_nichts_eigenes():
    assert not is_own_player(stats("1", "deej0t"), {})


# ── Route ────────────────────────────────────────────────────────────

@pytest.fixture
def route(monkeypatch, tmp_path):
    """/analyze-path mit ersetztem Parser, Export und Discord-Versand."""
    from cs2_coach.web import app as A

    demo = tmp_path / "match.dem"
    demo.write_bytes(b"")
    calls = {"export": 0, "discord": 0}

    def fake_parse(path, player, steamid):
        # Die Route liest diese Felder fuer die Ergebnisseite.
        return SimpleNamespace(player_stats=stats(steamid, player), duel_matrix={},
                               round_timeline=[], economy_performance={})

    monkeypatch.setattr(A, "parse_demo", fake_parse)
    monkeypatch.setattr(A, "generate_report", lambda r: "")
    monkeypatch.setattr(A, "export_match", lambda *a, **k: (calls.__setitem__("export", calls["export"] + 1)
                                                            or (tmp_path / "x.md", "x_coach.json")))
    monkeypatch.setattr(A, "_post_discord", lambda *a, **k: calls.__setitem__("discord", calls["discord"] + 1))
    monkeypatch.setattr(A, "render_template", lambda *a, **k: "ergebnisseite")
    monkeypatch.setattr(A, "_build_kill_map_data", lambda r: {})

    app = A.create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    cfg_id = A.load_config().get("steam_id") or ""

    def post(steamid, player):
        return client.post("/analyze-path", data={"demo_path": str(demo), "steamid": steamid, "player": player})

    return post, calls, cfg_id


def test_mitspieler_wird_weder_gespeichert_noch_gemeldet(route):
    post, calls, cfg_id = route
    if not cfg_id:
        pytest.skip("keine steam_id in der Test-Config")
    r = post("7656119999", "Łυкαѕ")
    assert r.status_code == 200
    assert calls == {"export": 0, "discord": 0}


def test_eigene_analyse_wird_weiter_gespeichert(route):
    post, calls, cfg_id = route
    if not cfg_id:
        pytest.skip("keine steam_id in der Test-Config")
    r = post(cfg_id, "deej0t")
    assert r.status_code == 302
    assert calls == {"export": 1, "discord": 1}
