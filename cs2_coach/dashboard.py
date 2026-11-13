"""Fokus-Dashboard (Startseite): was heute zaehlt, nicht alles auf einmal.

Ersetzt am 13.11. das alte Dashboard (12 Bloecke, ~4 Bildschirmhoehen).
Dessen Warnungen ("KAST sinkt: 67,9 % vs 72,7 %"), Skill-Benchmarks gegen
"Gold Nova" und Map-"BAN" aus 13 Matches waren rohe Mittelwertvergleiche
mit festen Schwellen - genau das, was CLAUDE.md verbietet. Hier steht nur:

- das letzte Match mit dem Anfang der KI-Nachbesprechung,
- das aktuelle Trainingsziel mit Fortschritt (training.current_plan, die
  gleiche Quelle wie das Tages-Briefing - inklusive evidence-Urteil),
- Form, letzte Matches und Maps als reine Messung ohne Urteilsfarben.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from . import report_ai
from .training import EARLY_KEY, current_plan
from .utility_analysis import early_utility

RECENT = 5
FORM = 20
INTRO_CHARS = 280

_cache: dict = {"key": None, "exports": []}


def _load(cfg: dict) -> tuple[list[Path], list[dict]]:
    """Alle Exporte in Spielreihenfolge, gecacht auf Anzahl + neueste mtime."""
    files = report_ai._exports(cfg)
    key = (len(files), max((f.stat().st_mtime for f in files), default=0))
    if _cache["key"] != key:
        data = []
        for f in files:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            d["_file"] = f.name
            data.append(d)
        _cache.update(key=key, exports=data)
    return files, _cache["exports"]


def _intro(text: str | None) -> str:
    """Die ersten Saetze der Nachbesprechung, an einer Satzgrenze gekuerzt."""
    if not text:
        return ""
    flat = re.sub(r"\s+", " ", re.sub(r"[*_#`]", "", text)).strip()
    if len(flat) <= INTRO_CHARS:
        return flat
    cut = flat[:INTRO_CHARS]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > 80 else cut.rstrip() + " …"


def _row(e: dict) -> dict:
    m, p = e.get("match") or {}, e.get("player") or {}
    early = early_utility(e)
    return {
        "file": e.get("_file", ""),
        "date": (m.get("datetime") or m.get("date") or "")[:16],
        "map": m.get("map", "?"),
        "score": f"{m.get('score_own', '?')}:{m.get('score_enemy', '?')}",
        "result": m.get("result", ""),
        "rating": p.get("rating"),
        "adr": p.get("adr"),
        "kd": p.get("kd"),
        "early": early if early and early[1] >= 1 else None,
    }


def _maps(exports: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for e in exports:
        m = e.get("match") or {}
        s = out.setdefault(m.get("map", "?"), {"map": m.get("map", "?"), "n": 0, "w": 0, "l": 0})
        s["n"] += 1
        s["w"] += m.get("result") == "Sieg"
        s["l"] += m.get("result") == "Niederlage"
    rows = sorted(out.values(), key=lambda s: -s["n"])
    for s in rows:
        s["pct"] = round(100 * s["w"] / s["n"]) if s["n"] else 0
    return rows


def _goal(plan: dict, exports: list[dict]) -> dict | None:
    goals = plan.get("goals") or []
    if not goals:
        return None
    g = goals[0]
    change = g.change or {}
    out = {
        "label": g.label, "unit": g.unit, "target": g.target, "recent": g.recent, "loss": g.loss,
        "since": (g.since or "")[:10], "matches_since": g.matches_since, "note": g.note,
        "verdict": change.get("verdict"), "why": g.why, "early": g.key == EARLY_KEY,
        "fill": 0, "checks": [],
    }
    if g.recent is not None and g.target:
        frac = g.recent / g.target if g.higher_is_better else g.target / max(g.recent, 1e-9)
        out["fill"] = round(min(frac, 1.0) * 100)
    if g.key == EARLY_KEY:
        for e in exports[-RECENT:]:
            c = early_utility(e)
            if c and c[1]:
                out["checks"].append({"k": c[0], "n": c[1], "ok": 100 * c[0] / c[1] >= g.target,
                                      "map": (e.get("match") or {}).get("map", "")})
    out["others"] = [x.label for x in goals[1:]]
    out["achieved"] = [x.label for x in plan.get("achieved") or []]
    return out


def build(cfg: dict, config_path: Path) -> dict:
    """Alles fuer die Startseite. has_data=False ohne Exporte."""
    _, exports = _load(cfg)
    if not exports:
        return {"has_data": False}
    last = exports[-1]
    plan = current_plan([dict(e) for e in exports], Path(config_path).parent / ".training_state.json",
                        datetime.now().strftime("%Y-%m-%d %H:%M"))
    form = [{"result": (e.get("match") or {}).get("result", ""),
             "rating": (e.get("player") or {}).get("rating"),
             "title": f"{(e.get('match') or {}).get('map', '')} "
                      f"{(e.get('match') or {}).get('score_own', '')}:{(e.get('match') or {}).get('score_enemy', '')}"}
            for e in exports[-FORM:]]
    results = [f["result"] for f in form]
    return {
        "has_data": True,
        "total": len(exports),
        "last": _row(last),
        "intro": _intro(report_ai.load(config_path, last.get("_file", ""))),
        "goal": _goal(plan, exports),
        "form": form,
        "form_wins": results.count("Sieg"),
        "form_losses": results.count("Niederlage"),
        "recent": [_row(e) for e in reversed(exports[-RECENT:])],
        "maps": _maps(exports),
    }
