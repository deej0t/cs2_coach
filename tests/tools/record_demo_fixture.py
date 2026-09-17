"""Zeichnet einen echten Demo-Parse als Test-Fixture auf.

Hintergrund: jeder der fuenf Metrikfehler (ADR, Accuracy, Counter-Strafing,
Crosshair, Spray-Benennung) wurde durch Messen an echten Demos gefunden -
keiner durch einen Test. Die vorhandenen Tests bauen synthetische
DataFrames und halten die Korrektur fest, haetten den Fehler aber nicht
finden koennen: sie bringen genau die Annahme mit, die falsch war.

Echte Demos taugen nicht als Fixture, sie sind 42 bis 331 MB gross. Statt
der Demo wird deshalb aufgezeichnet, was der Parser aus ihr liest: jeder
Aufruf an DemoParser mit seinem Ergebnis. Das laesst sich als Parquet
ablegen und ohne die Demo zurueckspielen.

Aufruf (einmalig, wenn sich die gelesenen Felder aendern):

    python -m tests.tools.record_demo_fixture <demo.dem> [--out tests/fixtures/demo]

Die aufgezeichneten Werte sind bewusst keine Wunschwerte, sondern der
Stand, der am 16.09.2026 gegen alle 63 Exporte als korrekt bestaetigt
wurde (metrics_version 1).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
from demoparser2 import DemoParser


def _key(method: str, payload) -> str:
    """Stabiler Schluessel je Aufruf, unabhaengig von der Reihenfolge."""
    blob = json.dumps([method, payload], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


class RecordingParser:
    """Reicht an den echten Parser durch und schreibt jedes Ergebnis mit."""

    def __init__(self, demo_path: str, out_dir: Path):
        self._real = DemoParser(demo_path)
        self._out = out_dir
        self._out.mkdir(parents=True, exist_ok=True)
        self.manifest: list[dict] = []

    def _store(self, method: str, payload, value):
        key = _key(method, payload)
        entry = {"key": key, "method": method, "payload": payload}
        if isinstance(value, pd.DataFrame):
            fn = f"{key}.parquet"
            # Objekt-Spalten mit gemischten Typen brechen Parquet; die
            # betroffenen Spalten werden zu Text, was fuer die Auswertung
            # ausreicht (SteamIDs sind ohnehin Strings).
            df = value.copy()
            for col in df.columns:
                if df[col].dtype == object:
                    df[col] = df[col].astype(str)
            df.to_parquet(self._out / fn, index=False)
            entry.update(kind="frame", file=fn, rows=len(df))
        elif value is None:
            entry.update(kind="none")
        else:
            entry.update(kind="json", value=value)
        self.manifest.append(entry)
        return value

    # ── Die Aufrufe, die parser.py tatsaechlich macht ────────────────

    def parse_header(self):
        return self._store("parse_header", None, self._real.parse_header())

    def parse_player_info(self):
        return self._store("parse_player_info", None,
                           self._real.parse_player_info())

    def parse_events(self, names, **kw):
        payload = {"names": list(names), **{k: str(v) for k, v in kw.items()}}
        res = self._real.parse_events(list(names), **kw)
        # Ergebnis ist eine Liste (name, df) - je Eintrag einzeln ablegen.
        stored = []
        for name, df in (res or []):
            self._store("parse_events:item", {"name": name}, df)
            stored.append(name)
        self.manifest.append({"key": _key("parse_events", payload),
                              "method": "parse_events", "payload": payload,
                              "kind": "eventlist", "names": stored})
        return res

    def parse_event(self, name, player=None, other=None, **kw):
        payload = {"name": name, "player": sorted(player or []),
                   "other": sorted(other or [])}
        return self._store("parse_event", payload,
                           self._real.parse_event(name, player=player,
                                                  other=other, **kw))

    def parse_ticks(self, props, ticks=None, **kw):
        payload = {"props": sorted(props),
                   "ticks": sorted(int(t) for t in ticks) if ticks is not None else None}
        return self._store("parse_ticks", payload,
                           self._real.parse_ticks(list(props), ticks=ticks, **kw))

    def __getattr__(self, item):
        # Alles, was parser.py sonst noch braucht, faellt auf.
        raise AttributeError(
            f"Nicht aufgezeichneter Zugriff auf DemoParser.{item} - "
            f"record_demo_fixture.py muss ergaenzt werden.")


def record(demo: str, out_dir: Path, player_name: str = "") -> dict:
    from cs2_coach import parser as parser_mod

    rec = RecordingParser(demo, out_dir)
    orig = parser_mod.DemoParser
    parser_mod.DemoParser = lambda _p: rec
    try:
        result = parser_mod.parse_demo(demo, player_name)
    finally:
        parser_mod.DemoParser = orig

    ts = parser_mod._read_demo_timestamp(Path(demo))
    (out_dir / "manifest.json").write_text(json.dumps({
        "demo": Path(demo).name,
        "timestamp": ts.isoformat(),
        # Der Zielspieler gehoert zur Aufzeichnung: die erwarteten Werte
        # gelten fuer ihn.
        "player_name": player_name,
        "calls": rec.manifest,
    }, indent=2), encoding="utf-8")
    return {"result": result, "calls": len(rec.manifest)}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    demo = argv[0]
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv \
        else Path("tests/fixtures/demo")
    player = argv[argv.index("--player") + 1] if "--player" in argv else ""
    info = record(demo, out, player)
    r = info["result"]
    s = r.player_stats
    size = sum(f.stat().st_size for f in out.glob("*"))
    print(f"aufgezeichnet: {info['calls']} Aufrufe, {size/1e6:.1f} MB -> {out}")
    print(f"{r.map_name} {r.score_team1}:{r.score_team2} | "
          f"{s.name} K/D {s.kills}/{s.deaths} | ADR {s.adr:.1f} | "
          f"Acc {s.accuracy:.1f}% | KAST {s.kast_pct:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
