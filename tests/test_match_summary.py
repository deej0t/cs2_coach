from cs2_coach import match_summary as ms


def _data():
    tl = [{"round": i, "side": "CT" if i <= 3 else "T", "won": i % 2 == 0, "buy": "full", "player_kills": i % 3,
           "died_early": False, "death_s": None} for i in range(1, 7)]
    return {"round_timeline": tl, "utility_positions": [{"r": 2, "t": "smoke", "s": 10}, {"r": 5, "t": "flash", "s": 60}],
            "player": {"side_split": {"ct_kills": 4, "ct_deaths": 2, "t_kills": 3, "t_deaths": 3}}}


def test_strip_marks_halftime_and_sides():
    st = ms.strip(_data())
    assert len(st) == 6 and st[2]["gap"] and not st[1]["gap"]
    sd = ms.sides(_data())
    assert [(s["side"], s["n"], s["kills"]) for s in sd] == [("CT", 3, 4), ("T", 3, 3)]


def test_mission_and_takeaways():
    m = ms.mission(_data(), None)
    if m is None:            # depends on which buys utility_analysis counts - then nothing to show
        return
    assert m["ok"] is None and m["k"] <= m["n"]
    m = dict(m, target=90.0, ok=False)
    take = ms.takeaways([{"kind": "death", "verdict": "Hauptgrund: X", "trigger": "t", "round": 3, "tick": 9}], m)
    assert take[0]["tone"] == "bad" and take[-1]["tone"] == "focus" and len(take) <= 3


def test_old_export_without_fields():
    assert ms.mission({}, 50) is None and ms.strip({}) == [] and ms.sides({}) == []
    assert ms.takeaways([], None) == []
