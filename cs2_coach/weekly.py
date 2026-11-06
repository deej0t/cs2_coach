"""Wochenrueckblick: die Vorwoche gegen die 4 Wochen davor, montags aufs Handy.

Gleiche Regeln wie die Nachbesprechung (report_ai.py): Fakten macht der Coach
(Medianwerte je Befund, Trainings der Woche, Verdikt aus training_effect), die
KI formuliert nur und wird verworfen, wenn sie Zahlen erfindet. Verschickt nach
dem ersten Sync ab Montag (Discord + Telegram), einmal pro Woche
(report_ai/week-<Jahr>-W<KW>.txt ist zugleich die Marke).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median

SYSTEM = """Du bist der persoenliche CS2-Coach eines Spielers und schreibst seinen Wochenrueckblick.

- Deutsch, per du, 4 bis 6 Saetze, hoechstens 800 Zeichen, kein Markdown, keine Aufzaehlung.
- Nur was in den FAKTEN steht; jede Zahl genau so wie dort, als Ziffern.
- Vergleiche die Woche mit den 4 Wochen davor: was hat sich bewegt, was nicht?
- Sag nur dann, dass das Training wirkt, wenn bei "Ob das Server-Training im Match wirkt" "messbar besser" steht. Diese Zeilen sind KEIN Fokus.
- Schliesse mit genau dem einen FOKUS FUER DIESE WOCHE aus den Fakten (steht keiner da: mit keinem).
- Keine Floskeln."""


def week_id(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _values(data: dict) -> dict[str, tuple[str, float, str]]:
    from .findings import evaluate
    return {f.key: (f.label, f.value, f.unit) for f in evaluate(data.get("player") or {})}


def facts(cfg: dict, config_path: Path, today: date) -> tuple[str, str] | None:
    """(Fakten, Fokus-Label) fuer die Woche vor 'today' - None ohne Matches in der Woche."""
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
    wins = sum(1 for d in week if (d.get("match") or {}).get("result") == "Sieg")
    lines = [f"Woche {start:%d.%m.}-{end - timedelta(days=1):%d.%m.}: {len(week)} Matches, {wins} gewonnen."]
    worst = None
    vals_w = [_values(d) for d in week]
    vals_b = [_values(d) for d in before]
    for key in sorted({k for v in vals_w for k in v}):
        a = [v[key][1] for v in vals_w if key in v]
        b = [v[key][1] for v in vals_b if key in v]
        label, _, unit = next(v[key] for v in vals_w if key in v)
        line = f"- {label}: Median {median(a):.2f}{unit}"
        if len(b) >= 3:
            line += f" (4 Wochen davor: {median(b):.2f}{unit})"
            rel = (median(a) - median(b)) / abs(median(b) or 1) * (-1 if key in LOWER_BETTER else 1)
            if key not in OUTCOME and (worst is None or rel < worst[0]):
                worst = (rel, label)
        lines.append(line + (" [haengt am Rundengewinn]" if key in OUTCOME else "") + ".")
    sessions = load_sessions(config_path)
    in_week = [s for s in sessions if start.isoformat() <= str(s.get("started", ""))[:10] < end.isoformat()]
    lines.append(f"Trainings auf dem Server: {len(in_week)}, {sum(s['played'] for s in in_week)} Runden, "
                 f"{sum(s['won'] for s in in_week)} gewonnen.")
    verdicts = {"solid_positive": "messbar besser", "solid_negative": "messbar schlechter",
                "solid_negligible": "kein Unterschied", "undecided": "noch offen"}
    eff = analyse(cfg, sessions)
    for r in eff.get("rows", []):
        lines.append(f"Ob das Server-Training im Match wirkt ({r['name']}): "
                     + (verdicts.get(r["cmp"].verdict, "noch offen") if r["cmp"] else "zu wenige Matches") + ".")
    focus = worst[1] if worst and worst[0] < 0 else ""
    if focus:
        lines.append(f"FOKUS FUER DIESE WOCHE: {focus}.")
    return "\n".join(lines), focus


def run(cfg: dict, config_path: Path, log=print, today: date | None = None, ask=None, send=True) -> bool:
    """Rueckblick der Vorwoche erzeugen und verschicken, falls noch nicht geschehen."""
    from .briefing import ask_ai
    from .report_ai import DIR, _send_text
    today = today or datetime.now().date()
    wid = week_id(today - timedelta(days=7))
    out = Path(config_path).parent / DIR / f"week-{wid}.txt"
    if out.exists():
        return False
    got = facts(cfg, config_path, today)
    if got is None:
        return False
    text = (ask or ask_ai)(cfg, got[0], log=log, system=SYSTEM,
                           instruction="Schreibe den Wochenrueckblick.", max_len=1000)
    if not text:
        return False
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding="utf-8")
    log(f"Wochenrueckblick {wid} erzeugt")
    if send:
        _send_text(cfg, config_path, f"**Wochenrueckblick {wid}**\n{text}", log)
    return True
