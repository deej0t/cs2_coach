"""Tests fuer evidence.py und die Reihenfolge der Matches.

Der Anlass: Tilt-, Session-, Pistol- und Kalenderseite gaben Ratschlaege
auf Grundlage einer Differenz zweier Mittelwerte mit fester Schwelle. Bei
der Umstellung kamen zwei Fehler in den Daten selbst zum Vorschein:

- Die Exporte wurden nach Dateinamen sortiert (<Datum>_<Map>_<Score>_<Zeit>).
  Innerhalb eines Tages ergab das Map- statt Spielreihenfolge; 35 von 62
  aufeinanderfolgenden Paaren waren falsch. Der angezeigte "leichte
  Tilt-Effekt" (Delta 0.06) entstand allein daraus - in richtiger
  Reihenfolge waren es -0.01.
- _build_sessions() bekam die Matches neueste zuerst. "Erste Haelfte der
  Session" war die spaetere, Tilt und Warmup-Effekt waren vertauscht.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cs2_coach import evidence as E
from cs2_coach.obsidian import export_sort_key


# ── Mittelwertvergleich ──────────────────────────────────────────────

def test_zu_kleine_gruppe_ergibt_keine_aussage():
    assert E.compare_groups([1.0] * 7, [0.5] * 20) is None


def test_deutlicher_unterschied_ist_belastbar():
    a = [1.0, 1.1, 0.9, 1.05, 0.95, 1.0, 1.1, 0.9, 1.0, 1.05] * 2
    b = [0.6, 0.7, 0.5, 0.65, 0.55, 0.6, 0.7, 0.5, 0.6, 0.65] * 2
    c = E.compare_groups(a, b)
    assert c.a_better and not c.b_better
    assert c.n_needed == 0


def test_kleiner_unterschied_bei_wenig_daten_bleibt_offen():
    """Genau der Fall der Tilt-Seite: Differenz sichtbar, Urteil offen."""
    a = [0.85, 0.7, 1.0, 0.9, 0.6, 1.1, 0.8, 0.9, 0.75, 0.95, 0.8]
    b = [0.8, 0.9, 0.6, 1.0, 0.7, 0.85, 0.75, 0.9, 0.65, 0.95, 0.8, 0.7, 0.85, 0.9, 0.75, 0.8]
    c = E.compare_groups(a, b)
    assert c.undecided
    assert "noch nicht belastbar" in c.note()


def test_fehlender_unterschied_ist_nicht_automatisch_stabilitaet():
    """'Kein Unterschied gemessen' ist nicht 'kein Unterschied belegt'."""
    a = [0.8, 1.0, 0.6, 0.9, 0.7, 1.1, 0.5, 0.9]
    b = [0.8, 1.0, 0.6, 0.9, 0.7, 1.1, 0.5, 0.9]
    c = E.compare_groups(a, b)
    assert c.effect == 0.0
    assert not c.no_difference          # 8 gegen 8 traegt keine Stabilitaet
    big = E.compare_groups(a * 40, b * 40)
    assert big.no_difference            # 320 gegen 320 schon


def test_niedriger_ist_besser_dreht_das_vorzeichen():
    a, b = [1.0] * 10 + [2.0] * 10, [3.0] * 10 + [4.0] * 10
    assert E.compare_groups(a, b, higher_is_better=False).a_better


def test_mehrere_kandidaten_verbreitern_den_bereich():
    """Den besten von sieben Wochentagen herauszusuchen, ist kein Befund."""
    a = [1.0, 0.9, 1.1, 0.8, 1.2, 0.95, 1.05, 0.85, 1.0, 0.9]
    b = [0.8, 0.7, 0.9, 0.6, 1.0, 0.75, 0.85, 0.65, 0.8, 0.7] * 3
    one = E.compare_groups(a, b)
    seven = E.compare_groups(a, b, comparisons=7)
    assert seven.ci_high - seven.ci_low > one.ci_high - one.ci_low
    assert seven.effect == one.effect


# ── Quotenvergleich ──────────────────────────────────────────────────

def test_quoten_bei_rund_60_runden_noch_offen():
    """Zahlen der echten Pistol-Seite: CT 32/66, T 36/60."""
    c = E.compare_rates(32, 66, 36, 60)
    assert (c.mean_a, c.mean_b) == (48.5, 60.0)
    assert c.undecided


def test_klare_quoten_sind_belastbar():
    assert E.compare_rates(80, 100, 30, 100).a_better
    assert E.compare_rates(30, 100, 80, 100).b_better


def test_quoten_unter_mindestgroesse():
    assert E.compare_rates(5, 7, 1, 7) is None


# ── Reihenfolge der Exporte ──────────────────────────────────────────

def test_sortierung_nach_spielzeit_statt_map_und_score():
    """Echte Dateien vom 09.07.2026, nach Namen sortiert 22:07 vor 20:51."""
    names = [
        "2026-07-09_cache_13-4_2207_coach.json",
        "2026-07-09_cache_13-5_2136_coach.json",
        "2026-07-09_cache_13-6_2051_coach.json",
        "2026-07-09_cache_8-13_2241_coach.json",
        "2026-07-08_mirage_13-5_2000_coach.json",
    ]
    ordered = sorted((Path(n) for n in names), key=export_sort_key)
    assert [p.name[-15:-11] for p in ordered] == ["2000", "2051", "2136", "2207", "2241"]


def test_unterstrich_im_mapnamen_verschiebt_nichts():
    a = Path("2026-07-09_de_dust2_13-4_2207_coach.json")
    b = Path("2026-07-09_ancient_13-4_2100_coach.json")
    assert sorted([a, b], key=export_sort_key) == [b, a]


def test_session_wird_in_spielreihenfolge_ausgewertet():
    """Rating faellt im Verlauf: das muss Tilt heissen, nicht Warmup."""
    from cs2_coach.web.app import _build_sessions

    def ex(time, rating):
        return {"date": "2026-07-09", "datetime": f"2026-07-09 {time}",
                "result": "Niederlage", "rating": rating, "kd": 1.0, "adr": 80}

    # So liefert _get_exports(): neueste zuerst.
    exports = [ex("22:41", 0.5), ex("22:07", 0.6), ex("21:36", 1.3), ex("20:51", 1.4)]
    session = _build_sessions(exports)[0]
    assert session["tilt"]["type"] == "tilt"
