"""Tests fuer das 2D-Runden-Replay.

Kein Spielvideo: dafuer muesste CS2 mit GPU laufen. Stattdessen eine
Rekonstruktion aus Positionsdaten auf der Radar-Karte. Eine Runde kostet
rund zwei Sekunden, weshalb die Daten bei Bedarf gelesen und nicht im
Export gespeichert werden - alle Runden aller Matches wuerden ihn um ein
Vielfaches aufblaehen.
"""

from __future__ import annotations

import pandas as pd
import pytest

from cs2_coach.web.app import REPLAY_FPS, build_round_replay


class FakeParser:
    def __init__(self, ends, freezes, ticks_df, kills=None, names=None):
        self._ends = ends
        self._freezes = freezes
        self._ticks = ticks_df
        self._kills = kills if kills is not None else []
        self._names = names or []

    def parse_event(self, name, **kw):
        if name == "round_end":
            return pd.DataFrame({"tick": self._ends})
        if name == "round_freeze_end":
            return pd.DataFrame({"tick": self._freezes})
        if name == "player_death":
            return pd.DataFrame(self._kills, columns=[
                "tick", "attacker_steamid", "user_steamid", "weapon"])
        raise ValueError(name)

    def parse_ticks(self, props, ticks=None):
        df = self._ticks
        return df[df["tick"].isin(ticks)] if ticks is not None else df

    def parse_player_info(self):
        return pd.DataFrame(self._names, columns=["steamid", "name"])


@pytest.fixture
def fake(monkeypatch):
    """DemoParser durch den Fake ersetzen - build_round_replay importiert lokal."""
    holder = {}

    class Factory:
        def __init__(self, path):
            pass
        def __new__(cls, path):
            return holder["parser"]

    import demoparser2
    monkeypatch.setattr(demoparser2, "DemoParser", Factory)
    return holder


def ticks_frame(start, end, step, sids, x=-2953.0, y=2164.0, health=100,
                teams=None):
    """Positionen im Koordinatenraum von Ancient (pos_x -2953, pos_y 2164).

    ``teams`` ordnet SteamIDs ein team_num zu (2 = T, 3 = CT).
    """
    teams = teams or {}
    rows = []
    for t in range(start, end, step):
        for i, sid in enumerate(sids):
            rows.append({"tick": t, "steamid": sid,
                         "X": x + 500 + i * 10, "Y": y - 500 - i * 10,
                         "health": health, "team_num": teams.get(sid)})
    return pd.DataFrame(rows)


def test_frames_cover_the_round(fake):
    step = 64 // REPLAY_FPS
    fake["parser"] = FakeParser(
        ends=[1000, 2000], freezes=[100, 1100],
        ticks_df=ticks_frame(1100, 2000, step, ["A", "B"]),
        names=[["A", "deej0t"], ["B", "mate"]])

    r = build_round_replay("x.dem", 2, "A", "ancient")

    assert r["start_tick"] == 1100 and r["end_tick"] == 2000
    assert r["fps"] == REPLAY_FPS
    assert len(r["frames"]) == len(range(1100, 2000, step))
    assert len(r["players"]) == 2


def test_target_player_is_marked(fake):
    fake["parser"] = FakeParser(
        ends=[1000], freezes=[100],
        ticks_df=ticks_frame(100, 1000, 8, ["A", "B"]),
        names=[["A", "deej0t"], ["B", "mate"]])

    r = build_round_replay("x.dem", 1, "A", "ancient")
    tgt = [p for p in r["players"] if p["is_target"]]
    assert len(tgt) == 1 and tgt[0]["name"] == "deej0t"


def test_coordinates_are_radar_space(fake):
    """Die Umrechnung passiert serverseitig, damit sie nur einmal existiert."""
    fake["parser"] = FakeParser(
        ends=[1000], freezes=[100],
        ticks_df=ticks_frame(100, 1000, 8, ["A"]))

    r = build_round_replay("x.dem", 1, "A", "ancient")
    for fr in r["frames"]:
        for _, x, y, _hp in fr["p"]:
            assert 0 <= x <= 1024 and 0 <= y <= 1024


def test_health_is_carried_for_dead_players(fake):
    fake["parser"] = FakeParser(
        ends=[1000], freezes=[100],
        ticks_df=ticks_frame(100, 1000, 8, ["A"], health=0))

    r = build_round_replay("x.dem", 1, "A", "ancient")
    assert all(e[3] == 0 for fr in r["frames"] for e in fr["p"])


def test_kills_within_the_round_are_included(fake):
    fake["parser"] = FakeParser(
        ends=[1000], freezes=[100],
        ticks_df=ticks_frame(100, 1000, 8, ["A"]),
        kills=[[500, "A", "B", "ak47"], [5000, "A", "B", "awp"]])

    r = build_round_replay("x.dem", 1, "A", "ancient")
    assert len(r["kills"]) == 1, "Kills ausserhalb der Runde gehoeren nicht dazu"
    assert r["kills"][0]["tick"] == 500


def test_unknown_round_is_rejected(fake):
    fake["parser"] = FakeParser(ends=[1000], freezes=[100],
                                ticks_df=ticks_frame(100, 1000, 8, ["A"]))
    assert "error" in build_round_replay("x.dem", 99, "A", "ancient")
    assert "error" in build_round_replay("x.dem", 0, "A", "ancient")


def test_missing_freeze_falls_back_to_previous_round_end(fake):
    """Ohne Freeze-Tick beginnt die Runde am Ende der vorherigen."""
    fake["parser"] = FakeParser(
        ends=[1000, 2000], freezes=[],
        ticks_df=ticks_frame(1000, 2000, 8, ["A"]))

    r = build_round_replay("x.dem", 2, "A", "ancient")
    assert r["start_tick"] == 1000


def test_players_are_referenced_by_index(fake):
    """Wiederholte SteamIDs je Frame wuerden die Antwort deutlich aufblaehen."""
    fake["parser"] = FakeParser(
        ends=[1000], freezes=[100],
        ticks_df=ticks_frame(100, 1000, 8, ["A", "B"]))

    r = build_round_replay("x.dem", 1, "A", "ancient")
    for fr in r["frames"]:
        for entry in fr["p"]:
            assert isinstance(entry[0], int)
            assert 0 <= entry[0] < len(r["players"])


# ── Team-Zuordnung ──────────────────────────────────────────────────────

def test_teammates_and_enemies_are_separated(fake):
    """Regression: ohne team_num waren beide Mannschaften gleich eingefaerbt."""
    fake["parser"] = FakeParser(
        ends=[1000], freezes=[100],
        ticks_df=ticks_frame(100, 1000, 8, ["A", "B", "C"],
                             teams={"A": 2, "B": 2, "C": 3}))

    r = build_round_replay("x.dem", 1, "A", "ancient")
    by = {p["steamid"]: p["team"] for p in r["players"]}
    assert by == {"A": "self", "B": "mate", "C": "enemy"}


def test_side_is_reported_per_player(fake):
    fake["parser"] = FakeParser(
        ends=[1000], freezes=[100],
        ticks_df=ticks_frame(100, 1000, 8, ["A", "C"], teams={"A": 3, "C": 2}))

    r = build_round_replay("x.dem", 1, "A", "ancient")
    by = {p["steamid"]: p["side"] for p in r["players"]}
    assert by == {"A": "CT", "C": "T"}


def test_team_follows_the_half_swap(fake):
    """Nach dem Seitenwechsel bleibt dasselbe Team das eigene.

    team_num kommt aus den Tickdaten der jeweiligen Runde, daher braucht
    es keine eigene Seitenlogik.
    """
    fake["parser"] = FakeParser(
        ends=[1000], freezes=[100],
        ticks_df=ticks_frame(100, 1000, 8, ["A", "B", "C"],
                             teams={"A": 3, "B": 3, "C": 2}))

    r = build_round_replay("x.dem", 1, "A", "ancient")
    by = {p["steamid"]: p["team"] for p in r["players"]}
    assert by == {"A": "self", "B": "mate", "C": "enemy"}


def test_missing_team_num_is_marked_unknown(fake):
    """Lieber "unbekannt" als eine geratene Zuordnung."""
    fake["parser"] = FakeParser(
        ends=[1000], freezes=[100],
        ticks_df=ticks_frame(100, 1000, 8, ["A", "B"], teams={}))

    r = build_round_replay("x.dem", 1, "A", "ancient")
    assert {p["team"] for p in r["players"]} == {"unknown"}


def test_spectator_team_num_is_ignored(fake):
    """team_num ausserhalb von 2 und 3 ist keine Seite."""
    fake["parser"] = FakeParser(
        ends=[1000], freezes=[100],
        ticks_df=ticks_frame(100, 1000, 8, ["A", "S"], teams={"A": 2, "S": 1}))

    r = build_round_replay("x.dem", 1, "A", "ancient")
    by = {p["steamid"]: p["team"] for p in r["players"]}
    assert by["A"] == "self" and by["S"] == "unknown"


# ── Granaten im Replay ──────────────────────────────────────────────────

class UtilParser(FakeParser):
    """Erweitert den Fake um Granaten-Events."""

    def __init__(self, *a, utils=None, **kw):
        super().__init__(*a, **kw)
        self._utils = utils or {}

    def parse_event(self, name, **kw):
        if name in self._utils:
            return pd.DataFrame(self._utils[name],
                                columns=["tick", "entityid", "user_steamid", "x", "y"])
        if name in ("smokegrenade_detonate", "smokegrenade_expired",
                    "inferno_startburn", "inferno_expire",
                    "flashbang_detonate", "hegrenade_detonate"):
            return pd.DataFrame(columns=["tick", "entityid", "user_steamid", "x", "y"])
        return super().parse_event(name, **kw)


def util_row(tick, eid, sid, x=-2453.0, y=1664.0):
    return [tick, eid, sid, x, y]


def replay_with_utils(fake, utils, teams=None):
    fake["parser"] = UtilParser(
        ends=[2000], freezes=[100],
        ticks_df=ticks_frame(100, 2000, 8, ["A", "B"], teams=teams or {"A": 2, "B": 3}),
        utils=utils)
    return build_round_replay("x.dem", 1, "A", "ancient")


def test_smoke_duration_comes_from_the_expire_event(fake):
    """Die echte Dauer steht in den Daten und muss nicht geraten werden."""
    r = replay_with_utils(fake, {
        "smokegrenade_detonate": [util_row(500, 7, "A")],
        "smokegrenade_expired": [util_row(500 + 64 * 18, 7, "A")],
    })
    u = r["utility"][0]
    assert u["type"] == "smoke"
    assert (u["end"] - u["t"]) / 64 == pytest.approx(18.0)


def test_reused_entity_id_takes_the_next_end(fake):
    """Regression: entityids werden im Match wiederverwendet.

    Ohne die Einschraenkung auf spaetere Ticks entstanden negative und
    2000-Sekunden-Dauern, weil ein fruehes Ende zugeordnet wurde.
    """
    r = replay_with_utils(fake, {
        "smokegrenade_detonate": [util_row(1000, 7, "A")],
        "smokegrenade_expired": [util_row(200, 7, "A"), util_row(1500, 7, "A")],
    })
    assert r["utility"][0]["end"] == 1500


def test_end_is_clamped_to_round_end(fake):
    """Ein Smoke darf nicht ueber das Rundenende hinaus stehen."""
    r = replay_with_utils(fake, {
        "smokegrenade_detonate": [util_row(1900, 7, "A")],
        "smokegrenade_expired": [util_row(99999, 7, "A")],
    })
    assert r["utility"][0]["end"] == 2000


def test_flash_without_expire_stays_visible_long_enough(fake):
    """Flash und HE haben kein Ende-Event.

    Sie wirken schlagartig, muessen im Replay aber lange genug stehen, um
    wahrgenommen zu werden: bei 8 Bildern pro Sekunde waeren 0.4 Sekunden
    nur drei Frames.
    """
    r = replay_with_utils(fake, {"flashbang_detonate": [util_row(500, 3, "A")]})
    u = r["utility"][0]
    assert u["type"] == "flash"
    frames_visible = (u["end"] - u["t"]) / 64 * 8
    assert frames_visible >= 8, f"nur {frames_visible:.0f} Frames sichtbar"
    assert (u["end"] - u["t"]) / 64 <= 2.0, "aber kein Dauerzustand"


def test_utility_outside_the_round_is_dropped(fake):
    r = replay_with_utils(fake, {
        "hegrenade_detonate": [util_row(50, 1, "A"), util_row(500, 2, "A"),
                               util_row(5000, 3, "A")]})
    assert [u["t"] for u in r["utility"]] == [500]


def test_utility_carries_thrower_team(fake):
    """Eigene und gegnerische Granaten muessen unterscheidbar sein."""
    r = replay_with_utils(fake, {
        "smokegrenade_detonate": [util_row(500, 1, "A"), util_row(600, 2, "B")],
        "smokegrenade_expired": [],
    })
    by = {u["t"]: u["team"] for u in r["utility"]}
    assert by == {500: "self", 600: "enemy"}


def test_utility_is_sorted_by_time(fake):
    r = replay_with_utils(fake, {
        "hegrenade_detonate": [util_row(900, 1, "A")],
        "flashbang_detonate": [util_row(300, 2, "A")],
        "smokegrenade_detonate": [util_row(600, 3, "A")],
    })
    ticks = [u["t"] for u in r["utility"]]
    assert ticks == sorted(ticks)


def test_utility_coordinates_are_radar_space(fake):
    r = replay_with_utils(fake, {"smokegrenade_detonate": [util_row(500, 1, "A")]})
    u = r["utility"][0]
    assert 0 <= u["x"] <= 1024 and 0 <= u["y"] <= 1024
    assert u["r"] > 0, "Radius wird in Radareinheiten umgerechnet"
