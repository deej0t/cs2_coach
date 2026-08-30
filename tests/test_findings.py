"""Tests fuer die strukturierten Coach-Befunde und deren Nachverfolgung."""

from __future__ import annotations

import pytest

from cs2_coach import findings as F


def player(**over) -> dict:
    """Spieler-Dict im Export-JSON-Schema, per Default ueberall unauffaellig."""
    base = {
        "adr": 90.0,
        "kd": 1.3,
        "kast_pct": 80.0,
        "utility_per_round": 2.0,
        "survival_rate": 50.0,
        "accuracy": 25.0,
        "counter_strafe_pct": 95.0,
        "crosshair_placement": {"avg_degrees": 4.0, "kills_analyzed": 20},
    }
    for k, v in over.items():
        if k == "crosshair":
            base["crosshair_placement"]["avg_degrees"] = v
        else:
            base[k] = v
    return base


# ── Einzelbewertung ─────────────────────────────────────────────────────

def test_clean_player_has_no_issues():
    assert F.issues(player()) == []


@pytest.mark.parametrize("degrees,expected", [
    (30.0, F.CRITICAL),
    (16.0, F.CRITICAL),
    (15.0, F.WARNING),   # Grenze gehoert noch zur milderen Stufe
    (9.0, F.WARNING),
    (8.0, F.OK),
    (2.0, F.OK),
])
def test_crosshair_thresholds(degrees, expected):
    """Niedriger ist besser - die Schwellen muessen exakt sitzen."""
    found = {f.key: f for f in F.evaluate(player(crosshair=degrees))}
    assert found["crosshair_placement"].severity == expected


@pytest.mark.parametrize("adr,expected", [
    (50.0, F.CRITICAL),
    (64.9, F.CRITICAL),
    (65.0, F.WARNING),
    (79.9, F.WARNING),
    (80.0, F.OK),
    (120.0, F.OK),
])
def test_adr_thresholds(adr, expected):
    """Hoeher ist besser - Gegenrichtung zur Crosshair-Regel."""
    found = {f.key: f for f in F.evaluate(player(adr=adr))}
    assert found["adr"].severity == expected


def test_issues_are_sorted_by_severity():
    p = player(adr=50.0, kast_pct=70.0)  # critical + warning
    got = F.issues(p)
    assert [f.severity for f in got] == sorted(
        [f.severity for f in got], key=lambda s: F.SEVERITY_ORDER[s]
    )
    assert got[0].key == "adr"


def test_missing_metric_is_skipped_not_guessed():
    p = player()
    del p["adr"]
    keys = {f.key for f in F.evaluate(p)}
    assert "adr" not in keys
    assert "kd" in keys


def test_crosshair_needs_analyzed_kills():
    """Ohne ausgewertete Kills ist 0.0 kein Befund, sondern fehlende Daten."""
    p = player()
    p["crosshair_placement"] = {"avg_degrees": 0.0, "kills_analyzed": 0}
    assert "crosshair_placement" not in {f.key for f in F.evaluate(p)}


def test_non_numeric_value_is_ignored():
    p = player(adr="keine Ahnung")
    assert "adr" not in {f.key for f in F.evaluate(p)}


def test_empty_player_yields_nothing():
    assert F.evaluate({}) == []


# ── Nachverfolgung ──────────────────────────────────────────────────────

def match(date, **over):
    return {"date": date, "map": "de_nuke", "player": player(**over)}


def test_no_matches_yields_no_tracks():
    assert F.build_tracks([]) == []


def test_chronic_issue_is_flagged_and_ranked_first():
    """Dauerproblem ueber alle juengsten Matches."""
    matches = [match(f"2026-08-{d:02d}", adr=50.0) for d in range(1, 8)]
    tracks = {t.key: t for t in F.build_tracks(matches)}

    adr = tracks["adr"]
    assert adr.status == F.CHRONIC
    assert adr.issue_count == 7
    assert adr.current_streak == 7
    assert F.build_tracks(matches)[0].key == "adr", "Chronisches zuerst"


def test_resolved_issue_is_detected():
    """Frueher problematisch, in den juengsten Matches sauber."""
    matches = (
        [match(f"2026-08-{d:02d}", adr=50.0) for d in range(1, 4)]
        + [match(f"2026-08-{d:02d}", adr=95.0) for d in range(4, 10)]
    )
    adr = {t.key: t for t in F.build_tracks(matches)}["adr"]

    assert adr.status == F.RESOLVED
    assert adr.current_streak == 0
    assert adr.issue_count == 3


def test_improving_when_recent_window_still_has_a_hit():
    """Aktuell sauber, aber im juengsten Fenster war noch ein Ausrutscher."""
    matches = (
        [match(f"2026-08-{d:02d}", adr=50.0) for d in range(1, 4)]
        + [match(f"2026-08-{d:02d}", adr=95.0) for d in range(4, 7)]
    )
    adr = {t.key: t for t in F.build_tracks(matches)}["adr"]

    assert adr.current_streak == 0
    assert adr.status == F.IMPROVING


def test_new_issue_only_in_recent_matches():
    matches = (
        [match(f"2026-08-{d:02d}", adr=95.0) for d in range(1, 8)]
        + [match(f"2026-08-{d:02d}", adr=50.0) for d in range(8, 12)]
    )
    adr = {t.key: t for t in F.build_tracks(matches)}["adr"]

    assert adr.status == F.NEW
    assert adr.current_streak == 4


def test_direction_aware_improvement():
    """Verbesserung heisst je nach Metrik steigend oder fallend."""
    rising = [match(f"2026-08-{d:02d}", adr=50.0) for d in range(1, 6)] + \
             [match(f"2026-08-{d:02d}", adr=95.0) for d in range(6, 11)]
    adr = {t.key: t for t in F.build_tracks(rising)}["adr"]
    assert adr.delta > 0 and adr.improved

    falling = [match(f"2026-08-{d:02d}", crosshair=30.0) for d in range(1, 6)] + \
              [match(f"2026-08-{d:02d}", crosshair=4.0) for d in range(6, 11)]
    ch = {t.key: t for t in F.build_tracks(falling)}["crosshair_placement"]
    assert ch.delta < 0 and ch.improved, "Weniger Grad = besser"


def test_worsening_is_not_reported_as_improvement():
    matches = [match(f"2026-08-{d:02d}", adr=95.0) for d in range(1, 6)] + \
              [match(f"2026-08-{d:02d}", adr=50.0) for d in range(6, 11)]
    adr = {t.key: t for t in F.build_tracks(matches)}["adr"]
    assert adr.delta < 0
    assert not adr.improved


def test_history_preserves_order_and_dates():
    matches = [match("2026-08-01", adr=50.0), match("2026-08-02", adr=95.0)]
    adr = {t.key: t for t in F.build_tracks(matches)}["adr"]

    assert [h["date"] for h in adr.history] == ["2026-08-01", "2026-08-02"]
    assert adr.first_seen == "2026-08-01"
    assert adr.last_seen == "2026-08-01"


def test_single_outlier_does_not_dominate_window():
    """Ein Ausreisser darf den Verlauf nicht dominieren.

    Echter Fall aus dem Vault: ein Match mit 21 Kills / 0 Deaths (K/D 21.0)
    zog den Mittelwert der ersten fuenf Matches auf 5.39 hoch und liess einen
    dramatischen Abfall erscheinen, den es nie gab. Deshalb Median.
    """
    matches = [match(f"2026-08-{d:02d}", kd=1.2) for d in range(1, 6)]
    matches[2] = match("2026-08-03", kd=21.0)
    kd = {t.key: t for t in F.build_tracks(matches)}["kd"]

    assert kd.first_value == pytest.approx(1.2), (
        f"Median muss den 21.0-Ausreisser ignorieren, war {kd.first_value}"
    )


def test_recent_window_uses_median_not_last_value():
    matches = [match(f"2026-08-{d:02d}", adr=90.0) for d in range(1, 10)]
    matches[-1] = match("2026-08-09", adr=20.0)
    adr = {t.key: t for t in F.build_tracks(matches)}["adr"]

    assert adr.recent_value == pytest.approx(90.0)


def test_match_without_player_data_is_survivable():
    tracks = F.build_tracks([{"date": "2026-08-01", "map": "de_nuke"}])
    assert tracks == []
