"""Duelle in Prozentpunkten Rundengewinn: du gegen die Lobby an derselben Stelle."""

from cs2_coach import duel_value as dv
from cs2_coach import winprob as wp

ME, MATE, E1, E2 = "me", "mate", "e1", "e2"


def _death(sid, by, side, place, by_place, cost, own=5, enemy=5, planted=False, demo="d1", m="Mirage"):
    return {"sid": sid, "by": by, "side": side, "place": place, "by_place": by_place if by else "",
            "cost": cost, "own": own, "enemy": enemy, "planted": planted, "demo": demo, "map": m}


def test_each_duel_counts_for_both_players_and_teamkills_for_nobody():
    ds = [_death(ME, E1, "CT", "Connector", "Palace", 12.0), _death(E2, "", "T", "Ramp", "", 5.0)]
    got = [(sid, side, place, value, won) for sid, _, side, place, value, won, _ in dv.duels(ds)]
    assert got == [(ME, "CT", "Connector", -12.0, False), (E1, "T", "Palace", 12.0, True)]


def test_zone_compares_you_with_the_lobby_at_the_same_callout():
    ds = []
    for i in range(40):   # du verlierst in Connector teure Duelle, die Lobby gewinnt sie dort
        ds.append(_death(ME, E1, "CT", "Connector", "Palace", 20.0 + i % 3))
        ds.append(_death(E2, MATE, "T", "Palace", "Connector", 18.0 + i % 4))
        ds.append(_death(E1, MATE, "T", "Palace", "Connector", 15.0 + i % 5))
    ds += [_death(E1, ME, "T", "Palace", "Connector", 10.0)]   # ein gewonnenes Duell von dir
    zones = dv.by_zone(ds, ME)
    connector = next(z for z in zones if z.key == ("Mirage", "CT", "Connector"))
    assert (connector.n, connector.won, len(connector.lobby), connector.lobby_won) == (41, 1, 80, 80)
    assert connector.weaker and connector.mean < 0 < connector.lobby_mean
    assert connector.value.verdict == "solid_negative" and connector.rate.verdict == "solid_negative"
    assert zones[0] is connector                # belastbar schwaechste Zone zuerst
    assert all(z.n >= dv.MIN_DUELS for z in zones)


def test_situation_is_seen_from_each_players_side():
    ds = [_death(ME, E1, "CT", "A", "B", 10.0, own=2, enemy=4) for _ in range(20)]
    ds += [_death(MATE, E2, "CT", "A", "B", 10.0, own=2, enemy=4) for _ in range(20)]
    tested = {g.key: g for g in dv.by_situation(ds, ME)}
    assert list(tested) == [("CT", "Unterzahl", False)]     # nur dort hast du und die Lobby Duelle
    assert (tested[("CT", "Unterzahl", False)].n, len(tested[("CT", "Unterzahl", False)].lobby)) == (20, 20)
    # dein Gegner gewinnt dasselbe Duell im 4v2: fuer ihn Ueberzahl
    every = {g.key: g for g in dv.by_situation(ds, ME, min_duels=0)}
    assert len(every[("T", "Ueberzahl", False)].lobby) == 40 and every[("T", "Ueberzahl", False)].value is None


def test_per_match_values_you_against_the_mean_of_the_others():
    rnd = {"ct": [ME, MATE], "t": [E1, E2]}
    matches = [{"demo": "d1", "rounds": [rnd] * 10}]
    ds = [_death(E1, ME, "T", "A", "B", 30.0), _death(ME, E2, "CT", "A", "B", 10.0),
          _death(MATE, E1, "CT", "A", "B", 20.0)]
    pm = dv.per_match(ds, matches, ME)
    assert (pm["gain_me"], pm["cost_me"], pm["net_me"]) == ([3.0], [1.0], [2.0])
    # Lobby je Runde: mate 0 Gewinn / 2 Kosten, e1 2 / 3, e2 1 / 0 -> Mittel der drei
    assert round(pm["gain_lobby"][0], 3) == 1.0
    assert round(pm["cost_lobby"][0], 3) == round(5 / 3, 3)
    assert round(pm["net_lobby"][0], 3) == round(-2 / 3, 3)


def test_priced_deaths_use_a_table_without_the_own_match():
    ct = [f"7656119800000000{i}" for i in range(5)]
    t = [f"7656119800000001{i}" for i in range(5)]

    def rnd(i):
        dies = ct[0] if i % 2 else t[0]
        return {"n": i, "map": "Nuke", "winner": "CT" if i % 3 else "T", "seconds": 60.0, "ct": ct, "t": t,
                "ct_equip": 20000, "t_equip": 20000, "events": [[10.0, "d", dies, (t if i % 2 else ct)[1], "X", "Y"]]}

    matches = [{"demo": f"m{k}", "rounds": [rnd(k * 10 + i) for i in range(10)]} for k in range(4)]
    ds = dv.priced_deaths(matches, folds=4)
    assert len(ds) == 40 and {d["map"] for d in ds} == {"Nuke"} and ds[0]["demo"] == "m0"
    assert all(d["opening"] for d in ds)
    seen = wp.observe(matches)
    without_m0 = wp.WinTable([r for d in ("m1", "m2", "m3") for r in seen[d]])
    expected = [s["cost"] for r in matches[0]["rounds"] for s in wp.swings(r, without_m0)]
    assert [d["cost"] for d in ds if d["demo"] == "m0"] == expected


def test_habits_count_rounds_per_side_and_skip_the_last_man_for_alone():
    rnd = {"ct": [ME, MATE], "t": [E1, E2]}
    matches = [{"demo": "d1", "rounds": [rnd] * 100}]

    def death(sid, side, **kw):
        base = {**_death(sid, E1 if side == "CT" else ME, side, "A", "B", 10.0), "second": 40.0,
                "opening": False, "mate_dist": 300, "held": 0, "thrown": 1}
        return {**base, **kw}

    ds = [death(ME, "CT", opening=True) for _ in range(40)]            # du stirbst oft als Erster
    ds += [death(MATE, "CT", opening=True) for _ in range(5)]
    ds += [death(E1, "T", opening=True) for _ in range(5)]
    ds += [death(ME, "CT", own=1, mate_dist=None) for _ in range(30)]  # letzter Mann: nicht "allein"
    ds += [death(MATE, "CT", mate_dist=2000) for _ in range(30)]
    habits = {h.key: h for h in dv.by_habit(ds, matches, ME)}
    opening = habits["CT_opening"]
    assert (opening.k, opening.n, opening.lobby_k, opening.lobby_n) == (40, 100, 5, 100)   # Lobby CT = mate
    assert opening.rate.verdict == "solid_negative" and opening.rate.effect < 0     # oefter = schlechter
    assert opening.cost == 4.0 and opening.lobby_cost == 0.5
    alone = habits["CT_alone"]
    assert (alone.k, alone.lobby_k) == (0, 30) and alone.rate.effect > 0
    assert habits["T_opening"].n == 0 and habits["T_opening"].rate is None          # du warst nie T
    assert next(iter(dv.by_habit(ds, matches, ME))).key == "CT_opening"             # belastbar schwach zuerst
