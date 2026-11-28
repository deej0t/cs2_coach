"""Match-Seite: Mission, Seitenbilanz, Rundenstreifen und "Was nimmst du mit?" fuer EIN Export.

Reine Aufbereitung vorhandener Felder (round_timeline, utility_positions, side_split) plus
der gecachten Storylines (moments_page-Index). Das Missionsziel kommt aus
training._early_goal (evidence-Urteil ueber alle Exports) - hier wird nichts neu bewertet.
"""

from __future__ import annotations

import json
from pathlib import Path

from .utility_analysis import _counted_rounds, _early_rounds

_TARGET: dict = {}      # (export_dir, Anzahl, neueste mtime) -> Ziel in % oder None


def early_target(export_dir: Path | None) -> float | None:
    """Ziel der Mission "fruehe Granate" (Median deiner Siege), nur wenn belastbar - sonst None."""
    from .training import _early_goal
    if not export_dir or not Path(export_dir).exists():
        return None
    files = list(Path(export_dir).glob("*_coach.json"))
    if not files:
        return None
    sig = (str(export_dir), len(files), max(f.stat().st_mtime for f in files))
    if sig not in _TARGET:
        exports = []
        for f in files:
            try:
                exports.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        g = _early_goal(exports)
        _TARGET.clear()
        _TARGET[sig] = g.target if g else None
    return _TARGET[sig]


def mission(data: dict, target: float | None) -> dict | None:
    """Fruehe Granate in den gezaehlten Full Buys dieses Matches (None ohne Felder/Runden)."""
    rounds = _counted_rounds(data)
    if not rounds:
        return None
    early = _early_rounds(data)
    cells = [{"round": int(r.get("round", 0)), "ok": int(r.get("round", 0)) in early, "side": r.get("side") or ""}
             for r in rounds]
    k, n = sum(c["ok"] for c in cells), len(cells)
    pct = round(100 * k / n)
    return {"k": k, "n": n, "pct": pct, "target": target,
            "ok": None if target is None else pct >= target, "rounds": cells}


def strip(data: dict) -> list[dict]:
    """Rundenstreifen des ganzen Matches fuer ui.round_strip (Luecke beim Seitenwechsel)."""
    out, prev = [], None
    for r in data.get("round_timeline") or []:
        side = r.get("side") or ""
        if out and prev and side and side != prev:
            out[-1]["gap"] = True
        prev = side or prev
        k = int(r.get("player_kills") or 0)
        out.append({"won": bool(r.get("won")), "side": side, "gap": False, "kills": k,
                    "title": f"Runde {r.get('round')} · {side} · {'gewonnen' if r.get('won') else 'verloren'}"
                             + (f" · {k} Kill(s)" if k else "") + (" · früh gestorben" if r.get("died_early") else "")})
    return out


def sides(data: dict) -> list[dict]:
    """CT/T: Runden gewonnen/gespielt und deine Kills/Tode je Seite."""
    sp = (data.get("player") or {}).get("side_split") or {}
    out = []
    for side in ("CT", "T"):
        rs = [r for r in data.get("round_timeline") or [] if r.get("side") == side]
        if not rs:
            continue
        key = side.lower()
        out.append({"side": side, "won": sum(bool(r.get("won")) for r in rs), "n": len(rs),
                    "kills": sp.get(f"{key}_kills"), "deaths": sp.get(f"{key}_deaths")})
    return out


def takeaways(stories: list[dict], mis: dict | None) -> list[dict]:
    """Bis zu drei Punkte: teuerster Tod, wertvollster Kill (aus den Storylines), Hausaufgabe."""
    out = []
    death = next((s for s in stories if s.get("kind") == "death"), None)
    kill = next((s for s in stories if s.get("kind") == "kill"), None)
    if death:
        out.append({"tone": "bad", "title": death["verdict"], "text": death.get("trigger", ""), "ref": death})
    if kill:
        out.append({"tone": "good", "title": kill["verdict"], "text": kill.get("trigger", ""), "ref": kill})
    if mis and mis.get("target") is not None:
        if mis["ok"]:
            out.append({"tone": "good", "title": "Mission erfüllt – weiter so",
                        "text": f"{mis['k']} von {mis['n']} Full Buys mit früher Granate (Ziel {mis['target']:.0f} %)."})
        else:
            out.append({"tone": "focus", "title": "Nächstes Match: früher Granate werfen",
                        "text": f"Nur {mis['k']} von {mis['n']} Full Buys mit Granate in den ersten 35 s, "
                                f"Ziel {mis['target']:.0f} %. Üben: !granaten"})
    return out[:3]
