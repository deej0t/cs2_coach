"""Wochenrueckblick: die Vorwoche gegen die 4 Wochen davor, montags aufs Handy.

Festes Layout wie das Briefing (briefing_view): Zahlen, Pfeile, Fokus macht der
Coach; die KI schreibt nur 1-2 Saetze Einleitung und wird verworfen, wenn sie
Zahlen erfindet - dann geht der Rueckblick ohne sie raus. Der erste Entwurf
liess die KI alles formulieren: eine Aufzaehlung aller Werte, "sehr bescheiden"
(6.11.). Verschickt nach dem ersten Sync ab Montag, einmal pro Woche
(report_ai/week-<Jahr>-W<KW>.txt ist zugleich die Marke).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median

SYSTEM = """Du bist der persoenliche CS2-Coach eines Spielers. Schreibe die Einleitung zu seinem Wochenrueckblick.

- Deutsch, per du, 1 bis 2 kurze Saetze, hoechstens 220 Zeichen, kein Markdown.
- Das Wichtigste der Woche in eigenen Worten; Zahlen nur genau so, wie sie in den FAKTEN stehen, als Ziffern.
- Keine Aufzaehlung von Werten (die stehen schon darunter), keine Floskeln."""
MAX_INTRO = 260
MIN_CHANGE = 0.02  # unter 2 % Unterschied zum Vorher gilt als gleich geblieben


def week_id(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _num(v: float, unit: str) -> str:
    s = f"{v:.2f}" if abs(v) < 10 and unit != "%" else f"{v:.1f}"
    return s.replace(".", ",") + ("°" if unit == "°" else " %" if unit == "%" else "")


def summary(cfg: dict, config_path: Path, today: date) -> dict | None:
    """Zahlen der Vorwoche gegen die 4 Wochen davor - None ohne Matches in der Woche."""
    from .findings import evaluate
    from .report_ai import LOWER_BETTER, OUTCOME, _exports
    from .training_effect import _match_time, analyse
    from .training_server import load_sessions
    start = today - timedelta(days=today.weekday() + 7)  # Montag der Vorwoche
    end = start + timedelta(days=7)
    week, before = [], []
    for f in _exports(cfg):
        t = _match_time(f)
        if t is None or not (start - timedelta(days=28) <= t.date() < end):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        (week if t.date() >= start else before).append(data)
    if not week:
        return None
    vals = lambda ds: [{f.key: f for f in evaluate(d.get("player") or {})} for d in ds]  # noqa: E731
    vw, vb = vals(week), vals(before)
    better, worse, result = [], [], []
    for key in sorted({k for v in vw for k in v}):
        a = [v[key].value for v in vw if key in v]
        b = [v[key].value for v in vb if key in v]
        f = next(v[key] for v in vw if key in v)
        if key in OUTCOME:
            result.append(f"{f.label} {_num(median(a), f.unit)}")
            continue
        if len(b) < 3 or not median(b):
            continue
        rel = (median(a) - median(b)) / abs(median(b)) * (-1 if key in LOWER_BETTER else 1)
        if abs(rel) >= MIN_CHANGE:
            (better if rel > 0 else worse).append(
                (abs(rel), f"{f.label} {_num(median(b), f.unit)} → {_num(median(a), f.unit)}", f))
    sessions = load_sessions(config_path)
    in_week = [s for s in sessions if start.isoformat() <= str(s.get("started", ""))[:10] < end.isoformat()]
    eff = analyse(cfg, sessions)
    verdicts = {"solid_positive": "messbar besser", "solid_negative": "messbar schlechter",
                "solid_negligible": "kein Unterschied"}
    effect = [f"{r['name']}: {verdicts.get(r['cmp'].verdict, 'noch offen')}" for r in eff.get("rows", [])
              if r["cmp"] and r["cmp"].verdict in verdicts]
    focus = max(worse, key=lambda x: x[0])[2] if worse else None
    return {"start": start, "end": end - timedelta(days=1), "matches": len(week),
            "wins": sum(1 for d in week if (d.get("match") or {}).get("result") == "Sieg"),
            "before": len(before),
            "before_wins": sum(1 for d in before if (d.get("match") or {}).get("result") == "Sieg"),
            "better": [t for _, t, _ in sorted(better, key=lambda x: -x[0])],
            "worse": [t for _, t, _ in sorted(worse, key=lambda x: -x[0])], "result": result,
            "trainings": len(in_week), "rounds": sum(s["played"] for s in in_week),
            "rounds_won": sum(s["won"] for s in in_week),
            "effect": effect, "effect_open": not effect and bool(eff.get("since")),
            "focus": (focus.label, focus.advice) if focus else None}


def render(s: dict, intro: str | None) -> str:
    pct = lambda w, n: f"{round(w * 100 / n)} %" if n else "–"  # noqa: E731
    lines = [f"**📅 Wochenrückblick {s['start']:%d.%m.}–{s['end']:%d.%m.}**"]
    if intro:
        lines.append(f"_{intro}_")
    lines.append("")
    lines.append(f"🎮 **{s['matches']} Matches · {s['wins']} Siege ({pct(s['wins'], s['matches'])})**"
                 + (f"  ·  4 Wochen davor: {pct(s['before_wins'], s['before'])}" if s["before"] else ""))
    if s["better"]:
        lines.append("📈 **Besser:** " + " · ".join(s["better"][:3]))
    if s["worse"]:
        lines.append("📉 **Schlechter:** " + " · ".join(s["worse"][:3]))
    if not s["better"] and not s["worse"]:
        lines.append("➖ Deine Werte sind gegenüber den 4 Wochen davor gleich geblieben.")
    if s["result"]:
        lines.append("🏁 Ergebnis-Werte (hängen am Rundengewinn): " + " · ".join(s["result"]))
    if s["trainings"]:
        lines.append(f"🏋️ **Training:** {s['trainings']}× auf dem Server · "
                     f"{s['rounds_won']}/{s['rounds']} Runden gewonnen")
    if s["effect"]:
        lines.append("🔬 Wirkt das Training im Match? " + " · ".join(s["effect"]))
    elif s["effect_open"]:
        lines.append("🔬 Ob das Training im Match wirkt: noch zu wenige Matches")
    if s["focus"]:
        lines.append("")
        lines.append(f"🎯 **Fokus diese Woche: {s['focus'][0]}**")
        if s["focus"][1]:
            lines.append(s["focus"][1])
    return "\n".join(lines)


def _facts(s: dict) -> str:
    return "\n".join([f"{s['matches']} Matches, {s['wins']} gewonnen.",
                      "Besser: " + ("; ".join(s["better"]) or "nichts"),
                      "Schlechter: " + ("; ".join(s["worse"]) or "nichts"),
                      f"{s['trainings']} Trainings auf dem Server.",
                      f"Fokus: {s['focus'][0] if s['focus'] else 'keiner'}."])


def run(cfg: dict, config_path: Path, log=print, today: date | None = None, ask=None, send=True) -> bool:
    """Rueckblick der Vorwoche erzeugen und verschicken, falls noch nicht geschehen."""
    from .briefing import ask_ai
    from .report_ai import DIR, _send_text
    today = today or datetime.now().date()
    wid = week_id(today - timedelta(days=7))
    out = Path(config_path).parent / DIR / f"week-{wid}.txt"
    if out.exists():
        return False
    s = summary(cfg, config_path, today)
    if s is None:
        return False
    intro = (ask or ask_ai)(cfg, _facts(s), log=log, system=SYSTEM,
                            instruction="Schreibe die Einleitung.", max_len=MAX_INTRO)
    text = render(s, intro)
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding="utf-8")
    log(f"Wochenrueckblick {wid} erzeugt")
    if send:
        _send_text(cfg, config_path, text, log)
    return True
