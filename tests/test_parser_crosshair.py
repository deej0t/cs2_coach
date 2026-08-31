"""Tests fuer die Crosshair-Placement-Kennzahl.

Die Verteilung der Winkelfehler ist stark rechtsschief: ein Gegner, der
aus einer voellig anderen Richtung auftaucht, erzeugt Fehler von ueber
100 Grad. Das ist keine schlechte Vorausrichtung, sondern eine
Ueberraschung. Ueber sechs echte Demos gemessen urteilte das
arithmetische Mittel in vier Faellen "auffaellig", wo der Median "ok"
sagte - ein einzelner 110-Grad-Kill zog das Mittel von 5.8 auf 13.3 Grad.

Massgeblich ist deshalb der Median.
"""

from __future__ import annotations

import pytest

from cs2_coach.parser import PlayerStats


def stats_with(errors: list[float]) -> PlayerStats:
    s = PlayerStats(name="p", steam_id="1", team="")
    for e in errors:
        s.crosshair_placement_sum += e
        s.crosshair_placement_errors.append(e)
        s.crosshair_placement_kills += 1
    return s


def test_median_ignores_a_single_surprise_kill():
    """Realer Fall: 110 Grad Fehler durch einen Gegner im Ruecken."""
    errors = [4.0, 5.0, 5.5, 6.0, 6.5, 110.0]
    s = stats_with(errors)

    assert s.crosshair_placement_median == pytest.approx(5.75)
    assert s.crosshair_placement_avg > 20, "Mittel wird vom Ausreisser dominiert"


def test_median_and_mean_agree_without_outliers():
    s = stats_with([5.0, 5.0, 5.0, 5.0])
    assert s.crosshair_placement_median == pytest.approx(5.0)
    assert s.crosshair_placement_avg == pytest.approx(5.0)


def test_rating_uses_median_not_mean():
    """Das Urteil darf nicht am Ausreisser haengen."""
    s = stats_with([3.0, 3.5, 4.0, 4.5, 200.0])
    assert s.crosshair_placement_median < 5
    assert s.crosshair_placement_rating == "Exzellent"


def test_rating_still_reacts_to_genuinely_bad_placement():
    s = stats_with([32.0, 35.0, 38.0, 40.0])
    assert s.crosshair_placement_rating == "Schwach"


@pytest.mark.parametrize("median_val,expected", [
    (2.0, "Exzellent"),
    (10.0, "Gut"),
    (20.0, "Durchschnittlich"),
    (35.0, "Schwach"),
])
def test_rating_thresholds(median_val, expected):
    s = stats_with([median_val] * 5)
    assert s.crosshair_placement_rating == expected


def test_no_data_is_neutral():
    s = PlayerStats(name="p", steam_id="1", team="")
    assert s.crosshair_placement_median == 0.0
    assert s.crosshair_placement_rating == "Keine Daten"


def test_even_sample_count_interpolates():
    s = stats_with([4.0, 6.0])
    assert s.crosshair_placement_median == pytest.approx(5.0)
