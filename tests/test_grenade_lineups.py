"""Granaten-Lineups aus Demos und das Granaten-Training auf dem Server."""

import pandas as pd

from cs2_coach import grenade_lineups as gl
from cs2_coach import training_server as ts

ME, MATE = "1", "2"


class FakeDemo:
    """Zwei Smokes aus dem Stand: ME bei Tick 100 von x=10, MATE bei Tick 500 von x=30 (gleiches Lineup),
    beide mit 675 u/s (Linksklick), Landung bei x=1000/1050. Dazu ein Laufwurf von ME bei Tick 800."""

    def __init__(self, path):
        pass

    def parse_grenades(self):
        rows = []
        for eid, sid, t0, x0, v in [(1, ME, 100, 10.0, 675.0), (2, MATE, 500, 30.0, 675.0), (3, ME, 800, 0.0, 900.0)]:
            for k in range(6):
                rows.append({"grenade_type": "CSmokeGrenadeProjectile", "grenade_entity_id": eid, "steamid": sid,
                             "tick": t0 + k, "x": x0 + v * k / 64, "y": 0.0, "z": 64.0})
        return pd.DataFrame(rows)

    def parse_event(self, name, player=None):
        if name == "smokegrenade_detonate":
            return pd.DataFrame({"tick": [300, 700, 950], "user_steamid": [ME, MATE, ME], "x": [1000.0, 1050.0, 500.0],
                                 "y": [0.0, 0.0, 0.0], "z": [0.0, 0.0, 0.0]})
        if name == "round_end":
            return pd.DataFrame({"tick": [1000], "winner": ["T"]})
        return pd.DataFrame(columns=["tick", "user_steamid", "x", "y", "z"])

    def parse_ticks(self, props, ticks):
        rows = []
        for t in ticks:
            for sid, x in ((ME, 10.0), (MATE, 30.0)):
                run = sid == ME and t >= 790  # ME laeuft beim dritten Wurf (250 u/s)
                rows.append({"tick": t, "steamid": sid, "X": x + (t * 250 / 64 if run else 0), "Y": 0.0, "Z": 0.0,
                             "pitch": -10.0, "yaw": 90.0, "team_num": 2, "is_airborne": False, "ducking": False})
        return pd.DataFrame(rows)


def test_throws_from_trajectory_with_style():
    throws = gl.scan_demo("x.dem", ME, parser_factory=FakeDemo)
    assert len(throws) == 3
    a = throws[0]
    assert a["land"] == [1000.0, 0.0, 0.0] and a["style"]["click"] == "left" and not a["style"]["run"]
    assert a["won"] is True and a["me"] and a["path"][-1] == [1000, 0, 0]
    assert throws[2]["style"]["run"]  # 250 u/s beim Loslassen
    lib = gl.library([{**t, "map": "de_mirage"} for t in throws])
    assert len(lib) == 1 and lib[0]["count"] == 2 and lib[0]["mine"] == 1  # Laufwurf faellt raus


def test_style_texts():
    assert gl.style_text({"click": "left", "jump": True}) == "Sprungwurf + Linksklick"
    assert "[LEERTASTE]" in gl.nade_keys({"click": "left", "jump": True})
    assert gl.nade_keys({"click": "both"}) == "[LINKS+RECHTS] loslassen"


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
