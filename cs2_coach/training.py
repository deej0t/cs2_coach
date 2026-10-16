"""Trainingsziele mit Startzeitpunkt und Fortschritt.

Die Auswertung sagt, was Siege von Niederlagen trennt. Ob es etwas bringt,
das zu trainieren, sagt sie nicht. Dieses Modul schliesst die Luecke:

- Ziele kommen aus belastbaren Befunden, nicht aus festen Schwellen. Das
  sind zum einen die Granatentypen, deren Wurfrate Siege belastbar von
  Niederlagen trennt (throw_relevance), zum anderen die offenen
  Trainingsziele aus findings.training_priorities().
- Jedes Ziel bekommt beim ersten Auftauchen einen Startzeitpunkt, der in
  .training_state.json neben der Config gespeichert wird. Ab dann wird
  verglichen: Matches seit dem Start gegen Matches davor - die Kennzahl
  selbst und die Siegquote, mit demselben Massstab wie ueberall
  (evidence.py).
- Zielwert ist, was der Spieler in seinen Siegen tut. Das ist erreichbar,
  weil er es schon kann.

Der Nutzer muss nichts tun, kein Ziel setzen und nichts bestaetigen.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .evidence import compare_groups, compare_rates
from .findings import RULES_BY_KEY, SOLID_POSITIVE, evaluate, training_priorities
from .utility_analysis import GRENADE_TYPES, _rates, throw_relevance

#: Ab so vielen Matches seit dem Start wird ein Vergleich gerechnet.
#: Darunter gibt es nur den Stand, keine Aussage.
MIN_AFTER = 8

RECENT = 5


@dataclass
class Goal:
    key: str
    label: str
    unit: str
    higher_is_better: bool
    target: float                 # Mittel in Siegen
    why: str                      # warum es ein Ziel ist, mit Zahlen
    since: str = ""
    recent: float | None = None   # Mittel der letzten RECENT Matches
    matches_since: int = 0
    change: dict | None = None    # Kennzahl seit Start gegen vorher
    note: str = ""
    values: list = field(default_factory=list, repr=False)


def _when(export: dict) -> str:
    m = export.get("match") or {}
    return m.get("datetime") or m.get("date") or ""


def _value(export: dict, key: str) -> float | None:
    """Wert einer Ziel-Kennzahl in einem Match, None wenn unbekannt."""
    if key.startswith("util_"):
        rates = _rates(export)
        return None if rates is None else rates.get(key[len("util_"):])
    for f in evaluate(export.get("player") or {}):
        if f.key == key:
            return f.value
    return None


def _candidate_goals(exports: list[dict]) -> list[Goal]:
    goals: list[Goal] = []

    # 1) Granatentypen, deren Wurfrate belastbar mit Siegen einhergeht.
    rel = throw_relevance(exports)
    for r in rel["rows"]:
        typ = r.key[len("util_"):]
        if r.verdict != SOLID_POSITIVE or typ not in GRENADE_TYPES:
            continue
        goals.append(Goal(
            key=r.key, label=f"{GRENADE_TYPES[typ]} pro Runde", unit="/Runde",
            higher_is_better=True, target=r.mean_win,
            why=(f"In Siegen {r.mean_win} pro Runde, in Niederlagen {r.mean_loss} "
                 f"(belastbar, d={r.effect} [{r.ci_low} … {r.ci_high}])."),
        ))

    # 2) Offene Trainingsziele aus den Befunden. Das allgemeine "Utility pro
    #    Runde" entfaellt, wenn die Granatentypen schon einzeln Ziele sind.
    have_utility = any(g.key.startswith("util_") for g in goals)
    matches = [{"result": (e.get("match") or {}).get("result", ""),
                "player": e.get("player") or {}, "date": _when(e)} for e in exports]
    for t in training_priorities(matches):
        if have_utility and t.key == "utility":
            continue
        rule = RULES_BY_KEY.get(t.key)
        wins = [v for e in exports if (e.get("match") or {}).get("result") == "Sieg"
                for v in [_value(e, t.key)] if v is not None]
        if not wins:
            continue
        goals.append(Goal(
            key=t.key, label=t.label, unit=t.unit,
            higher_is_better=not (rule and rule.lower_is_better),
            target=round(sorted(wins)[len(wins) // 2], 2),
            why=f"Offener Befund ({t.status}), {t.issue_count} von {t.total_matches} Matches auffaellig.",
        ))
    return goals


def _load(state_path: Path) -> dict:
    try:
        return json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"goals": {}}


def current_plan(exports: list[dict], state_path: Path, now: str) -> dict:
    """Aktuelle Ziele mit Fortschritt. Schreibt neue Startzeitpunkte fest.

    now im Format "YYYY-MM-DD HH:MM", wie match.datetime.
    """
    exports = sorted(exports, key=_when)
    state = _load(state_path)
    known = state.setdefault("goals", {})

    goals = _candidate_goals(exports)
    for g in goals:
        entry = known.setdefault(g.key, {"label": g.label, "since": now})
        entry.pop("ended", None)
        g.since = entry["since"]
    for key, entry in known.items():
        if key not in {g.key for g in goals} and "ended" not in entry:
            entry["ended"] = now
    Path(state_path).write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")

    for g in goals:
        pairs = [(_when(e), _value(e, g.key)) for e in exports]
        pairs = [(w, v) for w, v in pairs if v is not None]
        after = [v for w, v in pairs if w >= g.since]
        before = [v for w, v in pairs if w < g.since]
        g.matches_since = len(after)
        g.values = [v for _, v in pairs]
        last = [v for _, v in pairs][-RECENT:]
        g.recent = round(sum(last) / len(last), 2) if last else None
        if len(after) >= MIN_AFTER:
            c = compare_groups(after, before, higher_is_better=g.higher_is_better)
            if c is not None:
                g.change = asdict(c)
        else:
            g.note = (f"{len(after)} Matches seit Start, ab {MIN_AFTER} wird verglichen.")

    # Siegquote seit dem fruehesten Start gegen davor
    starts = [g.since for g in goals]
    win_change = None
    if starts:
        first = min(starts)
        res = [((_when(e) >= first), (e.get("match") or {}).get("result")) for e in exports]
        a = [r for s, r in res if s and r in ("Sieg", "Niederlage")]
        b = [r for s, r in res if not s and r in ("Sieg", "Niederlage")]
        c = compare_rates(a.count("Sieg"), len(a), b.count("Sieg"), len(b))
        win_change = asdict(c) if c else {"n_after": len(a), "note": f"{len(a)} Matches seit Start"}

    return {"goals": goals, "win_change": win_change,
            "ended": {k: v for k, v in known.items() if "ended" in v}}
