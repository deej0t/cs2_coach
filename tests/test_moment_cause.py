from cs2_coach.moment_cause import summarize


def _scene(**mo):
    base = {"me_idx": 0, "killer_idx": 1, "killer": "2", "death_tick": 1000, "facts": {}, "ctx": {}}
    base.update(mo)
    return {"players": [{"steamid": "1", "name": "Ich"}, {"steamid": "2", "name": "Bob"}], "moment": base}


def test_old_scene_without_fields_is_harmless():
    out = summarize({"moment": {}}, "death")
    assert out["causes"] == [] and out["turns"] == []
    assert out["verdict"]


def test_late_sight_is_main_cause_of_death():
    s = _scene(facts={"my_sight": 960, "his_sight": 930, "offset": 30.0, "my_shot": None, "his_shot": 940},
               ctx={"untraded": True, "mate_far": True, "mate_m": 25, "mate_name": "Al"})
    s["aim_enemy"] = {"shots": [{"tick": 945, "hit": True, "dmg": 100}]}
    out = summarize(s, "death")
    keys = [c["key"] for c in out["causes"]]
    assert keys[0] == "info" and "469 ms" in out["causes"][0]["label"]
    assert out["verdict"].startswith("Hauptgrund")
    assert "placement" in keys and len(out["causes"]) <= 4
    kinds = [t["kind"] for t in out["turns"]]
    assert "death" in kinds and "hit_taken" in kinds and "sight_his" in kinds
    assert [t["tick"] for t in out["turns"]] == sorted(t["tick"] for t in out["turns"])


def test_kill_lists_good_causes_first():
    s = _scene(kind="kill", facts={"my_sight": 900, "his_sight": 940, "offset": 2.0, "my_shot": 905, "his_shot": None})
    s["aim"] = {"shots": [{"tick": 905, "hit": True, "dmg": 120}], "mistakes": []}
    out = summarize(s, "kill")
    assert out["tone"] == "good" and out["causes"][0]["tone"] == "good"
    assert any(t["kind"] == "kill" for t in out["turns"])


def test_blind_and_nades_become_turns():
    s = _scene()
    s["hud"] = {"blinds": [[0, 950, 1100]]}
    s["nades"] = {"paths": [{"type": "flash", "sid": "2", "pts": [[900, 0, 0, 0]]},
                            {"type": "smoke", "sid": "9", "pts": [[910, 0, 0, 0]]}]}
    out = summarize(s, "death")
    assert out["causes"][0]["key"] == "blind"
    labels = [t["label"] for t in out["turns"]]
    assert "Bobs Flash" in labels and not any("Smoke" in x for x in labels)
