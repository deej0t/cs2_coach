"""Ziel pro Training: die schwaechste der gemessenen Gewohnheiten aus den letzten Trainings.

Jedes Ziel ist ein Kriterium, das pro Duell erfuellt ist oder nicht - so kann
der Spieler es im Spiel zaehlen ("heute 6 von 9"). Ob er sich verbessert, sagt
nicht der Rohunterschied, sondern evidence.compare_rates (Konfidenzintervall),
wie ueberall im Coach.

Die Reaktionszeit ist bewusst kein Ziel: wie frueh die Spotted-Maske anschlaegt,
ist erst nach !eichen bekannt. Die Grenzen (80 u/s, 10 Grad, 250 ms) sind
dieselben wie in den Tipps (training_server.duel_tip).
"""

from __future__ import annotations

from .evidence import SOLID_NEGATIVE, SOLID_POSITIVE, compare_rates

# key: (Text, Feld im Duell, erfuellt?)
GOALS = {
    "stopped": ("Erster Schuss gestoppt (Counter-Strafe)", "first_speed", lambda v: v <= 80),
    "aim": ("Fadenkreuz beim Auftauchen unter 10°", "seen_err", lambda v: v < 10),
    "cover": ("Nicht offen stehen: du siehst ihn zuerst", "peek_ms", lambda v: v < 250),
}
MIN_BEFORE = 8  # so viele gemessene Duelle braucht ein Ziel als Ausgangswert
LOOKBACK = 3    # die letzten so vielen Trainings mit Messwerten


def goal_ok(key: str, duel: dict) -> bool | None:
    """Erfuellt? None = in diesem Duell nicht gemessen (zaehlt nicht)."""
    _, field, test = GOALS[key]
    return None if field not in duel else bool(test(duel[field]))


def _rate(key: str, duels: list[dict]) -> tuple[int, int]:
    marks = [goal_ok(key, d) for d in duels]
    marks = [m for m in marks if m is not None]
    return sum(marks), len(marks)


def pick_goal(sessions: list[dict]) -> dict | None:
    """Ziel mit der niedrigsten Quote in den letzten Trainings (neueste zuerst uebergeben)."""
    measured = [s for s in sessions if any(f in d for d in s.get("duels", []) for _, f, _ in GOALS.values())]
    duels = [d for s in measured[:LOOKBACK] for d in s.get("duels", [])]
    best = None
    for key, (label, _, _) in GOALS.items():
        k, n = _rate(key, duels)
        if n >= MIN_BEFORE and (best is None or k / n < best["before_k"] / best["before_n"]):
            best = {"key": key, "label": label, "before_k": k, "before_n": n}
    return best


def progress(goal: dict | None, duels: list[dict]) -> dict | None:
    """Heute gegen vorher - Urteil nur mit Konfidenzintervall."""
    if not goal:
        return None
    k, n = _rate(goal["key"], duels)
    cmp = compare_rates(k, n, goal["before_k"], goal["before_n"])
    verdict = {SOLID_POSITIVE: "besser", SOLID_NEGATIVE: "schlechter"}.get(cmp.verdict if cmp else "", "offen")
    return {"k": k, "n": n, "pct": round(k * 100 / n) if n else None,
            "before_pct": round(goal["before_k"] * 100 / goal["before_n"]), "verdict": verdict}


def progress_text(goal: dict | None, prog: dict | None) -> str:
    if not goal or not prog or not prog["n"]:
        return ""
    tail = {"besser": "messbar besser als vorher", "schlechter": "messbar schlechter als vorher"}.get(
        prog["verdict"], "noch kein belastbarer Unterschied")
    return (f"Ziel '{goal['label']}': heute {prog['k']}/{prog['n']} ({prog['pct']} %), "
            f"vorher {prog['before_pct']} % - {tail}.")
