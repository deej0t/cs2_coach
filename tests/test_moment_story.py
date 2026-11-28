from types import SimpleNamespace

from cs2_coach import moment_story
from cs2_coach.evidence import SOLID_NEGATIVE
from cs2_coach.key_moments import duel_facts


def _scene(**mo):
    base = {"me_idx": 0, "killer_idx": 1, "killer": "2", "death_tick": 1000, "clip": [0, 2], "facts": {}, "ctx": {}}
    base.update(mo)
    return {"players": [{"steamid": "1", "name": "Ich"}, {"steamid": "2", "name": "Bob"}],
            "frames": [{"t": 600}, {"t": 900}, {"t": 1200}], "moment": base}


def test_old_scene_without_fields():
    st = moment_story.build({"moment": {}}, "death")
    assert st["version"] == moment_story.STORY_VERSION
    assert st["flow"] == [] and st["hot"] == [] and st["duel"] is None and st["trigger"]["text"]


def test_storyline_order_and_hot_ticks():
    s = _scene(facts={"my_sight": 960, "his_sight": 930, "offset": 30.0, "my_shot": 980, "his_shot": 940, "seen_by": 3},
               ctx={"untraded": True, "mate_far": True, "mate_m": 25, "mate_name": "Al"})
    s["aim_enemy"] = {"shots": [{"tick": 945, "hit": True, "dmg": 100}]}
    s["nades"] = {"paths": [{"type": "flash", "sid": "2", "pts": [[910, 0, 0, 0]]}],
                  "effects": [{"type": "flash", "t0": 925, "t1": 925, "x": 1, "y": 2, "z": 3, "sid": "2"},
                              {"type": "smoke", "t0": 100, "t1": 2000, "x": 0, "y": 0, "z": 0, "sid": "2"}]}
    st = moment_story.build(s, "death")
    assert st["trigger"]["tick"] == 930 and "früher" in st["trigger"]["text"]   # earliest negative factor
    ticks = [f["tick"] for f in st["flow"]]
    assert ticks == sorted(ticks) and len(ticks) <= moment_story.MAX_FLOW
    assert any("3 Gegner" in f["text"] for f in st["flow"])
    hot = st["hot"]
    assert [h["tick"] for h in hot] == sorted(h["tick"] for h in hot)
    assert all(600 <= h["tick"] <= 1200 for h in hot)               # smoke from tick 100 is outside the clip
    assert {"contact", "death", "nade_pop", "nade_throw"} <= {h["type"] for h in hot}
    assert st["duel"] == [930, 1000] and "Kein Trade" not in st["trigger"]["text"]


def test_seen_by_is_defensive():
    rows = [{"tick": t, "steamid": sid, "X": 0, "Y": 0, "yaw": 0, "spotted_by": sp}
            for t in (1, 2) for sid, sp in (("1", None), ("2", None))]
    assert duel_facts(rows, [], "1", "2", 3)["seen_by"] is None
    rows[2]["spotted_by"] = [2, 7, 0]
    assert duel_facts(rows, [], "1", "2", 3)["seen_by"] == 2
    assert moment_story._seen_by({}) is None


def test_impact_text():
    out = moment_story.impact_text({"before": 62, "cost": 21, "own": 4, "enemy": 4}, "death")
    assert out["delta"] == -21 and "62 % → 41 %" in out["text"] and "3 gegen 4" in out["text"]
    assert moment_story.impact_text({"before": 40, "gain": 15, "own": 3, "enemy": 3}, "kill")["after"] == 55


def test_lobby_hidden_without_solid_verdict(monkeypatch):
    import cs2_coach.duel_value as dv
    d = {"side": "CT", "map": "de_mirage", "place": "Palace"}

    def zone(verdict):
        v = SimpleNamespace(verdict=verdict, effect=-0.4, ci_low=-0.6, ci_high=-0.2)
        return [SimpleNamespace(key=("de_mirage", "CT", "Palace"), value=v, n=30, mean=-3.0, lobby_mean=1.0)]
    monkeypatch.setattr(dv, "by_zone", lambda priced, me: zone("unclear"))
    assert moment_story.lobby_line([{}], "1", d, "death") is None
    monkeypatch.setattr(dv, "by_zone", lambda priced, me: zone(SOLID_NEGATIVE))
    out = moment_story.lobby_line([{}], "1", d, "death")
    assert out["tone"] == "bad" and out["ci"] == [-0.6, -0.2] and "schlechter" in out["text"]
    assert moment_story.lobby_line([], "1", d, "death") is None
