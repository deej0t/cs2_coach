"""Tests fuer persoenliche Baselines und Ergebnisrelevanz.

Hintergrund: die absoluten Schwellen stammen aus coach.py und sind fuer
"einen CS2-Spieler" gesetzt. An echten Daten zeigte sich, dass zwei von
acht Regeln keinerlei Trennschaerfe haben - Counter-Strafing loeste in 54
von 58 Matches aus (Warnschwelle 90 Prozent, bestes Zehntel 88.6), und
Crosshair Placement in 1 von 58.
"""

from __future__ import annotations

import pytest

from cs2_coach import findings as F


def player(**over):
    base = {
        "adr": 90.0, "kd": 1.3, "kast_pct": 80.0, "utility_per_round": 2.0,
        "survival_rate": 50.0, "accuracy": 25.0, "counter_strafe_pct": 95.0,
        "crosshair_placement": {"median_degrees": 4.0, "kills_analyzed": 20},
    }
    base.update(over)
    return base


def match(result="Sieg", **over):
    return {"date": "2026-08-01", "map": "de_nuke", "result": result,
            "player": player(**over)}


# ── Baseline ────────────────────────────────────────────────────────────

def test_baseline_needs_enough_matches():
    few = [match(counter_strafe_pct=80.0) for _ in range(F.MIN_MATCHES_FOR_BASELINE - 1)]
    assert F.build_baselines(few) == {}


def test_baseline_percentiles_follow_the_distribution():
    ms = [match(counter_strafe_pct=float(v)) for v in range(60, 90)]
    b = F.build_baselines(ms)["counter_strafe"]
    assert b.n == 30
    assert b.p10 < b.p50 < b.p90


def test_always_firing_rule_is_flagged_as_uninformative():
    """Realer Fall: Counter-Strafing loeste in 93 Prozent der Matches aus."""
    ms = [match(counter_strafe_pct=v) for v in [70.0, 72.0, 75.0, 78.0, 80.0,
                                                 81.0, 82.0, 84.0, 85.0, 86.0]]
    b = F.build_baselines(ms)["counter_strafe"]
    assert b.always_fires
    assert b.is_uninformative


def test_never_firing_rule_is_flagged_as_uninformative():
    ms = [match(crosshair_placement={"median_degrees": 3.0, "kills_analyzed": 20})
          for _ in range(12)]
    b = F.build_baselines(ms)["crosshair_placement"]
    assert b.never_fires
    assert b.is_uninformative


def test_discriminating_rule_is_not_flagged():
    vals = [60.0, 65.0, 70.0, 75.0, 82.0, 88.0, 92.0, 95.0, 97.0, 99.0]
    b = F.build_baselines([match(counter_strafe_pct=v) for v in vals])["counter_strafe"]
    assert not b.is_uninformative


def test_percentile_respects_direction():
    """Bei 'niedriger ist besser' ist ein kleiner Wert das hohe Perzentil."""
    ms = [match(crosshair_placement={"median_degrees": float(v), "kills_analyzed": 20})
          for v in range(2, 22)]
    b = F.build_baselines(ms)["crosshair_placement"]
    assert b.percentile_of(3.0) > b.percentile_of(19.0)

    ms2 = [match(adr=float(v)) for v in range(60, 120)]
    b2 = F.build_baselines(ms2)["adr"]
    assert b2.percentile_of(115.0) > b2.percentile_of(62.0)


# ── Ergebnisrelevanz ────────────────────────────────────────────────────

def test_relevance_needs_both_outcomes():
    only_wins = [match("Sieg") for _ in range(20)]
    assert F.build_relevance(only_wins) == []


def test_metric_that_separates_outcomes_gets_a_large_effect():
    ms = ([match("Sieg", utility_per_round=2.5) for _ in range(10)]
          + [match("Niederlage", utility_per_round=0.8) for _ in range(10)])
    rel = {r.key: r for r in F.build_relevance(ms)}["utility"]
    assert rel.effect > 0.8
    assert rel.strength == "gross"


def test_metric_without_relationship_gets_no_effect():
    """Realer Fall: Counter-Strafing lag bei Sieg und Niederlage gleichauf."""
    ms = ([match("Sieg", counter_strafe_pct=80.0) for _ in range(10)]
          + [match("Niederlage", counter_strafe_pct=80.0) for _ in range(10)])
    rel = {r.key: r for r in F.build_relevance(ms)}["counter_strafe"]
    assert rel.effect == pytest.approx(0.0)
    assert rel.strength == "keiner"


def test_effect_sign_is_normalised_to_higher_is_better():
    """Weniger Grad ist besser - bessere Siege muessen positiv herauskommen."""
    ms = ([match("Sieg", crosshair_placement={"median_degrees": 3.0, "kills_analyzed": 20}) for _ in range(10)]
          + [match("Niederlage", crosshair_placement={"median_degrees": 12.0, "kills_analyzed": 20}) for _ in range(10)])
    rel = {r.key: r for r in F.build_relevance(ms)}["crosshair_placement"]
    assert rel.effect > 0, "niedrigerer Winkel bei Siegen = positiver Effekt"


def test_outcome_driven_metrics_are_marked():
    """Survival und Co. steigen auch blosz durch gewonnene Runden."""
    ms = ([match("Sieg", survival_rate=60.0, utility_per_round=2.0) for _ in range(10)]
          + [match("Niederlage", survival_rate=20.0, utility_per_round=1.0) for _ in range(10)])
    rel = {r.key: r for r in F.build_relevance(ms)}
    assert rel["survival"].outcome_driven is True
    assert rel["utility"].outcome_driven is False


def test_relevance_is_sorted_by_absolute_effect():
    ms = ([match("Sieg", utility_per_round=2.5, accuracy=25.5) for _ in range(10)]
          + [match("Niederlage", utility_per_round=0.8, accuracy=25.0) for _ in range(10)])
    rel = F.build_relevance(ms)
    assert [abs(r.effect) for r in rel] == sorted([abs(r.effect) for r in rel], reverse=True)


# ── Trainings-Prioritaeten ──────────────────────────────────────────────

def many(result, n, **over):
    return [match(result, **over) for _ in range(n)]


def test_outcome_driven_metrics_are_never_training_goals():
    """"Trainiere Ueberleben" ist kein Ratschlag.

    Survival hatte an echten Daten den groessten Effekt (1.17), steigt aber
    auch dann, wenn schlicht die Runde gewonnen wurde.
    """
    ms = many("Sieg", 15, survival_rate=10.0, kd=0.3, adr=40.0, kast_pct=40.0)
    keys = {t.key for t in F.training_priorities(ms, limit=8)}
    assert not (keys & {"survival", "kd", "adr", "kast"})


def test_uninformative_rule_is_not_a_training_goal():
    """Counter-Strafing loeste in 93 Prozent der Matches aus und ordnet nichts ein.

    Utility muss dagegen schwanken, sonst waere auch diese Regel nicht
    trennscharf und beide fielen raus.
    """
    util = [0.8, 2.0, 0.9, 2.1, 1.2, 2.2, 0.7, 2.0, 1.1, 2.3, 0.9, 2.1, 1.0, 2.0, 0.8]
    ms = [match("Sieg", counter_strafe_pct=82.0, utility_per_round=u) for u in util]

    prios = F.training_priorities(ms, limit=5)
    keys = {t.key for t in prios}
    assert "counter_strafe" not in keys, "loest immer aus, ordnet nichts ein"
    assert "utility" in keys, "schwankt und ist damit trennscharf"


def test_trainable_issue_is_picked():
    util = [0.5, 2.0, 0.6, 2.1, 0.4, 2.2, 0.5, 2.0, 0.7, 2.1, 0.5, 2.0, 0.6, 2.2, 0.5]
    ms = [match("Sieg", utility_per_round=u) for u in util]
    assert F.training_priorities(ms)[0].key == "utility"


def test_limit_is_respected():
    util = [0.5, 2.0] * 8
    acc = [12.0, 30.0] * 8
    ms = [match("Sieg", utility_per_round=u, accuracy=a) for u, a in zip(util, acc)]
    assert len(F.training_priorities(ms, limit=1)) == 1


def test_clean_player_still_gets_trainable_suggestions_only():
    """Ohne jeden Befund darf trotzdem nichts Ergebnisgetriebenes kommen."""
    ms = many("Sieg", 15)
    keys = {t.key for t in F.training_priorities(ms, limit=8)}
    assert not (keys & {"survival", "kd", "adr", "kast"})


def test_no_matches_yields_no_priorities():
    assert F.training_priorities([]) == []
