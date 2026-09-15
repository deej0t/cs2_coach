"""Tests fuer die Versionierung der Exporte.

Der Kern ist nicht das Schreiben der Zahl, sondern das Erschliessen fuer die
bereits vorhandenen Exporte: sie tragen kein Versionsfeld, sind aber
ueberwiegend aktuell. Wer sie pauschal als veraltet meldet, schickt den
Nutzer 63 Mal durch eine Neuanalyse, die nichts aendert.
"""

from __future__ import annotations

from cs2_coach.export_version import (
    METRICS_VERSION,
    SCHEMA_VERSION,
    export_status,
    export_versions,
    version_fields,
)


def make(*, median=True, utility=False, tick=False, **extra) -> dict:
    """Export-Fragment mit genau den Feldern, die die Version ausmachen."""
    kp = {"t": "k", "x": 1.0, "y": 2.0, "r": 1}
    if tick:
        kp["tk"] = 12345
    data = {
        "player": {"crosshair_placement": {"avg_degrees": 6.5}},
        "kill_positions": [kp],
    }
    if median:
        data["player"]["crosshair_placement"]["median_degrees"] = 4.1
    if utility:
        data["utility_positions"] = [{"t": "s", "x": 1.0, "y": 2.0, "r": 1}]
    data.update(extra)
    return data


# ── Ableitung fuer Exporte ohne Versionsfeld ─────────────────────────

def test_erschliesst_schema_aus_den_feldern():
    assert export_versions(make(median=False))[0] == 0
    assert export_versions(make())[0] == 1
    assert export_versions(make(utility=True))[0] == 2
    assert export_versions(make(utility=True, tick=True))[0] == 3


def test_median_degrees_belegt_die_metrik_korrekturen():
    """Das Feld entstand nach ADR-, Accuracy- und Counter-Strafing-Fix."""
    assert export_versions(make(median=True))[1] == 1
    assert export_versions(make(median=False))[1] == 0


def test_fehlender_crosshair_block_faellt_nicht_um():
    assert export_versions({"player": {}}) == (0, 0)
    assert export_versions({}) == (0, 0)
    assert export_versions({"player": {"crosshair_placement": None}}) == (0, 0)


def test_eingetragene_version_schlaegt_die_ableitung():
    """Sonst koennte ein spaeter entferntes Feld die Datei zurueckstufen."""
    data = make(median=False, schema_version=3, metrics_version=1)
    assert export_versions(data) == (3, 1)


# ── Bewertung ────────────────────────────────────────────────────────

def test_aktueller_export_ist_nicht_veraltet():
    st = export_status(make(utility=True, tick=True))
    assert st["is_outdated"] is False
    assert st["needs_reanalysis"] is False
    assert st["missing"] == []
    assert st["unreliable"] == []


def test_fehlende_felder_sind_kein_richtigkeitsproblem():
    """Schema 1: Utility-Karte und Ticks fehlen, die Zahlen stimmen aber."""
    st = export_status(make())
    assert st["is_outdated"] is True
    assert st["needs_reanalysis"] is False
    assert len(st["missing"]) == 2
    assert st["unreliable"] == []


def test_alte_metrik_verlangt_neuanalyse():
    st = export_status(make(median=False))
    assert st["needs_reanalysis"] is True
    assert "ADR" in st["unreliable"][0]


def test_version_fields_entspricht_dem_aktuellen_stand():
    assert version_fields() == {
        "schema_version": SCHEMA_VERSION,
        "metrics_version": METRICS_VERSION,
    }


def test_neuer_export_traegt_die_version():
    """Ein frisch gebauter Export darf sich nie selbst als veraltet melden."""
    st = export_status({**version_fields(), **make(utility=True, tick=True)})
    assert st["is_outdated"] is False
