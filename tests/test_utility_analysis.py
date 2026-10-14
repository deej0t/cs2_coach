"""Tests fuer die Utility-Relevanz je Granatentyp."""

from __future__ import annotations

from cs2_coach.findings import SOLID_POSITIVE, UNDECIDED
from cs2_coach.utility_analysis import _rates, throw_relevance


def export(result="Sieg", rounds=20, throws=(), with_positions=True):
    d = {"match": {"result": result, "total_rounds": rounds}}
    if with_positions:
        d["utility_positions"] = [{"t": t, "x": 0, "y": 0, "r": 1}
                                  for t in throws]
    return d


# ── Normierung ───────────────────────────────────────────────────────

def test_rate_ist_je_runde_nicht_je_match():
    """Der ganze Zweck: ein langes Match darf nicht fleissiger wirken."""
    kurz = _rates(export(rounds=10, throws="ssff"))
    lang = _rates(export(rounds=20, throws="ssssffff"))
    assert kurz["total"] == lang["total"] == 0.4
    assert kurz["s"] == lang["s"] == 0.2


def test_alte_exporte_zaehlen_nicht_als_null():
    """Fehlendes Feld ist unbekannt, nicht 'keine Wuerfe geworfen'."""
    assert _rates(export(with_positions=False)) is None


def test_ohne_rundenzahl_keine_quote():
    assert _rates(export(rounds=0, throws="ss")) is None


def test_unbekannte_kuerzel_werden_ignoriert():
    r = _rates(export(rounds=10, throws="ssx?"))
    assert r["s"] == 0.2
    assert r["total"] == 0.2


# ── Auswertung ───────────────────────────────────────────────────────

def test_unentschiedene_matches_fliegen_raus():
    ex = [export(result="Unentschieden", throws="s") for _ in range(20)]
    out = throw_relevance(ex)
    assert out["matches_used"] == 0
    assert out["rows"] == []


def test_uebersprungene_exporte_werden_gezaehlt():
    ex = ([export(result="Sieg", with_positions=False)] * 5
          + [export(result="Niederlage", with_positions=False)] * 5)
    out = throw_relevance(ex)
    assert out["matches_skipped"] == 10
    assert out["matches_used"] == 0


def test_zu_wenige_matches_ergeben_kein_urteil():
    """Unter MIN_MATCHES_PER_OUTCOME wird gar nichts behauptet."""
    ex = ([export(result="Sieg", throws="ss")] * 3
          + [export(result="Niederlage", throws="s")] * 3)
    assert throw_relevance(ex)["rows"] == []


def test_klarer_unterschied_wird_als_belastbar_erkannt():
    ex = ([export(result="Sieg", rounds=20, throws="h" * 10)] * 15
          + [export(result="Niederlage", rounds=20, throws="h")] * 15)
    rows = {r.key: r for r in throw_relevance(ex)["rows"]}
    he = rows["util_h"]
    assert he.label == "HE"
    assert he.mean_win > he.mean_loss
    assert he.effect > 0
    assert he.verdict == SOLID_POSITIVE


def test_kein_unterschied_bleibt_ohne_befund():
    ex = ([export(result="Sieg", rounds=20, throws="ssff")] * 15
          + [export(result="Niederlage", rounds=20, throws="ssff")] * 15)
    for r in throw_relevance(ex)["rows"]:
        assert r.effect == 0.0
        assert r.verdict != SOLID_POSITIVE


def test_zeilen_nach_effektstaerke_sortiert():
    ex = ([export(result="Sieg", rounds=20, throws="h" * 8 + "s")] * 15
          + [export(result="Niederlage", rounds=20, throws="s")] * 15)
    effects = [abs(r.effect) for r in throw_relevance(ex)["rows"]]
    assert effects == sorted(effects, reverse=True)


def test_matches_needed_nur_bei_unentschieden():
    ex = ([export(result="Sieg", rounds=20, throws="hh")] * 15
          + [export(result="Niederlage", rounds=20, throws="h")] * 15)
    for r in throw_relevance(ex)["rows"]:
        if r.verdict == UNDECIDED:
            assert r.matches_needed >= 0
        else:
            assert r.matches_needed == 0


# ── HE-Wirkung (Schema 4) ────────────────────────────────────────────

def he_export(result="Sieg", rounds=20, he_damage=100, fire=0, he_seconds=(10.0, 20.0)):
    ups = [{"t": "h", "x": 0, "y": 0, "r": 1, "tk": 1, "s": s} for s in he_seconds]
    return {"match": {"result": result, "total_rounds": rounds},
            "player": {"utility": {"he_damage": he_damage, "fire_damage": fire}},
            "utility_positions": ups}


def test_alte_exporte_ohne_schaden_werden_uebersprungen():
    """Kein Schadensfeld heisst unbekannt, nicht 'jede HE ging daneben'."""
    from cs2_coach.utility_analysis import he_effectiveness
    old = {"match": {"result": "Sieg", "total_rounds": 20},
           "player": {"utility": {}}, "utility_positions": [{"t": "h", "r": 1}]}
    out = he_effectiveness([old] * 10)
    assert out["matches_skipped"] == 10 and out["rows"] == []


def test_schaden_pro_he_trennt_treffen_von_menge():
    from cs2_coach.utility_analysis import _he_values
    v = _he_values(he_export(he_damage=90, he_seconds=(5.0, 12.0, 30.0)))
    assert v["dmg_per_he"] == 30
    assert v["he_dmg_round"] == 4.5
    assert v["he_second"] == 12.0          # Median der Wurfzeitpunkte


def test_ohne_he_kein_schaden_pro_he():
    from cs2_coach.utility_analysis import _he_values
    v2 = he_export(he_damage=0, he_seconds=())
    v2["utility_positions"] = [{"t": "s", "s": 3.0}]
    v = _he_values(v2)
    assert "dmg_per_he" not in v and "he_second" not in v
    assert v["he_dmg_round"] == 0


def test_treffsichere_he_in_siegen_ist_belastbar():
    from cs2_coach.findings import SOLID_POSITIVE
    from cs2_coach.utility_analysis import he_effectiveness
    ex = ([he_export("Sieg", he_damage=d) for d in (120, 140, 130, 150, 125, 135, 145, 128, 138, 132)]
          + [he_export("Niederlage", he_damage=d) for d in (20, 30, 25, 35, 15, 28, 22, 32, 18, 26)])
    rows = {r.key: r for r in he_effectiveness(ex)["rows"]}
    assert rows["he_dmg_per_he"].verdict == SOLID_POSITIVE
