"""Szenarien mit mehreren Gegnern: Auslesen aus der Demo und Ablauf mit mehreren Bots."""

from __future__ import annotations

import base64
import json

import pandas as pd
import pytest

from cs2_coach import training_scenarios as tsc
from cs2_coach import training_server as ts

ME = "76561198019262528"
A, B, C, MATE = "1001", "1002", "1003", "2001"


class FakeDemo:
    """Eine Runde: Freeze-Ende 1000, Plant 2000, dein erster Kontakt 3000, dein Tod 3200.

    Du bist CT (3). A und B (T) stehen nah, C weit weg; MATE ist dein Teamkollege.
    """

    def __init__(self, path):
        pass

    def parse_event(self, name):
        if name == "player_death":
            return pd.DataFrame({"tick": [3200], "user_steamid": [ME], "attacker_steamid": [A]})
        if name == "bomb_planted":
            return pd.DataFrame({"tick": [2000], "site": [179], "user_steamid": [A]})
        if name == "player_hurt":
            return pd.DataFrame({"tick": [3000, 3100], "user_steamid": [A, ME], "attacker_steamid": [ME, B]})
        return pd.DataFrame({"tick": [1000]})  # round_freeze_end

    def parse_ticks(self, props, ticks):
        rows = []
        for t in ticks:
            for sid, team, x, alive, inv, act in [
                (ME, 3, 0, True, ["Skeleton Knife", "USP-S", "M4A4", "Flashbang"], "M4A4"),
                (MATE, 3, 50, True, ["knife", "AWP"], "AWP"),
                (A, 2, 800, True, ["knife", "Glock-18", "AK-47"], "AK-47"),
                (B, 2, 1200 - (t - 1000) / 10, t < 3150, ["knife", "Glock-18", "AK-47", "Smoke Grenade"], "AK-47"),
                (C, 2, 6000, True, ["knife", "Glock-18", "AWP"], "AWP"),
            ]:
                rows.append({"tick": t, "steamid": sid, "name": f"p{sid}", "X": float(x), "Y": 0.0, "Z": 0.0,
                             "pitch": 0.0, "yaw": 180.0, "team_num": team, "is_alive": alive,
                             "inventory": inv, "active_weapon_name": act})
        return pd.DataFrame(rows)


def test_scan_finds_all_three_modes():
    out = tsc.scan_demo("x.dem", ME, 3, parser_factory=FakeDemo)
    sit = out["situation"][0]
    assert sit["tick"] == 3200 - 192 and sit["round"] == 1
    assert [e["name"] for e in sit["enemies"]] == [f"p{A}", f"p{B}"]  # Killer zuerst, C zu weit weg
    retake = out["retake"][0]
    assert retake["tick"] == 2000 + 64
    assert sorted(e["name"] for e in retake["enemies"]) == [f"p{A}", f"p{B}", f"p{C}"]  # beim Retake alle Ts
    hold = out["halten"][0]
    assert hold["tick"] == 3000 - 5 * 64 and len(hold["enemies"]) == 2
    b = next(e for e in hold["enemies"] if e["name"] == f"p{B}")
    assert len(b["path"]) < len(next(e for e in hold["enemies"] if e["name"] == f"p{A}")["path"])  # B stirbt
    assert sit["me_items"][:3] == ["weapon_m4a1", "weapon_knife", "weapon_usp_silencer"]
    assert all(e["name"] != f"p{MATE}" for m in out.values() for sc in m for e in sc["enemies"])


def test_t_side_rounds_are_no_retake_or_hold():
    class TSide(FakeDemo):
        def parse_ticks(self, props, ticks):
            df = super().parse_ticks(props, ticks)
            df["team_num"] = df["team_num"].map({3: 2, 2: 3})
            return df

    out = tsc.scan_demo("x.dem", ME, 3, parser_factory=TSide)
    assert out["retake"] == [] and out["halten"] == [] and out["situation"]


class Server:
    def __init__(self):
        self.sent: list[str] = []

    def __call__(self, *cmds):
        self.sent.extend(cmds)
        out = []
        for c in cmds:
            if c == "status":
                out.append("   2    09:33    0    0     active 786432 192.168.188.75:59684 'dj'")
            elif c == "coach_tp_version":
                out.append("coach_tp 1.6.0")
            elif c.startswith("coach_tp "):
                out.append(f"coach_tp ok {c.split()[1]}")
            elif c.startswith("coach_path_start"):
                out.append("coach_path ok start")
            else:
                out.append("ok")
        return out


def line(msg):
    return f"10/30/2026 - 01:00:00.000 - {msg}"


@pytest.fixture()
def multi(tmp_path, monkeypatch):
    items = tsc.scan_demo("x.dem", ME, 3, parser_factory=FakeDemo)["halten"]
    items = [{**it, "export": "e.json", "source": "27.10. R1"} for it in items]
    monkeypatch.setattr(tsc, "collect", lambda *a, **k: items)
    server, timers = Server(), []
    cfg = {"steam_id": ME, "obsidian_vault_path": ""}
    d = lambda: ts.Director(cfg, tmp_path / "config.yaml", server,  # noqa: E731
                            timer=lambda sec, fn: timers.append((sec, fn)))
    return d, server, timers, tmp_path / "config.yaml"


def test_hold_flow_with_two_bots(multi):
    director, server, timers, path = multi
    assert director().command(ME, "dj", "training", "mirage halten") == []
    state = ts.load_state(path)
    assert (state["mode"], state["bot_need"]) == ("halten", 2) and "map de_mirage" in server.sent
    timers.clear()
    director().on_lines([line('Loading map "de_mirage"'),
                         line('"dj<2><[U:1:58996800]>" switched from team <Unassigned> to <CT>')])
    timers.pop()[1]()  # _setup
    assert "bot_quota 2" in server.sent and "bot_join_team t" in server.sent
    director().on_lines([line('"Aa<3><BOT>" switched from team <Unassigned> to <TERRORIST>')])
    assert ts.load_state(path)["phase"] == "bot"  # wartet auf den zweiten
    director().on_lines([line('"Bb<4><BOT>" switched from team <Unassigned> to <TERRORIST>')])
    assert ts.load_state(path)["phase"] == "running" and server.sent[-1] == "mp_warmup_end"

    server.sent.clear()
    director().on_lines([line('World triggered "Round_Start"')])
    tps = [c for c in server.sent if c.startswith("coach_tp ")]
    assert len(tps) == 3  # du + zwei Bots
    assert sum(c.startswith("coach_path_start ") for c in server.sent) == 2
    assert any(c.startswith("coach_give 3 weapon_ak47") for c in server.sent)
    shown = [base64.b64decode(c.split()[3]).decode() for c in server.sent if c.startswith("coach_hud ")]
    assert "Site halten 1/1" in shown[0] and "2 Gegner: AK-47, AK-47" in shown[0]

    bot = '"Aa<3><BOT><TERRORIST>"'
    bot2 = '"Bb<4><BOT><TERRORIST>"'
    dj = '"dj<2><[U:1:58996800]><CT>"'
    hit = '(damage "27") (damage_armor "3") (health "73") (armor "96") (hitgroup "head")'
    director().on_lines([line(f'{dj} [0 0 0] attacked {bot} [1 1 1] with "m4a1" {hit}'),
                         line(f'{dj} [0 0 0] killed {bot} [1 1 1] with "m4a1" (headshot)')])
    assert ts.load_state(path)["outcome"] is None  # erst einer von zweien tot
    director().on_lines([line(f'{dj} [0 0 0] killed {bot2} [1 1 1] with "m4a1"')])
    assert ts.load_state(path)["outcome"] == "win"


def test_fewer_enemies_than_bots_slays_the_rest(multi, tmp_path):
    director, server, timers, path = multi
    director().command(ME, "dj", "training", "mirage halten")
    items = json.loads((path.parent / ts.ITEMS_FILE).read_text())
    items[0]["enemies"] = items[0]["enemies"][:1]
    (path.parent / ts.ITEMS_FILE).write_text(json.dumps(items))
    state = ts.load_state(path)
    state.update(phase="running", human_uid=2, bot_uids=[3, 4], bot_uid=3)
    ts.save_state(path, state)
    director().on_lines([line('World triggered "Round_Start"')])
    assert "coach_slay 4" in server.sent
    assert ts.load_state(path)["rnd"]["active_bots"] == [3]
    director().on_lines([line('"dj<2><[U:1:58996800]><CT>" [0 0 0] killed "Aa<3><BOT><TERRORIST>" [1 1 1] with "m4a1"')])
    assert ts.load_state(path)["outcome"] == "win"


def test_mode_menu_after_map(multi):
    director, server, _, _ = multi
    director().command(ME, "dj", "training", "mirage menu", 2)
    m = json.loads(base64.b64decode(next(c for c in server.sent if c.startswith("coach_menu 2 ")).split()[2]))
    assert [i[1] for i in m["items"]] == ["!training mirage duell", "!training mirage situation",
                                          "!training mirage retake", "!training mirage halten", "!training mirage eichen",
                                          "!training"]


def test_unknown_mode_is_rejected(multi):
    director, _, _, _ = multi
    assert "Unbekannter Modus" in director().command(ME, "dj", "training", "mirage quatsch")[0]



def test_demo_without_plant_still_scans():
    """demoparser2 gibt [] statt einer Tabelle zurueck, wenn es kein bomb_planted gibt."""
    class NoPlant(FakeDemo):
        def parse_event(self, name):
            return [] if name == "bomb_planted" else super().parse_event(name)

    out = tsc.scan_demo("x.dem", ME, 3, parser_factory=NoPlant)
    assert out["retake"] == [] and out["situation"] and out["halten"]



def test_teammates_from_the_demo_are_kept_for_coop():
    out = tsc.scan_demo("x.dem", ME, 3, parser_factory=FakeDemo)
    mates = out["retake"][0]["mates"]
    assert [m["name"] for m in mates] == [f"p{MATE}"]
    assert mates[0]["items"][0] == "weapon_awp" and mates[0]["start"][0] == 50.0


def test_friend_starts_where_your_teammate_stood(multi):
    director, server, timers, path = multi
    director().command(ME, "dj", "training", "mirage retake")
    state = ts.load_state(path)
    state.update(phase="running", human_uid=2, bot_uids=[3, 4, 5], bot_uid=3,
                 party={"7656": {"name": "Freund", "uid": 9}})
    items = tsc.scan_demo("x.dem", ME, 3, parser_factory=FakeDemo)["retake"]
    (path.parent / ts.ITEMS_FILE).write_text(json.dumps(items))
    ts.save_state(path, state)
    server.sent.clear()
    director().on_lines([line('World triggered "Round_Start"')])
    tp = next(c for c in server.sent if c.startswith("coach_tp 9 "))
    assert tp.split()[2] == "50.0"  # Position von MATE aus der Demo
    assert any(c.startswith("coach_give 9 weapon_awp") for c in server.sent)
    assert any(c.startswith("coach_hud 9 ") for c in server.sent)


def test_calibration_round_hides_bot_and_saves_result(tmp_path):
    import json as _json
    from cs2_coach import training_server as ts
    folder = tmp_path / "CS2-Coach" / "exports"
    folder.mkdir(parents=True)
    kills = [{"t": "k", "x": 0, "y": 0, "z": 0, "ex": 800, "ey": 0, "ez": 0, "w": "ak47", "r": i} for i in range(12)]
    kills += [{"t": "k", "x": 0, "y": 0, "z": 0, "ex": 50, "ey": 0, "ez": 0, "w": "ak47"},  # zu nah
              {"t": "k", "x": 0, "y": 0, "z": 0, "ex": 800, "ey": 0, "ez": 0, "w": "knife"}]
    (folder / "2026-10-27_mirage_13-8_2049_coach.json").write_text(_json.dumps(
        {"match": {"map": "Mirage"}, "kill_positions": kills}))
    cfg = {"obsidian_vault_path": str(tmp_path)}
    items = ts.calibration_items(cfg, "de_mirage")
    assert len(items) == ts.CALIB_ROUNDS and items[0]["enemies"][0]["start"][:3] == (800, 0, 0) or \
        list(items[0]["enemies"][0]["start"][:3]) == [800, 0, 0]
    assert ts.parse_stats("coach_stats 2 seen=2000 start=0 appear_seen=47 appear_shot=265")["appear_shot_ms"] == 265
    duels = [{"appear_seen_ms": 40 + i, "appear_shot_ms": 250 + 10 * i} for i in range(5)]
    c = ts.calibration_from(duels)
    assert c["reaction_ms"] == 270 and c["latency_ms"] == 42
    assert ts.calibration_from(duels[:4]) is None


def test_calibration_round_start_sends_appear(multi):
    director, server, timers, path = multi
    state = {"phase": "running", "map": "de_mirage", "mode": "eichen", "human": ME, "queue": [0], "pos": 0,
             "results": [], "level": 3, "round": 0, "human_uid": 2, "bot_uids": [3], "bot_uid": 3}
    item = {"me": [0, 0, 0, 0, 0], "me_items": [], "mates": [], "source": "Eichung",
            "enemies": [{"name": "Ziel", "start": [800, 0, 0, 0, 180], "path": [], "items": []}]}
    (path.parent / ts.ITEMS_FILE).write_text(json.dumps([item]))
    ts.save_state(path, state)
    director().on_lines([line('World triggered "Round_Start"')])
    assert "bot_freeze 1" in server.sent
    assert any(c.startswith("coach_appear 3 ") for c in server.sent)
    assert not any(c.startswith("coach_tp 3 ") for c in server.sent)
