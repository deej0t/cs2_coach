from cs2_coach import aim_profile as ap

ME, EN = "76561190000000001", "76561190000000002"


def _rows(yaws, seen_from=0):
    out = []
    for t, y in enumerate(yaws):
        out.append({"tick": t, "steamid": ME, "X": 0.0, "Y": 0.0, "Z": 0.0, "yaw": y, "pitch": 0.0,
                    "ducking": False, "spotted_by": set()})
        out.append({"tick": t, "steamid": EN, "X": 1000.0, "Y": 0.0, "Z": 0.0, "yaw": 180.0, "pitch": 0.0,
                    "ducking": False, "spotted_by": {ME} if t >= seen_from else set()})
    return out


def test_measure_from_sight_on():
    rows = _rows([40, 40, 40, 20, 10, 2, 0, 0, 0, 0], seen_from=2)
    m = ap.measure(rows, [{"tick": 7, "steamid": ME, "weapon": "ak47"}], [], ME, EN, 9)
    assert m["sight"] == 2 and round(m["placement"]) == 40
    assert m["ttt"] == 3 * 1000 / 64 and not m["never"]
    assert m["shot_ms"] == 5 * 1000 / 64 and m["first_err"] < 1 and m["weapon"] == "ak47"


def test_measure_without_sight_is_none():
    assert ap.measure(_rows([0] * 5, seen_from=99), [], [], ME, EN, 4) is None


def test_weapon_group():
    assert ap.weapon_group("awp") == "sniper" and ap.weapon_group("glock") == "pistol"
    assert ap.weapon_group("ak47") == "rifle" and ap.weapon_group("knife_t") == "other"


def _duel(sid, won, placement, moving):
    return {"sid": sid, "won": won, "placement": placement, "ttt": placement * 10, "shot_ms": 100.0,
            "first_err": placement / 2, "never": False, "moving": moving, "overshoot": False,
            "group": "rifle", "demo": "d", "tick": 1, "round": 1}


def test_profile_flags_worse_than_lobby():
    duels = [_duel(ME, i % 2 == 0, 20.0 + i % 3, True) for i in range(60)]
    duels += [_duel(f"7656119999999{i:04d}", i % 2 == 0, 3.0 + i % 3, False) for i in range(200)]
    rows = {r.key: r for r in ap.profile(duels, ME, "rifle")}
    assert rows["placement"].vs_lobby["verdict"] == "solid_negative"
    assert rows["moving"].vs_lobby["verdict"] == "solid_negative" and rows["moving"].me == 100.0
    assert rows["shot_ms"].vs_lobby["verdict"] != "solid_negative"
    ex = ap.examples(duels, ME, "placement")
    assert len(ex) == 6 and ex[0]["placement"] == 22.0
