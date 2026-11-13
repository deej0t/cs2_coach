"""Tests fuer zones: Duelle je Callout ueber alle Matches."""

from __future__ import annotations

from cs2_coach import zones


def match(events, side="CT", map_="Mirage"):
    """events: Liste (t, callout) - alle in Runde 1 auf *side*."""
    return {"match": {"map": map_},
            "round_timeline": [{"round": 1, "side": side}],
            "kill_positions": [{"t": t, "p": p, "r": 1} for t, p in events]}


def test_zaehlt_je_map_seite_und_callout():
    c = zones.count([match([("k", "Connector"), ("d", "Connector")]),
                     match([("k", "Connector")], side="T")])
    assert c[("Mirage", "CT", "Connector")] == [1, 2]
    assert c[("Mirage", "T", "Connector")] == [1, 1]


def test_alte_exporte_ohne_callout_fallen_heraus():
    old = {"match": {"map": "Mirage"}, "round_timeline": [{"round": 1, "side": "CT"}],
           "kill_positions": [{"t": "k", "r": 1}]}
    assert zones.count([old]) == {}


def test_schwache_zone_wird_belastbar_erkannt():
    weak = [("d", "Connector")] * 90 + [("k", "Connector")] * 30      # 25 %
    rest = [("k", "Jungle")] * 120 + [("d", "Jungle")] * 80           # 60 %
    z = zones.analyse([match(weak + rest)])
    assert z[0].place == "Connector" and z[0].rate == 25.0
    assert z[0].solid and z[0].cmp.verdict == "solid_negative"


def test_auswahl_aus_vielen_zonen_wird_korrigiert():
    """Mehr gepruefte Zonen = strengeres Intervall (Bonferroni)."""
    base = [("d", "Connector")] * 12 + [("k", "Connector")] * 8 + [("k", "Jungle")] * 20 + [("d", "Jungle")] * 20
    few = zones.analyse([match(base)])
    extra = [(t, f"Zone{i}") for i in range(6) for t in ("k",) * 10 + ("d",) * 10]
    many = zones.analyse([match(base + extra)])
    w1 = next(z for z in few if z.place == "Connector").cmp
    w2 = next(z for z in many if z.place == "Connector").cmp
    assert (w2.ci_high - w2.ci_low) > (w1.ci_high - w1.ci_low)


def test_zu_wenige_duelle_werden_nicht_verglichen():
    z = zones.analyse([match([("d", "Connector")] * 5 + [("k", "Jungle")] * 30)])
    assert [x.place for x in z] == ["Jungle"]
