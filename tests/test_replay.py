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


def ticks_frame(start, end, step, sids, x=-2953.0, y=2164.0, health=100):
    """Positionen im Koordinatenraum von Ancient (pos_x -2953, pos_y 2164)."""
    rows = []
    for t in range(start, end, step):
        for i, sid in enumerate(sids):
            rows.append({"tick": t, "steamid": sid,
                         "X": x + 500 + i * 10, "Y": y - 500 - i * 10,
                         "health": health})
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
