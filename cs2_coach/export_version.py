"""Versionierung der Export-Dateien.

Ein Export ist eine Momentaufnahme dessen, was der Parser zum Zeitpunkt der
Analyse konnte und wie er gerechnet hat. Beides aendert sich. Bisher stand
das nirgends in der Datei, was zwei verschiedene Probleme erzeugt:

*Fehlende Felder* lassen sich noch erkennen - man prueft den Schluessel und
faellt zurueck. Genau das passiert an inzwischen sechs Stellen verstreut
(``median_degrees | default(avg_degrees)``, ``burst_hits |
default(burst_kills)``, Praesenzpruefungen auf ``utility_positions``).

*Geaenderte Bedeutungen* lassen sich dagegen ueberhaupt nicht erkennen. Ein
ADR von 106 aus einem alten Export und einer von 106 aus einem neuen sehen
identisch aus - der alte ist aber um rund 30 Prozent ueberhoeht, weil
``dmg_health`` ungedeckelt aufsummiert wurde. Eine Zahl traegt ihre
Herkunft nicht mit sich.

Deshalb zwei getrennte Zaehler:

``SCHEMA_VERSION``
    Welche Felder die Datei enthaelt. Steigt, wenn etwas dazukommt.
    Ein niedriger Wert heisst: unvollstaendig, aber korrekt.

``METRICS_VERSION``
    Wie die Zahlen gerechnet wurden. Steigt, wenn sich die Bedeutung
    einer bestehenden Kennzahl aendert. Ein niedriger Wert heisst:
    vollstaendig, aber falsch - und das ist der gefaehrlichere Fall.

Bestehende Exporte tragen keinen Zaehler. Sie werden nicht pauschal als
veraltet behandelt, sondern aus den vorhandenen Feldern abgeleitet
(``_detect_*``): die 63 vorhandenen Exporte wurden am 31.08. um 21:20 neu
erzeugt, eine Minute nach der Crosshair-Korrektur, und sind damit
metrisch aktuell. Sie pauschal zu verwerfen waere schlicht falsch.
"""

from __future__ import annotations

# ── Aktueller Stand ──────────────────────────────────────────────────

SCHEMA_VERSION = 3
METRICS_VERSION = 1


# ── Historie, fuer die Anzeige ───────────────────────────────────────

#: Was die jeweilige Schema-Version ergaenzt hat. Fehlt sie, fehlt das.
SCHEMA_FEATURES = {
    1: "Crosshair-Verteilung und Median-Winkel",
    2: "Detonationsorte der eigenen Granaten (Utility-Karte)",
    3: "Ticks an den Kill-Positionen (Sprung in die Demo, 2D-Replay)",
}

#: Was die jeweilige Metrik-Version korrigiert hat. Fehlt sie, sind die
#: betroffenen Zahlen falsch - nicht nur unvollstaendig.
METRICS_FIXES = {
    1: (
        "ADR auf die Rest-HP des Opfers gedeckelt (vorher rund 30 % zu hoch), "
        "Accuracy ohne Granaten und Messer im Nenner, "
        "Counter-Strafing ueber alle Schuesse statt nur ueber Treffer, "
        "Crosshair Placement ueber den Median statt das Mittel"
    ),
}


def _crosshair(data: dict) -> dict:
    """Crosshair-Block des Zielspielers, notfalls leer."""
    cp = (data.get("player") or {}).get("crosshair_placement")
    return cp if isinstance(cp, dict) else {}


def _detect_schema_version(data: dict) -> int:
    """Schema-Version eines Exports ohne Versionsfeld erschliessen.

    Geprueft wird jeweils das Feld, das die Version eingefuehrt hat,
    von neu nach alt.
    """
    kills = data.get("kill_positions") or []
    if any("tk" in kp for kp in kills):
        return 3
    if "utility_positions" in data:
        return 2
    if "median_degrees" in _crosshair(data):
        return 1
    return 0


def _detect_metrics_version(data: dict) -> int:
    """Metrik-Version eines Exports ohne Versionsfeld erschliessen.

    ``median_degrees`` ist der verlaessliche Marker: das Feld entstand mit
    der Crosshair-Korrektur vom 31.08., die nach ADR-, Accuracy- und
    Counter-Strafing-Korrektur (alle 30.08.) kam. Ist es vorhanden, sind
    somit alle vier Korrekturen eingeflossen.

    Der Umkehrschluss gilt nur fuer Exporte mit ausgewerteten Kills - ohne
    Kills gibt es keinen Crosshair-Block. Das ist hier unkritisch, weil
    solche Exporte auch keine der betroffenen Kennzahlen sinnvoll tragen.
    """
    return 1 if "median_degrees" in _crosshair(data) else 0


def export_versions(data: dict) -> tuple[int, int]:
    """(Schema-Version, Metrik-Version) eines Exports.

    Das eingetragene Feld hat Vorrang; fehlt es, wird abgeleitet.
    """
    schema = data.get("schema_version")
    metrics = data.get("metrics_version")
    return (
        int(schema) if isinstance(schema, int) else _detect_schema_version(data),
        int(metrics) if isinstance(metrics, int) else _detect_metrics_version(data),
    )


def export_status(data: dict) -> dict:
    """Bewertet einen Export gegen den aktuellen Stand.

    Liefert neben den Versionen zwei getrennte Listen, weil sie
    unterschiedlich dringend sind: ``missing`` sind Auswertungen, die es
    fuer dieses Match schlicht nicht gibt; ``unreliable`` sind Zahlen, die
    angezeigt werden, aber falsch sind.
    """
    schema, metrics = export_versions(data)
    missing = [SCHEMA_FEATURES[v] for v in sorted(SCHEMA_FEATURES)
               if v > schema]
    unreliable = [METRICS_FIXES[v] for v in sorted(METRICS_FIXES)
                  if v > metrics]
    return {
        "schema_version": schema,
        "metrics_version": metrics,
        "current_schema": SCHEMA_VERSION,
        "current_metrics": METRICS_VERSION,
        "missing": missing,
        "unreliable": unreliable,
        # Nur die Metrik-Version macht einen Export unbrauchbar. Fehlende
        # Felder sind ein Komfort-, kein Richtigkeitsproblem.
        "is_outdated": bool(missing or unreliable),
        "needs_reanalysis": bool(unreliable),
    }


def version_fields() -> dict:
    """Die Versionsfelder, wie sie in jeden neuen Export geschrieben werden."""
    return {
        "schema_version": SCHEMA_VERSION,
        "metrics_version": METRICS_VERSION,
    }


def demo_status(export_dir) -> dict[str, dict]:
    """Je Demo-Datei der Stand ihrer Exporte, fuer Stapellaeufe.

    Zu einer Demo koennen mehrere Exporte gehoeren - je analysiertem
    Spieler einer. Eine Demo gilt nur dann als aktuell, wenn *alle* ihre
    Exporte es sind; andernfalls lohnt der erneute Lauf.

    Zu beachten: eine Neuanalyse erzeugt nur den Export des konfigurierten
    Spielers neu. Exporte, die fuer einen Mitspieler erstellt wurden,
    bleiben auf ihrem Stand, bis sie gezielt neu erzeugt werden.
    """
    from pathlib import Path
    import json

    export_dir = Path(export_dir)
    if not export_dir.exists():
        return {}

    by_demo: dict[str, dict] = {}
    for f in sorted(export_dir.glob("*_coach.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        demo = (data.get("match") or {}).get("demo_file") or ""
        if not demo:
            continue
        st = export_status(data)
        entry = by_demo.setdefault(demo, {
            "exports": 0, "outdated": 0, "needs_reanalysis": False,
        })
        entry["exports"] += 1
        entry["outdated"] += bool(st["is_outdated"])
        entry["needs_reanalysis"] |= st["needs_reanalysis"]

    for entry in by_demo.values():
        entry["is_current"] = entry["outdated"] == 0
    return by_demo
