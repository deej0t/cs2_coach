"""Bringt das Server-Training etwas im echten Match?

Jedes Training misst bisher nur sich selbst. Hier werden die Matches seit dem
ersten Training in zwei Gruppen geteilt: mit Training in den 24 Stunden davor
und ohne. Verglichen werden die Gewohnheiten, die das Training uebt und die
der Parser im Match genauso misst: Fadenkreuz vor Kills (Median, weniger ist
besser), Counter-Strafing (% gestoppter Schuesse) und Granaten pro Runde.

Bewusst nur Matches SEIT dem ersten Training: ein Vorher/Nachher ueber den
ganzen Bestand wuerde jeden Langzeittrend dem Training zuschreiben. Statistik
wie ueberall ueber evidence.compare_groups (3 Metriken -> comparisons=3).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from .evidence import MIN_PER_GROUP, compare_groups

WINDOW = timedelta(hours=24)
METRICS = (  # key, Name, Einheit, hoeher ist besser
    ("crosshair", "Fadenkreuz vor Kills", "°", False),
    ("strafe", "Counter-Strafing", "%", True),
    ("utility", "Granaten pro Runde", "", True),
)


def _match_time(path: Path) -> datetime | None:
    parts = path.stem.split("_")  # <Datum>_<Map>_<Score>_<HHMM>_coach
    try:
        return datetime.strptime(f"{parts[0]} {parts[-2]}", "%Y-%m-%d %H%M")
    except (ValueError, IndexError):
        return None


def _values(export: dict) -> dict[str, float]:
    from .utility_analysis import _rates
    player = export.get("player") or {}
    out = {}
    cp = player.get("crosshair_placement") or {}
    if cp.get("kills_analyzed") and cp.get("median_degrees") is not None:
        out["crosshair"] = float(cp["median_degrees"])
    cs = player.get("counter_strafe_pct", player.get("counter_strafe_score"))
    if cs is not None:
        out["strafe"] = float(cs)
    rates = _rates(export)
    if rates:
        out["utility"] = rates["total"]
    return out


def matches(cfg: dict) -> list[tuple[datetime, dict]]:
    vault = (cfg.get("obsidian_vault_path") or "").strip()
    if not vault:
        return []
    out = []
    for f in (Path(vault) / cfg.get("coach_subfolder", "CS2-Coach") / "exports").glob("*_coach.json"):
        t = _match_time(f)
        if t is None:
            continue
        try:
            out.append((t, _values(json.loads(f.read_text(encoding="utf-8")))))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda m: m[0])


def training_times(sessions: list[dict]) -> list[datetime]:
    out = []
    for s in sessions:
        try:
            out.append(datetime.fromisoformat(str(s.get("started", ""))))
        except ValueError:
            continue
    return sorted(out)


def analyse(cfg: dict, sessions: list[dict]) -> dict:
    trained = training_times([s for s in sessions if s.get("played")])
    if not trained:
        return {"since": None, "with": 0, "without": 0, "rows": []}
    since = trained[0]
    groups = {True: [], False: []}
    for t, vals in matches(cfg):
        if t < since:
            continue
        groups[any(t - WINDOW <= x < t for x in trained)].append(vals)
    rows = []
    for key, name, unit, higher in METRICS:
        a = [v[key] for v in groups[True] if key in v]
        b = [v[key] for v in groups[False] if key in v]
        cmp = compare_groups(a, b, higher_is_better=higher, comparisons=len(METRICS))
        rows.append({"name": name, "unit": unit, "n_with": len(a), "n_without": len(b),
                     "mean_with": sum(a) / len(a) if a else None, "mean_without": sum(b) / len(b) if b else None,
                     "cmp": cmp, "missing": max(0, MIN_PER_GROUP - len(a)) + max(0, MIN_PER_GROUP - len(b))})
    return {"since": since, "with": len(groups[True]), "without": len(groups[False]), "rows": rows}
