"""Dieses Match gegen die eigenen letzten Matches (Idee aus Leetifys Matchseite).

Reine Messung: Wert, eigener Median und Prozentrang in der eigenen
Historie, dazu die Granaten beider Teams. Hier wird nichts bewertet - ob
eine Kennzahl Siege von Niederlagen trennt, sagt allein die Effektstaerke
auf /coaching (findings.build_relevance). Leetify faerbt Zellen nach festen
Schwellen gruen oder rot; genau das soll hier nicht passieren.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from .findings import RULES_BY_KEY, evaluate
from .obsidian import export_sort_key

LOOKBACK = 30      # wie Leetifys "Last 30 Matches"
MIN_HISTORY = 5    # darunter kein Prozentrang - fuenf Werte sind keine Verteilung
TOP = 5

# Bedeutung und Berechnung je Kennzahl, fuer den Tooltip. Wo Leetify gleich
# benannte Zahlen anders rechnet, steht es dabei - sonst vergleicht man
# 2,3 Grad mit 9,4 Grad und haelt das eine fuer falsch.
EXPLAIN = {
    "crosshair_placement": (
        "Wie weit dein Fadenkreuz kurz vor dem Kill vom Gegner weg war. Niedriger ist besser.",
        "Median ueber alle Kills: Winkel 0,5 s vor dem Kill, von deinem Blick zu seinen Fuessen. "
        "Leetify misst beim ersten Sehen - die Zahlen sind nicht vergleichbar.",
    ),
    "first_sight": (
        "Wie gut du vorgezielt hast: der Winkel in dem Moment, in dem ein Gegner neu in Sicht kommt. "
        "Niedriger ist besser.",
        "Median ueber alle Sichtungen. Sicht = Radar-Sichtbarkeit aus der Demo, die leicht verzoegert "
        "anschlaegt - deshalb niedriger als Leetifys Crosshair Placement.",
    ),
    "counter_strafe": (
        "Anteil deiner Schuesse im Stillstand.",
        "Alle Schuesse mit Schusswaffen, Stillstand = unter 80 u/s. Leetify zaehlt nur Gewehre "
        "bei gesehenem Gegner (am 12.11. trotzdem 85 gegen 87 %).",
    ),
    "adr": (
        "Schaden pro Runde.",
        "Jeder Treffer gedeckelt auf die restlichen HP des Opfers.",
    ),
    "kd": ("Kills geteilt durch Tode.", "Ueber das ganze Match."),
    "kast": (
        "Anteil der Runden mit Kill, Assist, Ueberleben oder getradetem Tod.",
        "Ueber alle Runden des Matches.",
    ),
    "utility": (
        "Geworfene Granaten pro Runde.",
        "Alle geworfenen Granaten geteilt durch die Runden des Matches.",
    ),
    "survival": ("Anteil der Runden, die du ueberlebt hast.", "Ueber alle Runden des Matches."),
    "accuracy": (
        "Anteil deiner Schuesse, die getroffen haben.",
        "Nur Schusswaffen - Granaten und Messer zaehlen nicht. Schrotflinten lesen sich zu hoch.",
    ),
}


@dataclass
class Row:
    key: str
    label: str
    unit: str
    value: float
    median: float | None       # eigener Median der letzten Matches
    n: int                     # so viele Matches mit diesem Wert in der Historie
    better_pct: int | None     # in so viel Prozent davon war dieses Match besser
    outcome_driven: bool
    meaning: str
    how: str

    @property
    def unusual(self) -> int:
        """Abstand vom eigenen Normalfall (0 = genau typisch, 50 = Rekord)."""
        return abs(self.better_pct - 50) if self.better_pct is not None else -1


def _values(player: dict) -> dict[str, float]:
    return {f.key: f.value for f in evaluate(player, include_observed=True)}


def _better_pct(value: float, history: list[float], lower_is_better: bool) -> int:
    """Anteil der Historie, den dieses Match uebertrifft - Gleichstand zaehlt halb."""
    score = 0.0
    for h in history:
        if h == value:
            score += 0.5
        elif (value < h) == lower_is_better:
            score += 1
    return round(score / len(history) * 100)


def compare(data: dict, history: list[dict]) -> list[Row]:
    """Jede Kennzahl dieses Matches gegen die Exporte in *history* (aelteste zuerst)."""
    now = _values(data.get("player") or {})
    past: dict[str, list[float]] = {}
    for h in history[-LOOKBACK:]:
        for k, v in _values(h.get("player") or {}).items():
            past.setdefault(k, []).append(v)
    rows = []
    for key, value in now.items():
        rule = RULES_BY_KEY[key]
        ref = past.get(key, [])
        enough = len(ref) >= MIN_HISTORY
        meaning, how = EXPLAIN.get(key, ("", ""))
        rows.append(Row(
            key=key, label=rule.label, unit=rule.unit, value=value,
            median=round(median(ref), 2) if enough else None, n=len(ref),
            better_pct=_better_pct(value, ref, rule.lower_is_better) if enough else None,
            outcome_driven=rule.outcome_driven, meaning=meaning, how=how,
        ))
    return rows


def top(rows: list[Row], k: int = TOP) -> list[Row]:
    """Die Werte, die am weitesten von deinem Normalfall weg sind - nach oben wie unten."""
    ranked = [r for r in rows if r.better_pct is not None]
    ranked.sort(key=lambda r: -r.unusual)
    return ranked[:k]


def _sides(data: dict) -> dict[str, str] | None:
    """steam_id -> "own"/"enemy". None, wenn sich die Teams nicht sicher trennen lassen."""
    board = data.get("scoreboard") or []
    me = next((p for p in board if p.get("is_target")), None)
    if me is None:
        return None
    # Schema 5: Startseite je Spieler
    if all(p.get("team") in ("T", "CT") for p in board):
        return {p["steam_id"]: "own" if p["team"] == me["team"] else "enemy" for p in board}
    # Aeltere Exporte: Gegner sind, gegen wen du Duelle hattest. Nur wenn
    # das genau fuenf ergibt - sonst fehlt einer und zaehlte als Mitspieler.
    enemies = {d.get("steam_id") for d in data.get("duel_matrix") or []}
    ids = {p.get("steam_id") for p in board}
    if len(board) != 10 or len(enemies) != 5 or not enemies <= ids:
        return None
    return {p["steam_id"]: "enemy" if p["steam_id"] in enemies else "own" for p in board}


def team_utility(data: dict) -> dict | None:
    """Geworfene Granaten beider Teams (Leetify: "190 gegen 86").

    Pro Spieler steht nur utility_per_round im Scoreboard; mal Runden ergibt
    die Zahl wieder (Rundungsfehler hoechstens 0,005 x 30 Runden).
    """
    rounds = (data.get("match") or {}).get("total_rounds") or 0
    sides = _sides(data)
    if not rounds or sides is None:
        return None
    total = {"own": 0, "enemy": 0}
    mine = 0
    for p in data.get("scoreboard") or []:
        n = round((p.get("utility_per_round") or 0) * rounds)
        total[sides[p["steam_id"]]] += n
        if p.get("is_target"):
            mine = n
    top_n = max(total.values()) or 1
    return {
        "own": total["own"], "enemy": total["enemy"], "mine": mine, "rounds": rounds,
        "own_per_round": round(total["own"] / rounds, 1),
        "enemy_per_round": round(total["enemy"] / rounds, 1),
        "own_width": round(total["own"] / top_n * 100),
        "enemy_width": round(total["enemy"] / top_n * 100),
    }


def history_for(export_dir: Path, filename: str) -> list[dict]:
    """Die bis zu LOOKBACK Exporte vor *filename*, aelteste zuerst."""
    files = sorted(Path(export_dir).glob("*_coach.json"), key=export_sort_key)
    names = [f.name for f in files]
    if filename not in names:
        return []
    out = []
    for f in files[max(0, names.index(filename) - LOOKBACK):names.index(filename)]:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def build(export_dir: Path, filename: str, data: dict) -> dict:
    """Alles fuer das Partial _partials/match_compare.html."""
    rows = compare(data, history_for(export_dir, filename))
    return {
        "rows": rows,
        "top": top(rows),
        "n": max((r.n for r in rows), default=0),
        "utility": team_utility(data),
    }
