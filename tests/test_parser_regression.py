"""Regressionstest der Metriken gegen echte Spieldaten.

Die uebrigen Parser-Tests bauen synthetische DataFrames. Sie halten die
gefundenen Korrekturen fest, haetten die Fehler aber nicht finden koennen -
sie bringen genau die Annahme mit, die falsch war. Gefunden wurde jeder der
fuenf Fehler durch Messen an echten Demos.

Dieser Test schliesst die Luecke: er laesst die vollstaendige Kette gegen
eine aufgezeichnete echte Demo laufen (siehe replay_parser.py) und nagelt
die Kennzahlen fest. Die Werte stammen aus dem Stand vom 16.09.2026, der
gegen alle 63 Exporte als metrics_version 1 bestaetigt wurde.

Aendert sich hier ein Wert, ist das entweder ein Fehler - oder eine
gewollte Korrektur, die METRICS_VERSION hochzaehlen muss.
"""

from __future__ import annotations

import pytest

from tests.replay_parser import parse_recorded_demo


@pytest.fixture(scope="module")
def result():
    return parse_recorded_demo()


@pytest.fixture(scope="module")
def player(result):
    return result.player_stats


# ── Die Aufzeichnung selbst ──────────────────────────────────────────

def test_fixture_ist_das_erwartete_match(result):
    """Schuetzt davor, dass ein Fixture-Tausch unbemerkt alles verschiebt."""
    assert result.map_name == "Inferno"
    assert (result.score_team1, result.score_team2) == (5, 10)
    assert result.total_rounds == 15
    assert result.match_date == "2026-08-17"
    assert result.player_stats.name == "deej0t"


# ── Der Gesamtstand ──────────────────────────────────────────────────

def test_kennzahlen_unveraendert(player, result):
    """Gesamtabgleich. Faellt bei jeder Bedeutungsaenderung auf."""
    assert (player.kills, player.deaths, player.assists) == (10, 10, 2)
    assert player.damage == 1041
    assert player.rounds_played == 15
    assert player.adr == pytest.approx(69.4, abs=0.05)
    assert player.accuracy == pytest.approx(20.0, abs=0.05)
    assert player.kast_pct == pytest.approx(66.667, abs=0.05)
    assert player.headshot_pct == pytest.approx(40.0, abs=0.05)
    assert player.survival_rate == pytest.approx(33.333, abs=0.05)
    assert result.rating == pytest.approx(0.95, abs=0.005)


# ── Die einzelnen Korrekturen, je mit ihrem Fehlerbild ───────────────

def test_adr_bleibt_gedeckelt(player):
    """Ungedeckelt lag der ADR 18 bis 47 Prozent hoeher.

    dmg_health meldet den rohen Waffenschaden: ein toedlicher Treffer auf
    ein Opfer mit 20 HP taucht als 108 auf. Faellt die Deckelung in
    _process_damage() weg, steigt die Schadenssumme deutlich.
    """
    assert player.damage == 1041
    # Obergrenze aus der Spielmechanik: mehr als 100 HP je Gegner und
    # Runde sind nicht entfernbar, bei maximal 5 Gegnern.
    assert player.damage <= 100 * 5 * player.rounds_played


def test_counter_strafing_zaehlt_alle_schuesse(player):
    """Vor der Korrektur wurde nur ueber Treffer gemessen.

    Die Quote kam aus bullet_damage.inaccuracy_move, das nur bei Treffern
    feuert - 81 Prozent der Schuesse fehlten. Die Summe aus stehenden und
    laufenden Schuessen muss daher allen Schuessen entsprechen (175), nicht
    nur den Treffern (35).
    """
    assert player.shots_standing + player.shots_running == player.shots_fired
    assert player.shots_fired == 175
    assert player.total_shots_hit == 35
    assert player.counter_strafe_score == pytest.approx(70.286, abs=0.05)


def test_accuracy_ohne_granaten_und_messer(player):
    """Granaten und Messer koennen keinen bullet_damage erzeugen.

    Lagen sie im Nenner, fiel die Accuracy rund zwei Punkte zu schlecht
    aus. Der Spieler wirft hier 10 Granaten (0.667 je Runde), die nicht
    unter shots_fired auftauchen duerfen.
    """
    assert player.accuracy == pytest.approx(
        player.total_shots_hit / player.shots_fired * 100, abs=0.01)
    assert player.utility_per_round == pytest.approx(0.667, abs=0.005)


def test_crosshair_median_statt_mittel(player):
    """Die Verteilung ist stark rechtsschief - hier um das Dreifache.

    Genau deshalb wird der Median bewertet: das Mittel meldet 6.5 Grad
    ("auffaellig"), der Median 2.1 ("ok"). Beide bleiben erhalten, die
    Bewertung haengt am Median.
    """
    assert player.crosshair_placement_median == pytest.approx(2.14, abs=0.01)
    assert player.crosshair_placement_avg == pytest.approx(6.46, abs=0.01)
    assert player.crosshair_placement_median < player.crosshair_placement_avg
    assert player.crosshair_placement_kills == 10
    buckets = (player.crosshair_placement_excellent,
               player.crosshair_placement_good,
               player.crosshair_placement_average,
               player.crosshair_placement_poor)
    assert buckets == (7, 1, 2, 0)
    assert sum(buckets) == player.crosshair_placement_kills


def test_spray_zaehlt_treffer_nicht_kills(player):
    """Frueher burst_kills/spray_kills benannt, gezaehlt wurden Treffer.

    Die Summe muss deshalb ueber den Kills liegen duerfen - hier 25
    Treffer bei 10 Kills.
    """
    assert (player.burst_hits, player.spray_hits) == (23, 2)
    assert player.burst_hits + player.spray_hits > player.kills


# ── Positionsdaten, die in den Export wandern ────────────────────────

def test_positionen_vollstaendig(result):
    """Grundlage von 2D-Karte, Utility-Karte und Demo-Sprung."""
    assert len(result.kill_positions) == 37
    assert len(result.utility_positions) == 72
    assert all("tick" in kp for kp in result.kill_positions)
