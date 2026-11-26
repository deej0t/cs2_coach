"""Demo-Parser mit Gedaechtnis fuer die Schluesselmomente.

Jeder ``parse_event``-Aufruf liest die ganze Demo (~0,3 s lokal, ~0,4 s auf dem
Server); eine Szene brauchte 17 davon. ``CachedParser`` liest beim ersten
Bedarf alle ueblichen Events in EINEM Durchlauf (``parse_events``) und behaelt
sie je Demo im Speicher - die naechsten Szenen desselben Matches (Vorberechnung
im Hintergrund) kosten dafuer nichts mehr. Dazu: ``preload_ticks`` liest die
Tick-Felder aller Teile einer Szene in einem Aufruf; spaetere ``parse_ticks``,
die darin enthalten sind, werden aus dem Vorrat bedient.

Rueckgaben sind Kopien - Aufrufer duerfen sie veraendern.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict

EVENTS = ("round_end", "round_freeze_end", "player_death", "weapon_fire", "player_hurt",
          "smokegrenade_detonate", "smokegrenade_expired", "inferno_startburn", "inferno_expire",
          "flashbang_detonate", "hegrenade_detonate", "decoy_started", "decoy_detonate")
MAX_DEMOS = 2               # so viele Demos bleiben im Speicher (je ~10-40 MB)

_store: OrderedDict[str, dict] = OrderedDict()
_lock = threading.Lock()


def _entry(path: str) -> dict:
    key = os.path.abspath(path)
    if not os.path.exists(path):     # nichts Echtes zum Merken (Tests mit Fake-Parser)
        return {"mtime": None, "events": {}, "grenades": None, "info": None, "lock": threading.Lock()}
    mtime = (os.path.getmtime(path), os.path.getsize(path))
    with _lock:
        e = _store.get(key)
        if e is None or e["mtime"] != mtime:
            e = {"mtime": mtime, "events": {}, "grenades": None, "info": None, "lock": threading.Lock()}
            _store[key] = e
        _store.move_to_end(key)
        while len(_store) > MAX_DEMOS:
            _store.popitem(last=False)
    return e


def clear() -> None:
    with _lock:
        _store.clear()


class CachedParser:
    """Wie ``demoparser2.DemoParser``, mit Gedaechtnis fuer Events, Granaten, Spielerinfo."""

    def __init__(self, path: str):
        from demoparser2 import DemoParser
        self.path = str(path)
        self._p = DemoParser(self.path)
        self._e = _entry(self.path)
        self._ticks = None          # (Felder, Ticks, DataFrame) aus preload_ticks

    def parse_event(self, name: str, player=None, other=None):
        if player or other:         # Zusatzspalten: nicht im Vorrat
            return self._p.parse_event(name, player=player or [], other=other or [])
        if self._e["mtime"] is None:    # keine echte Datei: ohne Gedaechtnis durchreichen
            return self._p.parse_event(name)
        ev = self._e["events"]
        if name not in ev:
            with self._e["lock"]:
                if name not in ev:
                    import pandas as pd
                    want = [n for n in dict.fromkeys(EVENTS + (name,)) if n not in ev]
                    got = dict(self._p.parse_events(want))
                    for n in want:      # nie ausgeloeste Events fehlen in der Antwort
                        ev[n] = got.get(n, pd.DataFrame())
        return ev[name].copy()

    def parse_grenades(self):
        if self._e["mtime"] is None:
            return self._p.parse_grenades()
        with self._e["lock"]:
            if self._e["grenades"] is None:
                self._e["grenades"] = self._p.parse_grenades()
        return self._e["grenades"].copy()

    def parse_player_info(self):
        if self._e["mtime"] is None:
            return self._p.parse_player_info()
        with self._e["lock"]:
            if self._e["info"] is None:
                self._e["info"] = self._p.parse_player_info()
        return self._e["info"].copy()

    def preload_ticks(self, fields, ticks) -> None:
        """Felder fuer alle Ticks einer Szene in einem Durchlauf lesen."""
        fields, ticks = list(dict.fromkeys(fields)), sorted(set(int(t) for t in ticks))
        self._ticks = (set(fields), set(ticks), self._p.parse_ticks(fields, ticks=ticks))

    def parse_ticks(self, fields, ticks=None, **kw):
        if self._ticks and ticks is not None and not kw:
            have_f, have_t, df = self._ticks
            want = set(int(t) for t in ticks)
            if set(fields) <= have_f and want <= have_t:
                cols = [c for c in df.columns if c not in have_f or c in fields]
                return df[df["tick"].isin(want)][cols].reset_index(drop=True)
        return self._p.parse_ticks(fields, ticks=ticks, **kw)

    def __getattr__(self, name):
        return getattr(self._p, name)


def cached_parser(path) -> CachedParser:
    """Ersatz fuer ``DemoParser(path)`` (gleiche Methoden)."""
    return CachedParser(path)
