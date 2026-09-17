"""Spielt einen aufgezeichneten Demo-Parse zurueck.

Ersetzt DemoParser durch ein Objekt, das die mit
tests/tools/record_demo_fixture.py aufgezeichneten Antworten liefert. Damit
laeuft die vollstaendige Parse-Kette gegen echte Spieldaten, ohne dass eine
42-MB-Demo im Repository liegen muss.

Ein nicht aufgezeichneter Aufruf ist ein Fehler, kein leeres Ergebnis:
liest der Parser kuenftig ein weiteres Feld, soll der Test das sagen und
zur Neuaufzeichnung auffordern, statt stillschweigend mit None
weiterzurechnen und eine Kennzahl auf null zu setzen.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "demo"


def _key(method: str, payload) -> str:
    blob = json.dumps([method, payload], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


class NotRecorded(AssertionError):
    """Der Parser fragt etwas ab, das die Aufzeichnung nicht kennt."""


class ReplayParser:
    def __init__(self, fixture_dir: Path = FIXTURE_DIR):
        self.dir = Path(fixture_dir)
        manifest = json.loads(
            (self.dir / "manifest.json").read_text(encoding="utf-8"))
        self.demo = manifest["demo"]
        self.player_name = manifest.get("player_name", "")
        self.timestamp = manifest["timestamp"]
        self._by_key = {c["key"]: c for c in manifest["calls"]}
        self._events = {c["payload"]["name"]: c
                        for c in manifest["calls"]
                        if c["method"] == "parse_events:item"}

    # ── innen ────────────────────────────────────────────────────────

    def _frame(self, entry: dict):
        if entry["kind"] == "none":
            return None
        if entry["kind"] == "json":
            return entry["value"]
        return pd.read_parquet(self.dir / entry["file"])

    def _lookup(self, method: str, payload):
        entry = self._by_key.get(_key(method, payload))
        if entry is None:
            raise NotRecorded(
                f"{method}({payload!r}) ist nicht aufgezeichnet. "
                f"Fixture mit tests/tools/record_demo_fixture.py neu erzeugen.")
        return self._frame(entry)

    # ── die von parser.py genutzte Oberflaeche ───────────────────────

    def parse_header(self):
        return self._lookup("parse_header", None)

    def parse_player_info(self):
        return self._lookup("parse_player_info", None)

    def parse_events(self, names, **kw):
        payload = {"names": list(names), **{k: str(v) for k, v in kw.items()}}
        entry = self._by_key.get(_key("parse_events", payload))
        if entry is None:
            raise NotRecorded(
                f"parse_events({list(names)!r}) ist nicht aufgezeichnet.")
        return [(n, self._frame(self._events[n])) for n in entry["names"]]

    def parse_event(self, name, player=None, other=None, **kw):
        return self._lookup("parse_event", {
            "name": name, "player": sorted(player or []),
            "other": sorted(other or [])})

    def parse_ticks(self, props, ticks=None, **kw):
        return self._lookup("parse_ticks", {
            "props": sorted(props),
            "ticks": sorted(int(t) for t in ticks) if ticks is not None else None})

    def __getattr__(self, item):
        raise NotRecorded(
            f"DemoParser.{item} wird von der Aufzeichnung nicht abgedeckt.")


def parse_recorded_demo(fixture_dir: Path = FIXTURE_DIR):
    """Laesst parse_demo vollstaendig gegen die Aufzeichnung laufen.

    Ersetzt neben dem Parser auch _read_demo_timestamp: das Datum kaeme
    sonst aus der mtime der Platzhalterdatei und das Ergebnis waere von
    Lauf zu Lauf verschieden.
    """
    import datetime
    import tempfile

    from cs2_coach import parser as parser_mod

    rp = ReplayParser(fixture_dir)
    placeholder = Path(tempfile.mkdtemp()) / rp.demo
    placeholder.write_bytes(b"")

    orig_parser = parser_mod.DemoParser
    orig_ts = parser_mod._read_demo_timestamp
    parser_mod.DemoParser = lambda _path: rp
    parser_mod._read_demo_timestamp = \
        lambda _path: datetime.datetime.fromisoformat(rp.timestamp)
    try:
        return parser_mod.parse_demo(str(placeholder), rp.player_name)
    finally:
        parser_mod.DemoParser = orig_parser
        parser_mod._read_demo_timestamp = orig_ts
