"""Duell-Training: Situationen aus Exporten, Ablauf Start -> Team -> Runden -> Ende, adaptive Stufe."""

from __future__ import annotations

import json

import pytest

from cs2_coach import training_server as ts

OWNER = "76561198019262528"
DJ = '"dj<2><[U:1:58996800]><TERRORIST>"'
BOT = '"K<4><BOT><CT>"'

# Echte Ausgaben vom Server (30.10.2026), gekuerzt
STATUS_ALONE = """---------players--------
  id     time ping loss      state   rate adr name
65535 [NoChan]    0    0 challenging      0unknown ''
   2    09:33    0    0     active 786432 192.168.188.75:59684 'dj'
   4      BOT    0    0     active      0 'K'
#end"""
STATUS_TWO = STATUS_ALONE.replace("#end", "   3    09:25   19    0     active 786432 213.55.218.192:19968 'Dancho'\n#end")
ENT_HUMAN = """Searching for entities with class/target name containing substrings: "player"
   'env_player_visibility' : '' (entindex 370)
   'player' : '' (entindex 385)
   'cs_player_controller' : '' (entindex 4)"""
ENT_BOTH = ENT_HUMAN + "\n   'player' : '' (entindex 387) "


def line(msg: str) -> str:
    return f"10/30/2026 - 01:00:00.000 - {msg}"


def export(tmp_path, map_name="Mirage", deaths=None):
    folder = tmp_path / "vault" / "CS2-Coach" / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    deaths = deaths if deaths is not None else [
        {"t": "d", "x": -822.8, "y": -2301.1, "z": -174.5, "ex": -178.4, "ey": -1961.2, "ez": -168.0,
         "w": "m4a1", "hs": False, "r": 3, "tk": 17263, "e": "Pöpp Escobar"},
        {"t": "d", "x": 0, "y": 0, "z": 0, "ex": 50, "ey": 0, "ez": 0, "w": "ak47", "r": 4, "e": "nah"},
        {"t": "d", "x": 0, "y": 0, "z": 0, "ex": 900, "ey": 0, "ez": 0, "w": "knife_t", "r": 5, "e": "messer"},
        {"t": "k", "x": 0, "y": 0, "z": 0, "ex": 900, "ey": 0, "ez": 0, "w": "ak47", "r": 6, "e": "kill"},
        {"t": "d", "x": 100, "y": 100, "z": 0, "ex": 900, "ey": 100, "ez": 0, "w": "awp", "r": 7, "e": "Sniper"},
    ]
    (folder / f"2026-10-27_{map_name.lower()}_13-8_2049_coach.json").write_text(json.dumps(
        {"match": {"map": map_name, "date": "2026-10-27"}, "kill_positions": deaths}), encoding="utf-8")
    return tmp_path


class FakeServer:
    def __init__(self, status=STATUS_ALONE):
        self.sent: list[str] = []
        self.status = status
        self.pawns = ENT_HUMAN

    def __call__(self, *cmds):
        self.sent.extend(cmds)
        out = []
        for c in cmds:
            out.append(self.status if c == "status" else self.pawns if c == "ent_find player" else "")
        return out


@pytest.fixture()
def env(tmp_path):
    root = export(tmp_path)
    cfg = {"steam_id": OWNER, "obsidian_vault_path": str(root / "vault"),
           "server_restore_command": "host_workshop_map 3245740485"}
    server = FakeServer()
    timers = []
    director = lambda: ts.Director(cfg, tmp_path / "config.yaml", server,  # noqa: E731
                                   timer=lambda sec, fn: timers.append((sec, fn)))
    return cfg, tmp_path / "config.yaml", server, timers, director


def run_timers(timers):
    while timers:
        timers.pop(0)[1]()


def test_scenarios_only_gun_duels_at_distance(tmp_path):
    sc = ts.build_scenarios(export(tmp_path) / "vault" / "CS2-Coach" / "exports")
    assert [s.opponent for s in sc["de_mirage"]] == ["Pöpp Escobar", "Sniper"]
    assert sc["de_mirage"][0].source == "27.10. R3"


def test_maps_outside_pool_are_ignored(tmp_path):
    export(tmp_path, "Cache")
    assert ts.build_scenarios(tmp_path / "vault" / "CS2-Coach" / "exports") == {}


def test_only_owner_may_start(env):
    _, _, server, _, director = env
    assert director().command("123", "Fremder", "training", "mirage") == [
        "Das Training kann nur der Server-Besitzer starten."]
    assert server.sent == []


def test_start_refused_while_others_play(env):
    _, path, server, _, director = env
    server.status = STATUS_TWO
    assert "Dancho" in director().command(OWNER, "dj", "training", "mirage")[0]
    assert not ts.is_active(path)


def test_lists_maps_and_rejects_pool_outsiders(env):
    _, _, _, _, director = env
    assert director().command(OWNER, "dj", "training", "")[0].startswith("Maps mit Situationen: mirage (2)")
    assert "nicht im Trainings-Pool" in director().command(OWNER, "dj", "training", "cache")[0]


def test_full_flow(env):
    _, path, server, timers, director = env
    assert director().command(OWNER, "dj", "training", "mirage") == []
    assert "changelevel de_mirage" in server.sent

    director().on_lines([line('Loading map "de_mirage"')])
    run_timers(timers)
    assert "sv_cheats 1" in server.sent and "mp_warmup_start" in server.sent

    director().on_lines([line(f'"dj<2><[U:1:58996800]>" switched from team <Unassigned> to <TERRORIST>')])
    run_timers(timers)
    assert ts.load_state(path)["human_pawn"] == 385
    assert 'bot_loadout "m4a1"' in server.sent and "bot_add_ct" in server.sent

    server.pawns = ENT_BOTH
    director().on_lines([line('"K<4><BOT>" switched from team <Unassigned> to <CT>')])
    run_timers(timers)
    state = ts.load_state(path)
    assert (state["phase"], state["bot_pawn"]) == ("running", 387)
    assert server.sent[-1] == "mp_warmup_end"

    # Runde 1: Platzierung genau an den Demo-Positionen
    server.sent.clear()
    director().on_lines([line('World triggered "Round_Start"')])
    assert "ent_setpos 385 -822.8 -2301.1 -174.5" in server.sent
    assert "ent_setpos 387 -178.4 -1961.2 -168.0" in server.sent
    assert server.sent[0] == "bot_freeze 1"
    assert any(c.startswith("ent_setang 385 ") for c in server.sent)
    sec, release = timers.pop()
    assert sec == ts.FREEZE_TIME + ts.DELAYS[ts.START_LEVEL[1]]
    release()
    assert server.sent[-1] == "bot_freeze 0"

    director().on_lines([line(f'{DJ} [0 0 0] killed {BOT} [1 1 1] with "ak47" (headshot)'),
                         line('World triggered "Round_End"')])
    state = ts.load_state(path)
    assert state["results"] == ["win"] and state["pos"] == 1
    assert server.sent[-1] == 'bot_loadout "awp"'  # Waffe fuer das naechste Duell

    # Runde 2 verloren -> letzte Situation vorbei -> Training endet, Server zurueck
    director().on_lines([line('World triggered "Round_Start"'),
                         line(f'{BOT} [1 1 1] killed {DJ} [0 0 0] with "awp"'),
                         line('World triggered "Round_End"')])
    assert not ts.is_active(path)
    assert "sv_cheats 0" in server.sent and server.sent[-1] == "host_workshop_map 3245740485"
    assert any("1/2 Duelle gewonnen" in c for c in server.sent)


def test_old_release_timer_does_not_free_next_round(env):
    _, path, server, timers, director = env
    ts.save_state(path, {"phase": "running", "map": "de_mirage", "human": OWNER, "queue": [0, 1],
                         "pos": 0, "results": [], "level": [1, 1], "round": 0,
                         "human_pawn": 385, "bot_pawn": 387})
    director().on_lines([line('World triggered "Round_Start"')])
    old_release = timers.pop()[1]
    director().on_lines([line('World triggered "Round_End"'), line('World triggered "Round_Start"')])
    server.sent.clear()
    old_release()
    assert "bot_freeze 0" not in server.sent


def test_adaptive_level():
    d = ts.Director({}, "x", lambda *c: [""])
    state = {"results": [], "level": [1, 1]}
    sent = []
    d.send = lambda *c: sent.extend(c) or [""]
    for _ in range(5):
        d._result(state, "win")
    assert state["level"] == [1, 2] and state["window"] == []  # Bot startet frueher
    for _ in range(5):
        d._result(state, "loss")
    assert state["level"] == [1, 1]
    for _ in range(4):
        d._result(state, "win")
    d._result(state, "loss")
    assert state["level"] == [1, 2]
    state["level"] = [1, len(ts.DELAYS) - 1]
    d._shift(state, 1)
    assert state["level"] == [2, len(ts.DELAYS) - 1]  # erst wenn kein Vorsprung mehr: staerkerer Bot


def test_skip_ends_round_without_counting(env):
    _, path, server, timers, director = env
    ts.save_state(path, {"phase": "running", "map": "de_mirage", "human": OWNER, "queue": [0, 1],
                         "pos": 0, "results": [], "level": [1, 1], "round": 0,
                         "human_pawn": 385, "bot_pawn": 387})
    director().on_lines([line('World triggered "Round_Start"')])
    assert director().command(OWNER, "dj", "skip", "") == ["Übersprungen."]
    assert server.sent[-1] == "endround"
    director().on_lines([line('World triggered "Round_End"')])
    state = ts.load_state(path)
    assert state["results"] == [] and state["pos"] == 1


def test_stop_restores_server(env):
    _, path, server, _, director = env
    ts.save_state(path, {"phase": "running", "map": "de_mirage", "human": OWNER, "queue": [0],
                         "pos": 0, "results": ["win", "loss"], "level": [1, 1]})
    director().command(OWNER, "dj", "stop", "")
    assert not ts.is_active(path)
    assert "bot_kick" in server.sent and "sv_hibernate_when_empty 1" in server.sent
    assert any("1/2 Duelle gewonnen" in c for c in server.sent)


def test_disconnect_restores_server(env):
    _, path, server, _, director = env
    ts.save_state(path, {"phase": "running", "map": "de_mirage", "human": OWNER, "queue": [0],
                         "pos": 0, "results": [], "level": [1, 1], "round": 1})
    director().on_lines([line('"dj<2><[U:1:58996800]><TERRORIST>" disconnected (reason "Disconnect")')])
    assert not ts.is_active(path) and "sv_cheats 0" in server.sent


def test_disconnect_during_map_load_is_ignored(env):
    _, path, _, _, director = env
    ts.save_state(path, {"phase": "loading", "map": "de_mirage", "human": OWNER, "queue": [0],
                         "pos": 0, "results": [], "level": [1, 1]})
    director().on_lines([line('"dj<2><[U:1:58996800]><TERRORIST>" disconnected (reason "Map change")')])
    assert ts.is_active(path)
