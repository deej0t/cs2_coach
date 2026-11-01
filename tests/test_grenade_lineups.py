"""Granaten-Lineups aus Demos und das Granaten-Training auf dem Server."""

import pandas as pd

from cs2_coach import grenade_lineups as gl
from cs2_coach import training_server as ts

ME, MATE = "1", "2"


class FakeDemo:
    def __init__(self, path):
        pass

    def parse_event(self, name, player=None):
        if name == "weapon_fire":
            return pd.DataFrame({"tick": [100, 500, 900], "user_steamid": [ME, MATE, ME],
                                 "weapon": ["weapon_smokegrenade", "weapon_smokegrenade", "weapon_ak47"],
                                 "user_X": [10.0, 30.0, 0.0], "user_Y": [0.0, 0.0, 0.0], "user_Z": [0.0, 0.0, 0.0],
                                 "user_pitch": [-10.0, -10.0, 0.0], "user_yaw": [90.0, 90.0, 0.0]})
        if name == "smokegrenade_detonate":
            return pd.DataFrame({"tick": [300, 700], "user_steamid": [ME, MATE], "x": [1000.0, 1050.0],
                                 "y": [0.0, 0.0], "z": [0.0, 0.0]})
        if name == "round_end":
            return pd.DataFrame({"tick": [1000], "winner": ["T"]})
        return pd.DataFrame(columns=["tick", "user_steamid", "x", "y", "z"])

    def parse_ticks(self, props, ticks):
        return pd.DataFrame([{"tick": t, "steamid": sid, "X": 10.0 if sid == ME else 30.0, "Y": 0.0, "team_num": 2}
                             for t in ticks for sid in (ME, MATE)])


def test_throws_paired_with_landing_and_grouped():
    throws = gl.scan_demo("x.dem", ME, parser_factory=FakeDemo)
    assert len(throws) == 2 and throws[0]["land"] == [1000.0, 0.0, 0.0] and throws[0]["speed"] == 0
    assert throws[0]["won"] is True and throws[0]["me"] and throws[0]["flight"] == 3.12
    lib = gl.library([{**t, "map": "de_mirage"} for t in throws])
    assert len(lib) == 1 and lib[0]["count"] == 2 and lib[0]["mine"] == 1  # 20 Units / 50 Units auseinander


def test_running_throws_are_not_lineups():
    t = {"type": "he", "side": "T", "pos": [0, 0, 0], "ang": [0, 0], "land": [9, 9, 9], "flight": 1, "map": "m",
         "speed": 200, "me": True, "won": True}
    assert gl.library([t]) == []


def test_grenade_mode_sends_lineups_without_bot(tmp_path, monkeypatch):
    sent = []

    def send(*cmds):
        sent.extend(cmds)
        return ["coach_tp 1.16.0" if c == "coach_tp_version" else ("coach_nade_result 2/40/1 3/300/0"
                if c == "coach_nade_result" else "") for c in cmds]

    lib = [{"type": "smoke", "side": "T", "pos": [1, 2, 3], "ang": [-10, 90], "land": [4, 5, 6], "flight": 6,
            "map": "de_mirage", "count": 9, "mine": 3, "won": 5},
           {"type": "molotov", "side": "CT", "pos": [1, 2, 3], "ang": [0, 0], "land": [4, 5, 6], "flight": 2,
            "map": "de_mirage", "count": 4, "mine": 0, "won": 2}]
    import cs2_coach.grenade_lineups as glm
    monkeypatch.setattr(glm, "collect", lambda *a, **k: [])
    monkeypatch.setattr(glm, "library", lambda throws, limit=10: lib)
    d = lambda: ts.Director({"steam_id": "9"}, tmp_path / "config.yaml", send, timer=lambda *a: None)  # noqa: E731
    assert d().command("9", "dj", "granaten", "mirage") == []
    st = ts.load_state(tmp_path / "config.yaml")
    st.update(phase="team", team="TERRORIST", human_uid=0)
    d()._add_bot(st)
    assert st["phase"] == "running" and not any(c.startswith("bot_quota") for c in sent)
    adds = [c for c in sent if c.startswith("coach_nade_add")]
    assert adds[0].startswith("coach_nade_add weapon_smokegrenade smoke ") and "weapon_incgrenade" in adds[1]
    assert sent[-2] == "coach_nade_start 0"
    ts.save_state(tmp_path / "config.yaml", st)
    d().command("9", "dj", "stop", "")
    sess = ts.load_sessions(tmp_path / "config.yaml")[0]
    assert [x["outcome"] for x in sess["duels"]] == ["win", "loss"] and sess["duels"][1]["nade_dist"] == 300
