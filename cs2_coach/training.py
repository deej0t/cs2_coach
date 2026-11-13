"""Trainingsziele mit Startzeitpunkt und Fortschritt.

Die Auswertung sagt, was Siege von Niederlagen trennt. Ob es etwas bringt,
das zu trainieren, sagt sie nicht. Dieses Modul schliesst die Luecke:

- Ziele kommen aus belastbaren Befunden, nicht aus festen Schwellen - und
  nur, wenn die Kennzahl Siege belastbar von Niederlagen trennt. Das
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
from statistics import median
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .evidence import compare_groups, compare_rates
from .findings import RULES_BY_KEY, SOLID_POSITIVE, build_relevance, evaluate, training_priorities
from .utility_analysis import GRENADE_TYPES, _rates, early_utility, throw_relevance

#: Ab so vielen Matches seit dem Start wird ein Vergleich gerechnet.
#: Darunter gibt es nur den Stand, keine Aussage.
MIN_AFTER = 8

RECENT = 5

#: Darunter ist die Quote eines Matches Zufall (1 von 2 = 50 %).
MIN_EARLY_ROUNDS = 4
EARLY_KEY = "early_util"


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
    loss: float | None = None     # Mittel bzw. Median in Niederlagen, zur Einordnung
    values: list = field(default_factory=list, repr=False)


def _when(export: dict) -> str:
    m = export.get("match") or {}
    return m.get("datetime") or m.get("date") or ""


def _value(export: dict, key: str) -> float | None:
    """Wert einer Ziel-Kennzahl in einem Match, None wenn unbekannt."""
    if key == EARLY_KEY:
        counts = early_utility(export)
        if counts is None or counts[1] < MIN_EARLY_ROUNDS:
            return None
        return round(100 * counts[0] / counts[1], 1)
    if key.startswith("util_"):
        rates = _rates(export)
        return None if rates is None else rates.get(key[len("util_"):])
    for f in evaluate(export.get("player") or {}):
        if f.key == key:
            return f.value
    return None


def _early_goal(exports: list[dict]) -> Goal | None:
    """Ziel "fruehe Granate im Full Buy", wenn die Quote Siege belastbar trennt."""
    def rates(result: str) -> list[float]:
        return [v for e in exports if (e.get("match") or {}).get("result") == result
                for v in [_value(e, EARLY_KEY)] if v is not None]
    wins, losses = rates("Sieg"), rates("Niederlage")
    c = compare_groups(wins, losses)
    if c is None or c.verdict != SOLID_POSITIVE:
        return None
    return Goal(
        key=EARLY_KEY, label="Frühe Granate im Full Buy", unit="%", higher_is_better=True,
        target=round(median(wins), 1), loss=round(median(losses), 1),
        why=(f"Anteil deiner Full-Buy-Runden mit Granate in den ersten 35 s (solange du lebst): "
             f"in Siegen {c.mean_a:.0f} %, in Niederlagen {c.mean_b:.0f} % "
             f"(belastbar, d={c.effect} [{c.ci_low} … {c.ci_high}])."),
    )


def _candidate_goals(exports: list[dict]) -> list[Goal]:
    goals: list[Goal] = []

    # 1) Granatentypen, deren Wurfrate belastbar mit Siegen einhergeht.
    # 0) Fruehe Granate im Full Buy. Die Granatenzahl je Match steigt zum
    #    grossen Teil, weil man Runden gewinnt (13.11.: der Gegner wirft in
    #    deinen Siegen 1,05 statt 1,44 pro Runde). Ist die saubere Kennzahl
    #    belastbar, ersetzt sie die Zaehl-Ziele je Granatentyp.
    early = _early_goal(exports)
    if early is not None:
        goals.append(early)

    rel = throw_relevance(exports) if early is None else {"rows": []}
    for r in rel["rows"]:
        typ = r.key[len("util_"):]
        if r.verdict != SOLID_POSITIVE or typ not in GRENADE_TYPES:
            continue
        goals.append(Goal(
            key=r.key, label=f"{GRENADE_TYPES[typ]} pro Runde", unit="/Runde",
            higher_is_better=True, target=r.mean_win, loss=r.mean_loss,
            why=(f"In Siegen {r.mean_win} pro Runde, in Niederlagen {r.mean_loss} "
                 f"(belastbar, d={r.effect} [{r.ci_low} … {r.ci_high}])."),
        ))

    # 2) Offene Trainingsziele aus den Befunden. Das allgemeine "Utility pro
    #    Runde" entfaellt, wenn die Granatentypen schon einzeln Ziele sind.
    have_utility = any(g.key.startswith("util_") or g.key == EARLY_KEY for g in goals)
    matches = [{"result": (e.get("match") or {}).get("result", ""),
                "player": e.get("player") or {}, "date": _when(e)} for e in exports]
    # Nur was Siege belastbar von Niederlagen trennt (12.11.: Accuracy stand auf Platz 2,
    # obwohl sie in Siegen und Niederlagen gleich war, 24.2 gegen 24.2 %).
    separates = {r.key for r in build_relevance(matches) if r.verdict == SOLID_POSITIVE}
    for t in training_priorities(matches):
        if have_utility and t.key == "utility":
            continue
        if t.key not in separates:
            continue
        rule = RULES_BY_KEY.get(t.key)
        wins = [v for e in exports if (e.get("match") or {}).get("result") == "Sieg"
                for v in [_value(e, t.key)] if v is not None]
        if not wins:
            continue
        losses = sorted(v for e in exports if (e.get("match") or {}).get("result") == "Niederlage"
                        for v in [_value(e, t.key)] if v is not None)
        goals.append(Goal(
            key=t.key, label=t.label, unit=t.unit,
            higher_is_better=not (rule and rule.lower_is_better),
            target=round(sorted(wins)[len(wins) // 2], 2),
            loss=round(losses[len(losses) // 2], 2) if losses else None,
            why=f"In {t.issue_count} von {t.total_matches} Matches auffaellig.",
        ))
    return goals


def gap(g: Goal) -> float:
    """Relativer Abstand der letzten Matches zum Zielwert (0 = erreicht)."""
    if g.recent is None or not g.target:
        return 0.0
    d = (g.target - g.recent) if g.higher_is_better else (g.recent - g.target)
    return max(d / abs(g.target), 0.0)


def is_achieved(g: Goal) -> bool:
    """Erreicht = seit dem Start belastbar besser UND die letzten Matches am Zielwert.

    Wunsch 12.11.: das Flash-Ziel (0.64/Runde) stand weiter im Briefing, obwohl
    die letzten 5 Matches 1.14 hatten und es seit dem Start belastbar besser war.
    Faellt der Wert wieder unter das Ziel, ist es automatisch wieder aktiv; der
    Startzeitpunkt bleibt, damit der Vergleich weiter stimmt.
    """
    return bool(g.change and g.change.get("verdict") == SOLID_POSITIVE and g.recent is not None and gap(g) == 0)


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

    active, achieved = [], []
    for g in goals:
        (achieved if is_achieved(g) else active).append(g)
    # Groesster Abstand zum Zielwert zuerst - das erste Ziel ist auch der Fokus der Nachbesprechung
    active.sort(key=lambda g: -gap(g))
    return {"goals": active, "achieved": achieved, "win_change": win_change,
            "ended": {k: v for k, v in known.items() if "ended" in v}}
