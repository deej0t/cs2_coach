"""Tests fuer match_compare: dieses Match gegen die eigenen letzten Matches."""

from __future__ import annotations

import json

from cs2_coach import match_compare as mc


def export(adr=80.0, crosshair=3.0, first_sight=None, **match):
    p = {"adr": adr, "crosshair_placement": {"median_degrees": crosshair, "kills_analyzed": 10}}
    if first_sight is not None:
        p["first_sight"] = {"median_degrees": first_sight, "sightings": 30}
    return {"player": p, "match": {"total_rounds": 20, **match}}


def row(rows, key):
    return next(r for r in rows if r.key == key)


def test_prozentrang_beachtet_die_richtung():
    history = [export(adr=a, crosshair=c) for a, c in
               [(60, 5.0), (70, 4.0), (80, 3.0), (90, 2.0), (100, 1.0)]]
    rows = mc.compare(export(adr=95, crosshair=1.5), history)
    assert row(rows, "adr").better_pct == 80           # hoeher ist besser
    assert row(rows, "crosshair_placement").better_pct == 80   # niedriger ist besser
    assert row(rows, "adr").median == 80


def test_gleichstand_zaehlt_halb():
    rows = mc.compare(export(adr=80), [export(adr=80)] * 5)
    assert row(rows, "adr").better_pct == 50


def test_zu_wenig_historie_kein_prozentrang():
    """Vier Werte sind keine Verteilung - kein Rang, kein Platz unter den Top 5."""
    rows = mc.compare(export(adr=150), [export()] * (mc.MIN_HISTORY - 1))
    assert row(rows, "adr").better_pct is None
    assert row(rows, "adr").median is None
    assert mc.top(rows) == []


def test_beobachtete_kennzahl_ist_dabei():
    """first_sight ist observe_only - hier soll es trotzdem erscheinen."""
    rows = mc.compare(export(first_sight=2.0), [export(first_sight=3.0)] * 5)
    assert row(rows, "first_sight").better_pct == 100


def test_top_sortiert_nach_abstand_vom_normalfall():
    history = [export(adr=a, crosshair=3.0) for a in (60, 70, 80, 90, 100)]
    rows = mc.compare(export(adr=10, crosshair=3.0), history)
    assert mc.top(rows)[0].key == "adr"                # 0 % ist so ungewoehnlich wie 100 %
    assert row(rows, "adr").better_pct == 0


def board(teams=True):
    out = []
    for i in range(10):
        p = {"steam_id": str(i), "utility_per_round": 1.5 if i < 5 else 0.5,
             "is_target": i == 0}
        if teams:
            p["team"] = "T" if i < 5 else "CT"
        out.append(p)
    return out


def test_granaten_beider_teams():
    u = mc.team_utility({"scoreboard": board(), "match": {"total_rounds": 20}})
    assert (u["own"], u["enemy"], u["mine"]) == (150, 50, 30)
    assert u["own_width"] == 100 and u["enemy_width"] == 33


def test_aeltere_exporte_trennen_teams_ueber_die_duelle():
    data = {"scoreboard": board(teams=False), "match": {"total_rounds": 20},
            "duel_matrix": [{"steam_id": str(i)} for i in range(5, 10)]}
    assert mc.team_utility(data)["enemy"] == 50


def test_unsichere_teams_ergeben_keine_karte():
    """Fehlt ein Gegner in den Duellen, zaehlte er sonst als Mitspieler."""
    data = {"scoreboard": board(teams=False), "match": {"total_rounds": 20},
            "duel_matrix": [{"steam_id": str(i)} for i in range(5, 9)]}
    assert mc.team_utility(data) is None
    roster = board()
    roster[3]["team"] = "2"           # Startseite nicht aufgeloest
    assert mc.team_utility({"scoreboard": roster, "match": {"total_rounds": 20}}) is None


def test_historie_in_spielreihenfolge_und_ohne_das_match_selbst(tmp_path):
    names = ["2026-11-12_nuke_5-13_2105", "2026-11-12_cache_13-6_2143",
             "2026-11-11_anubis_3-13_2107"]
    for n in names:
        (tmp_path / f"{n}_coach.json").write_text(json.dumps({"id": n}), encoding="utf-8")
    hist = mc.history_for(tmp_path, "2026-11-12_cache_13-6_2143_coach.json")
    # nach Uhrzeit, nicht nach Map: Nuke 21:05 kam vor Cache 21:43
    assert [h["id"] for h in hist] == ["2026-11-11_anubis_3-13_2107", "2026-11-12_nuke_5-13_2105"]
