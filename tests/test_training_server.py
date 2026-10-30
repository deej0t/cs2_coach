"""Duell-Training: Situationen aus Exporten, Ablauf Start -> Team -> Runden -> Ende, adaptive Stufe."""

from __future__ import annotations

import json
import time

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

def huds(server) -> list[str]:
    """Inhalt aller Einblendungen (coach_hud <uid> <s> <base64>)."""
    import base64
    return [base64.b64decode(c.split()[3]).decode("utf-8") for c in server.sent if c.startswith("coach_hud ")]


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
    """Antwortet wie der echte Server mit Plugin (coach_tp 1.0.0, 30.10.2026)."""

    def __init__(self, status=STATUS_ALONE):
        self.sent: list[str] = []
        self.status = status
        self.plugin = True
        self.tp_reply = "coach_tp ok"
        self.path_reply = "coach_path ok start 49"

    def __call__(self, *cmds):
        self.sent.extend(cmds)
        out = []
        for c in cmds:
            if c == "status":
                out.append(self.status)
            elif c == "coach_tp_version":
                out.append("coach_tp 1.0.0" if self.plugin else "Unknown command 'coach_tp_version'!")
            elif c.startswith("coach_tp "):
                out.append(f"{self.tp_reply} {c.split()[1]}")
            elif c.startswith("coach_menu"):
                out.append("coach_menu ok 3" if self.plugin else "Unknown command 'coach_menu'!")
            elif c.startswith("coach_path_start"):
                out.append(self.path_reply)
            elif c.startswith("coach_path"):
                out.append("coach_path ok")
            else:
                out.append("")
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


def test_full_flow(env, monkeypatch):
    from cs2_coach import ingame_coach
    monkeypatch.setattr(ingame_coach, "ask", lambda cfg, system, prompt: "Gutes Training, weiter so.")
    _, path, server, timers, director = env
    assert director().command(OWNER, "dj", "training", "mirage") == []
    assert "map de_mirage" in server.sent

    director().on_lines([line('Loading map "de_mirage"')])
    run_timers(timers)
    assert "sv_cheats 1" in server.sent and "mp_warmup_start" in server.sent

    director().on_lines([line('"dj<2><[U:1:58996800]>" switched from team <Unassigned> to <TERRORIST>')])
    assert ts.load_state(path)["human_uid"] == 2
    assert 'bot_loadout "m4a1"' in server.sent and "bot_join_team ct" in server.sent
    assert server.sent[-1] == "bot_quota 1"

    director().on_lines([line('"K<4><BOT>" switched from team <Unassigned> to <CT>')])
    state = ts.load_state(path)
    assert (state["phase"], state["bot_uid"]) == ("running", 4)
    assert server.sent[-1] == "mp_warmup_end"

    # Runde 1: beide per Plugin an die Demo-Positionen (hier ohne Demo: am Todesort)
    server.sent.clear()
    director().on_lines([line('World triggered "Round_Start"')])
    assert server.sent[0].startswith("coach_tp 2 -822.8 -2301.1 -174.5 ")
    assert server.sent[1].startswith("coach_tp 4 -178.4 -1961.2 -168.0 ")
    assert "bot_freeze 1" not in server.sent  # eingefrorene Bots schiessen nicht
    assert not any("fehlgeschlagen" in c for c in server.sent)

    director().on_lines([line(f'{DJ} [0 0 0] killed {BOT} [1 1 1] with "ak47" (headshot)'),
                         line('World triggered "Round_End"')])
    state = ts.load_state(path)
    assert state["results"] == ["win"] and state["pos"] == 1
    assert server.sent[-1] == 'bot_loadout "awp"'  # Waffe fuer das naechste Duell

    # Runde 2 verloren -> letzte Situation vorbei -> Training endet, Server zurueck
    director().on_lines([line('World triggered "Round_Start"'),
                         line(f'{DJ} [0 0 0] attacked {BOT} [1 1 1] with "ak47" (damage "27") (damage_armor "3") '
                              '(health "73") (armor "96") (hitgroup "chest")'),
                         line(f'{BOT} [1 1 1] killed {DJ} [0 0 0] with "awp"'),
                         line('World triggered "Round_End"')])
    assert not ts.is_active(path)
    assert any("1/2 Duelle gewonnen" in c for c in server.sent)
    # Map bleibt noch stehen ("abrupt" gewechselt), Fazit kommt privat, dann zurueck
    assert "sv_cheats 0" not in server.sent and "bot_kick" in server.sent
    assert any("TRAINING FERTIG" in h and "Map wechselt in 30 s" in h for h in huds(server))
    assert sorted(sec for sec, _ in timers) == [0.1, ts.FAREWELL_SECONDS]
    run_timers(timers)
    assert any(c.startswith("coach_chat 2 ") for c in server.sent)
    assert "sv_cheats 0" in server.sent and server.sent[-1] == "host_workshop_map 3245740485"
    assert ts.load_sessions(path)[0]["fazit"] == "Gutes Training, weiter so."


def test_adaptive_level():
    d = ts.Director({}, "x", lambda *c: [""])
    state = {"results": [], "level": 1}
    sent = []
    d.send = lambda *c: sent.extend(c) or [""]
    for _ in range(5):
        d._result(state, "win")
    assert state["level"] == 2 and state["window"] == [] and "bot_difficulty 2" in sent
    for _ in range(5):
        d._result(state, "loss")
    assert state["level"] == 1
    for _ in range(4):
        d._result(state, "win")
    d._result(state, "loss")
    assert state["level"] == 2
    state["level"] = 3
    d._shift(state, 1)
    assert state["level"] == 3  # Obergrenze
    state["level"] = 0
    d._shift(state, -1)
    assert state["level"] == 0


def test_skip_ends_round_without_counting(env):
    _, path, server, timers, director = env
    ts.save_state(path, {"phase": "running", "map": "de_mirage", "human": OWNER, "queue": [0, 1],
                         "pos": 0, "results": [], "level": 1, "round": 0,
                         "human_uid": 2, "bot_uid": 4})
    director().on_lines([line('World triggered "Round_Start"')])
    assert director().command(OWNER, "dj", "skip", "") == ["Übersprungen."]
    assert server.sent[-1] == "endround"
    director().on_lines([line('World triggered "Round_End"')])
    state = ts.load_state(path)
    assert state["results"] == [] and state["pos"] == 1


def test_stop_restores_server(env):
    _, path, server, _, director = env
    ts.save_state(path, {"phase": "running", "map": "de_mirage", "human": OWNER, "queue": [0],
                         "pos": 0, "results": ["win", "loss"], "level": 1})
    director().command(OWNER, "dj", "stop", "")
    assert not ts.is_active(path)
    assert "bot_kick" in server.sent and "sv_hibernate_when_empty 1" in server.sent
    assert any("1/2 Duelle gewonnen" in c for c in server.sent)


def test_disconnect_restores_server(env):
    _, path, server, _, director = env
    ts.save_state(path, {"phase": "running", "map": "de_mirage", "human": OWNER, "queue": [0],
                         "pos": 0, "results": [], "level": 1, "round": 1})
    director().on_lines([line('"dj<2><[U:1:58996800]><TERRORIST>" disconnected (reason "Disconnect")')])
    assert not ts.is_active(path) and "sv_cheats 0" in server.sent


def test_disconnect_during_map_load_is_ignored(env):
    _, path, _, _, director = env
    ts.save_state(path, {"phase": "loading", "map": "de_mirage", "human": OWNER, "queue": [0],
                         "pos": 0, "results": [], "level": 1, "t0": time.time()})
    director().on_lines([line('"dj<2><[U:1:58996800]><TERRORIST>" disconnected (reason "Map change")')])
    assert ts.is_active(path)


def test_map_not_loaded_in_time_is_discarded(env):
    """30.10.: der Server hing beim Mapwechsel. Das Training darf danach nicht weiterleben."""
    _, path, server, timers, director = env
    director().command(OWNER, "dj", "training", "mirage")
    sec, check = timers.pop()
    assert sec == ts.LOAD_TIMEOUT
    check()
    assert not ts.is_active(path) and "sv_cheats 0" in server.sent


def test_stale_loading_state_ignores_server_restart(env):
    """Startet der haengende Server neu, meldet er "Loading map de_mirage" - das alte Training nicht aufsetzen."""
    _, path, server, timers, director = env
    ts.save_state(path, {"phase": "loading", "map": "de_mirage", "human": OWNER, "queue": [0],
                         "pos": 0, "results": [], "level": 1, "t0": 0})
    director().on_lines([line('Loading map "de_mirage"')])
    assert not ts.is_active(path) and timers == [] and "sv_cheats 1" not in server.sent


def test_check_loaded_survives_dead_rcon(tmp_path):
    root = export(tmp_path)

    def dead(*cmds):
        raise TimeoutError("timed out")

    d = ts.Director({"steam_id": OWNER, "obsidian_vault_path": str(root / "vault")},
                    tmp_path / "config.yaml", dead, timer=lambda s, f: None)
    ts.save_state(d.path, {"phase": "loading", "map": "de_mirage", "t0": 0})
    d._check_loaded()
    assert not ts.is_active(d.path)


def test_team_chosen_before_setup_is_remembered(env):
    """30.10.: dj trat T bei, 2 s bevor das Setup lief - der Ablauf hing danach in "team"."""
    _, path, server, timers, director = env
    director().command(OWNER, "dj", "training", "mirage")
    timers.clear()  # Lade-Zeitlimit
    director().on_lines([line('Loading map "de_mirage"'),
                         line('"dj<0><[U:1:58996800]>" switched from team <Unassigned> to <TERRORIST>')])
    run_timers(timers)  # _setup -> Team bekannt -> Bot kommt
    state = ts.load_state(path)
    assert (state["phase"], state["human_uid"]) == ("bot", 0)
    assert any("Team T erkannt" in c for c in server.sent) and server.sent[-1] == "bot_quota 1"


def test_second_bot_is_kicked(env):
    """30.10.: ein bot_add_ct brachte zwei Bots."""
    _, path, server, _, director = env
    ts.save_state(path, {"phase": "bot", "map": "de_mirage", "human": OWNER, "queue": [0], "pos": 0,
                         "results": [], "level": 1, "human_uid": 0, "team": "TERRORIST"})
    director().on_lines([line('"Juuvy<3><BOT>" switched from team <Unassigned> to <CT>'),
                         line('"Aggressor<4><BOT>" switched from team <Unassigned> to <CT>')])
    assert ts.load_state(path)["bot_uid"] == 3 and server.sent[-1] == "kickid 4"


def test_start_refused_without_plugin(env):
    _, path, server, _, director = env
    server.plugin = False
    assert "Plugin nicht geladen" in director().command(OWNER, "dj", "training", "mirage")[0]
    assert not ts.is_active(path) and "map de_mirage" not in server.sent


def test_failed_teleport_is_reported_once(env):
    _, path, server, _, director = env
    server.tp_reply = "coach_tp: kein Spieler mit userid"
    ts.save_state(path, {"phase": "running", "map": "de_mirage", "human": OWNER, "queue": [0, 1],
                         "pos": 0, "results": [], "level": 1, "round": 0, "human_uid": 2, "bot_uid": 4})
    director().on_lines([line('World triggered "Round_Start"'), line('World triggered "Round_End"'),
                         line('World triggered "Round_Start"')])
    assert sum("Versetzen fehlgeschlagen" in c for c in server.sent) == 1


class FakeParser:
    """Antwortet wie demoparser2 an der echten Mirage-Demo vom 27.10. (Werte gekuerzt)."""

    def __init__(self, path):
        import pandas as pd
        self.pd = pd
        self.calls = 0

    def parse_event(self, name):
        pd = self.pd
        if name == "player_death":
            return pd.DataFrame({"tick": [17263, 900], "user_steamid": [OWNER, OWNER],
                                 "attacker_steamid": ["765", "766"]})
        return pd.DataFrame({"tick": [16500, 800]})  # round_freeze_end

    def parse_ticks(self, props, ticks):
        self.calls += 1
        rows = []
        for t in ticks:
            rows += [{"tick": t, "steamid": OWNER, "X": -876.5, "Y": -2262.3, "Z": -171.5, "pitch": 356.3, "yaw": 22.9,
                      "inventory": ["Skeleton Knife", "Glock-18", "MP9", "High Explosive Grenade"],
                      "active_weapon_name": "MP9"},
                     {"tick": t, "steamid": "765", "X": -199.6, "Y": -1922.5, "Z": -168.0, "pitch": 0.4, "yaw": 207.9,
                      "inventory": ["knife", "P2000", "M4A4"], "active_weapon_name": "M4A4"},
                     {"tick": t, "steamid": "766", "X": 1.0, "Y": 2.0, "Z": 3.0, "pitch": 0.0, "yaw": 0.0}]
        return self.pd.DataFrame(rows)


def test_pre_positions_three_seconds_before_death():
    pre = ts.pre_positions("demo.dem", OWNER, [17263, 900], parser_factory=FakeParser)
    a = pre[17263]
    assert a["me"] == [-876.5, -2262.3, -171.5, -3.7, 22.9]  # 356,3 Grad = 3,7 nach oben
    assert a["foe"] == [-199.6, -1922.5, -168.0, 0.4, -152.1]
    assert a["lead"] == 3.0
    assert pre[900]["lead"] == round(100 / 64, 1)  # nie vor das Ende der Freeze-Time


def test_prepare_caches_per_demo(tmp_path):
    (tmp_path / "demos").mkdir()
    (tmp_path / "demos" / "m.dem").write_bytes(b"x")
    cfg = {"demo_folder": str(tmp_path / "demos"), "steam_id": OWNER}
    scs = [ts.Scenario("de_mirage", 0, 0, 0, 1, 1, 1, "m4a1", "x", "s", 17263, "m.dem"),
           ts.Scenario("de_mirage", 0, 0, 0, 1, 1, 1, "m4a1", "x", "s", 555, "m.dem"),
           ts.Scenario("de_mirage", 0, 0, 0, 1, 1, 1, "m4a1", "x", "s", 17263, "fehlt.dem")]
    made = []

    def factory(path):
        made.append(path)
        return FakeParser(path)

    pre = ts.prepare(cfg, tmp_path / "config.yaml", scs, parser_factory=factory)
    assert set(pre) == {0} and len(made) == 1  # Tick 555 ist kein Tod von dir, fehlende Demo uebersprungen
    ts.prepare(cfg, tmp_path / "config.yaml", scs[:1], parser_factory=factory)
    assert len(made) == 1  # aus dem Zwischenspeicher


def test_round_start_uses_demo_positions(env):
    _, path, server, _, director = env
    ts.save_state(path, {"phase": "running", "map": "de_mirage", "human": OWNER, "queue": [0, 1],
                         "pos": 0, "results": [], "level": 1, "round": 0, "human_uid": 2, "bot_uid": 4,
                         "pre": {"0": {"me": [-876.5, -2262.3, -171.5, -3.7, 22.9],
                                       "foe": [-199.6, -1922.5, -168.0, 0.4, -152.1], "lead": 3.0}}})
    director().on_lines([line('World triggered "Round_Start"')])
    # Blick zum Gegner (nicht die Blickrichtung von damals: "mit dem Ruecken zum Gegner")
    assert server.sent[0].startswith("coach_tp 2 -876.5 -2262.3 -171.5 ")
    pitch, yaw = map(float, server.sent[0].split()[5:7])
    assert abs(yaw - 26.7) < 1 and abs(pitch) < 2
    assert server.sent[1] == "coach_tp 4 -199.6 -1922.5 -168.0 0.4 -152.1"
    assert any("3 s vor dem Duell" in c for c in server.sent)
    shown = huds(server)
    assert "Duell 1/2" in shown[0] and "Gegner mit M4A4" in shown[0] and "kommt von vorne" in shown[0]


def test_weapon_names_from_demo():
    assert ts.classname("M4A4") == "weapon_m4a1" and ts.classname("M4A1-S") == "weapon_m4a1_silencer"
    assert ts.classname("Skeleton Knife") == "weapon_knife" and ts.classname("knife") == "weapon_knife"
    assert ts.classname("High Explosive Grenade") == "weapon_hegrenade"
    assert ts.classname("C4 Explosive") == ""


def test_give_full_kit_to_you_and_only_guns_to_bot():
    """Echte Inventare aus R5 der Mirage-Demo vom 27.10."""
    pre = {"me_items": ["weapon_ak47", "weapon_knife", "weapon_glock", "weapon_ak47", "weapon_flashbang",
                        "weapon_flashbang", "weapon_hegrenade"],
           "foe_items": ["weapon_aug", "weapon_knife", "weapon_usp_silencer", "weapon_aug",
                         "weapon_smokegrenade", "weapon_hegrenade"]}
    give = ts._give_commands(2, 4, pre)
    assert give[0] == ("coach_give 2 weapon_ak47 weapon_knife weapon_glock weapon_ak47 "
                       "weapon_flashbang weapon_flashbang weapon_hegrenade")
    assert give[1] == "coach_give 4 weapon_aug weapon_usp_silencer weapon_aug"
    assert ts._give_commands(2, 4, None) == []  # ohne Demo: Standardausruestung
    assert "mp_give_player_c4 0" in ts.SETUP



def running_state(**extra):
    st = {"phase": "running", "map": "de_mirage", "human": OWNER, "queue": [0, 1], "pos": 0,
          "results": [], "level": 1, "round": 0, "human_uid": 2, "bot_uid": 4}
    st.update(extra)
    return st


def test_round_without_hit_repeats_duel_then_pauses(env):
    """30.10.: AFK-Runden zaehlten als Duelle, nach 20 Runden kam die Aim-Map zurueck."""
    _, path, server, timers, director = env
    ts.save_state(path, running_state())
    for _ in range(2):
        director().on_lines([line('World triggered "Round_Start"'),
                             line(f'{BOT} [1 1 1] killed {DJ} [0 0 0] with "m4a1"'),
                             line('World triggered "Round_End"')])
    state = ts.load_state(path)
    assert state["pos"] == 0 and state["results"] == []  # nichts gezaehlt, gleiches Duell
    assert state["paused"] and "bot_freeze 1" in server.sent and "mp_ignore_round_win_conditions 1" in server.sent
    assert "mp_pause_match" not in server.sent  # CS2-Pause verdraengte das Menue
    assert any("KEIN TREFFER" in h for h in huds(server)) and any("PAUSE" in h for h in huds(server))
    assert timers[-1][0] == ts.PAUSE_TIMEOUT

    assert director().command(OWNER, "dj", "weiter", "") == []
    assert server.sent[-1] == "endround" and not ts.load_state(path)["paused"]
    director().on_lines([line('World triggered "Round_End"')])  # Runde durch !weiter beendet
    state = ts.load_state(path)
    assert state["pos"] == 0 and state["results"] == []  # dasselbe Duell, nichts gezaehlt


def test_long_pause_ends_training(env):
    _, path, server, timers, director = env
    ts.save_state(path, running_state(idle=1))
    director().on_lines([line('World triggered "Round_Start"'), line('World triggered "Round_End"')])
    timers.pop()[1]()  # PAUSE_TIMEOUT abgelaufen
    assert not ts.is_active(path) and any("pausiert" in c for c in server.sent)


def test_resumed_pause_is_not_ended_by_old_timer(env):
    _, path, server, timers, director = env
    ts.save_state(path, running_state(idle=1))
    director().on_lines([line('World triggered "Round_Start"'), line('World triggered "Round_End"')])
    old = timers.pop()[1]
    director().command(OWNER, "dj", "weiter", "")
    old()
    assert ts.is_active(path)


def test_round_with_hit_counts_even_when_lost(env):
    _, path, server, _, director = env
    ts.save_state(path, running_state())
    director().on_lines([line('World triggered "Round_Start"'),
                         line(f'{DJ} [0 0 0] attacked {BOT} [1 1 1] with "ak47" (damage "27") (damage_armor "3") '
                              '(health "73") (armor "96") (hitgroup "chest")'),
                         line(f'{BOT} [1 1 1] killed {DJ} [0 0 0] with "m4a1"'),
                         line('World triggered "Round_End"')])
    state = ts.load_state(path)
    assert state["results"] == ["loss"] and state["pos"] == 1


def test_killer_path_from_demo():
    pre = ts.pre_positions("demo.dem", OWNER, [17263], parser_factory=FakeParser)[17263]
    assert len(pre["path"]) == 3 * ts.PATH_HZ + 1  # 3 s mit 16 Punkten pro Sekunde, Start und Ende
    assert pre["path"][0] == pre["foe"]


def test_round_start_sends_path_in_short_commands(env):
    _, path, server, _, director = env
    pts = [[float(i), -1922.5, -168.0, 0.4, -152.1] for i in range(49)]
    ts.save_state(path, {"phase": "running", "map": "de_mirage", "human": OWNER, "queue": [0, 1],
                         "pos": 0, "results": [], "level": 1, "round": 0, "human_uid": 2, "bot_uid": 4,
                         "pre": {"0": {"me": [0, 0, 0, 0, 0], "foe": pts[0], "lead": 3.0, "path": pts}}})
    director().on_lines([line('World triggered "Round_Start"')])
    adds = [c for c in server.sent if c.startswith("coach_path_add 4 ")]
    assert server.sent[2] == "coach_path_clear 4" and len(adds) == 7  # 49 Punkte zu je 8
    assert all(len(c) < 400 for c in adds)
    assert adds[0].startswith("coach_path_add 4 0.0 -1922.5 -168.0 0.4 -152.1 1.0 ")
    assert "coach_path_start 4 16" in server.sent
    assert any("läuft den echten Weg" in c for c in server.sent)
    assert not any("nicht moeglich" in c for c in server.sent)


def test_old_plugin_without_path_is_reported(env):
    _, path, server, _, director = env
    server.path_reply = "Unknown command 'coach_path_start'!"
    pts = [[0, 0, 0, 0, 0], [1, 1, 1, 0, 0]]
    ts.save_state(path, {"phase": "running", "map": "de_mirage", "human": OWNER, "queue": [0, 1],
                         "pos": 0, "results": [], "level": 1, "round": 0, "human_uid": 2, "bot_uid": 4,
                         "pre": {"0": {"me": [0, 0, 0, 0, 0], "foe": pts[0], "lead": 3.0, "path": pts}}})
    director().on_lines([line('World triggered "Round_Start"')])
    assert any("Weg ablaufen nicht moeglich" in c for c in server.sent)


def test_inventory_from_demo():
    pre = ts.pre_positions("demo.dem", OWNER, [17263], parser_factory=FakeParser)[17263]
    assert pre["me_items"] == ["weapon_mp9", "weapon_knife", "weapon_glock", "weapon_mp9", "weapon_hegrenade"]
    assert pre["foe_items"] == ["weapon_m4a1", "weapon_knife", "weapon_hkp2000", "weapon_m4a1"]


def test_direction_relative_to_your_view():
    me = [0, 0, 0, 0, 0]  # Blick nach +X
    assert ts.direction(me, [1000, 0, 0]) == "vorne, 25 m"
    assert ts.direction(me, [0, 1000, 0]).startswith("links,")  # Source: +Y liegt links
    assert ts.direction(me, [0, -1000, 0]).startswith("rechts,")
    assert ts.direction(me, [-1000, 10, 0]).startswith("hinten")
    assert ts.direction(me, [700, 700, 0]).startswith("links vorne")


def test_result_is_shown_big_and_duel_recorded(env):
    _, path, server, _, director = env
    ts.save_state(path, running_state())
    director().on_lines([line('World triggered "Round_Start"'),
                         line(f'{DJ} [0 0 0] killed {BOT} [1 1 1] with "ak47" (headshot)'),
                         line('World triggered "Round_End"')])
    assert any("GEWONNEN" in h and "Stand 1:0" in h for h in huds(server))
    duel = ts.load_state(path)["duels"][0]
    assert (duel["outcome"], duel["tries"], duel["opponent"], duel["round"]) == ("win", 1, "Pöpp Escobar", 3)
    assert duel["export"].endswith("_coach.json")


def test_menu_without_training_lists_maps(env):
    _, path, server, _, director = env
    assert director().command(OWNER, "dj", "training", "", 2) == []
    import base64, json
    m = json.loads(base64.b64decode(next(c for c in server.sent if c.startswith("coach_menu 2 ")).split()[2]))
    assert m["items"][0] == ["Mirage (2 Duelle)", "!training mirage"] and m["items"][-1][1] == ""
    assert not ts.is_active(path)


def test_menu_during_training_offers_actions(env):
    _, path, server, _, director = env
    ts.save_state(path, running_state(paused=True))
    director().command(OWNER, "dj", "menu", "", 2)
    import base64, json
    m = json.loads(base64.b64decode(next(c for c in server.sent if c.startswith("coach_menu 2 ")).split()[2]))
    assert [i[1] for i in m["items"]] == ["!weiter", "!skip", "!leichter", "!schwerer", "!stop", ""]


def test_menu_with_old_plugin_falls_back_to_commands(env):
    _, _, server, _, director = env
    server.plugin = False
    assert "aktuelle Server-Plugin" in director().command(OWNER, "dj", "menu", "", 2)[0]


def test_finished_training_is_saved_for_the_web_page(env):
    cfg, path, server, _, director = env
    cfg["public_url"] = "https://cs2-coach.deej0t.eu"
    ts.save_state(path, running_state(queue=[0], started="2026-10-30T08:00:00"))
    director().on_lines([line('World triggered "Round_Start"'),
                         line(f'{BOT} [1 1 1] attacked {DJ} [0 0 0] with "m4a1" (damage "27") (damage_armor "3") '
                              '(health "73") (armor "96") (hitgroup "chest")'),
                         line(f'{DJ} [0 0 0] attacked {BOT} [1 1 1] with "ak47" (damage "27") (damage_armor "3") '
                              '(health "73") (armor "96") (hitgroup "chest")'),
                         line(f'{BOT} [1 1 1] killed {DJ} [0 0 0] with "m4a1"'),
                         line('World triggered "Round_End"')])
    assert not ts.is_active(path)
    sessions = ts.load_sessions(path)
    assert len(sessions) == 1 and sessions[0]["played"] == 1 and sessions[0]["won"] == 0
    assert any("cs2-coach.deej0t.eu/training" in c for c in server.sent)
    assert any("TRAINING FERTIG" in h for h in huds(server))
    hard = ts.hardest(sessions)
    assert hard[0]["lost"] == 1 and hard[0]["opponent"] == "Pöpp Escobar"


def test_training_page_renders_sessions(tmp_path, monkeypatch):
    """Die Seite mit gespeichertem Training - der Smoke-Test sieht sie nur leer."""
    import importlib
    cfg = tmp_path / "config.yaml"
    cfg.write_text("language: de\n", encoding="utf-8")
    monkeypatch.setenv("CS2COACH_CONFIG", str(cfg))
    import cs2_coach.web.app as app_module
    try:
        mod = importlib.reload(app_module)
        ts.save_session(cfg, {"map": "de_mirage", "started": "2026-10-30T08:00:00", "level": 2, "duels": [
            {"n": 1, "source": "27.10. R3", "opponent": "Pöpp Escobar", "weapon": "m4a1", "outcome": "loss",
             "tries": 2, "export": "2026-10-27_mirage_13-8_2049_coach.json", "round": 3}]}, "Training fertig")
        f = next(ts.sessions_dir(cfg).glob("*.json"))
        import json as _json
        data = _json.loads(f.read_text(encoding="utf-8")); data["fazit"] = "Halte die Winkel enger."
        f.write_text(_json.dumps(data), encoding="utf-8")
        client = mod.create_app().test_client()
        page = client.get("/training").get_data(as_text=True)
        assert "0 von 1 gewonnen" in page and "Pöpp Escobar" in page and "M4A4" in page
        assert "/export/2026-10-27_mirage_13-8_2049_coach.json" in page
        assert "Fazit des Coaches:</strong> Halte die Winkel enger." in page
    finally:
        monkeypatch.delenv("CS2COACH_CONFIG")
        importlib.reload(app_module)


def test_lost_duel_gives_tip_and_remembers_it_for_tipp(env):
    _, path, server, _, director = env
    ts.save_state(path, running_state())
    hit = '(damage "27") (damage_armor "3") (health "73") (armor "96") (hitgroup "{g}")'
    director().on_lines([line('World triggered "Round_Start"'),
                         line(f'{BOT} [1 1 1] attacked {DJ} [0 0 0] with "m4a1" ' + hit.format(g="chest")),
                         line(f'{DJ} [0 0 0] attacked {BOT} [1 1 1] with "ak47" ' + hit.format(g="chest")),
                         line(f'{DJ} [0 0 0] attacked {BOT} [1 1 1] with "ak47" ' + hit.format(g="stomach")),
                         line(f'{BOT} [1 1 1] killed {DJ} [0 0 0] with "m4a1"'),
                         line('World triggered "Round_End"')])
    duel = ts.load_state(path)["duels"][0]
    assert (duel["first"], duel["you_hits"], duel["you_head"], duel["bot_hits"]) == ("bot", 2, 0, 1)
    assert duel["tip"].startswith("Keiner deiner 2 Treffer am Kopf")
    assert any("Keiner deiner 2 Treffer" in h for h in huds(server))
    from cs2_coach.ingame_coach import load_last_duel
    last = load_last_duel(path)
    assert last["weapon"] == "M4A4" and last["direction"].startswith("vorne")



def test_restore_waits_unless_new_training_started(env):
    _, path, server, timers, director = env
    ts.save_state(path, running_state(human_uid=2))
    director().command(OWNER, "dj", "stop", "")
    assert "sv_cheats 0" not in server.sent
    ts.save_state(path, running_state())  # in den 30 s ein neues Training gestartet
    next(fn for sec, fn in timers if sec == ts.FAREWELL_SECONDS)()
    assert "sv_cheats 0" not in server.sent


def test_disconnect_still_restores_immediately(env):
    _, path, server, timers, director = env
    ts.save_state(path, running_state(round=1))
    director().on_lines([line('"dj<2><[U:1:58996800]><TERRORIST>" disconnected (reason "Disconnect")')])
    assert "sv_cheats 0" in server.sent and not timers
