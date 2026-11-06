"""KI-Nachbesprechung pro Match: was war in DIESEM Match anders als sonst?

Der Coach-Report (coach.py) ist aus Textbausteinen gebaut und wiederholt sich
("es kommen zu oft die selben Saetze", 6.11.). Hier schreibt die KI (Gemini,
sonst Ollama) 4-6 Saetze - aber nur aus Fakten: die Befunde dieses Matches
(findings.evaluate) neben dem eigenen Median der 10 Matches davor, dazu die
Trainingsziele. Wie beim Briefing wird ein Text verworfen, der Zahlen ohne
Grundlage enthaelt oder zu lang ist; dann bleibt es beim Bausteine-Report.

Erzeugt im Hintergrund nach jedem Sync (die KI braucht Sekunden bis Minuten,
bei 503 mit Wiederholung), gespeichert in report_ai/<export>.txt neben der
Config; die Match-Seite zeigt ihn, sobald er da ist.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import median

DIR = "report_ai"
LOOKBACK = 10
MAX_LEN = 900
PER_SYNC = 5  # so viele neue Matches pro Sync (jede Anfrage kostet bis zu 2,5 min bei 503)
OUTCOME = {"adr", "kd", "kast", "survival"}  # steigen mit dem Rundengewinn

SYSTEM = """Du bist der persoenliche CS2-Coach eines Spielers und schreibst die Nachbesprechung zu EINEM Match.

- Deutsch, per du, direkt wie ein Trainer im Gespraech. 4 bis 6 Saetze, hoechstens 700 Zeichen. Kein Markdown, keine Aufzaehlung.
- Nur was in den FAKTEN steht. Jede Zahl, die du nennst, muss genau so in den FAKTEN stehen.
- Vergleiche mit SEINEM Normalwert (Median der Matches davor), nicht mit Profis: was war in diesem Match anders als sonst - besser und schlechter?
- K/D, ADR, KAST und Survival steigen einfach mit gewonnenen Runden. Erklaere damit nicht das Ergebnis, nenne sie hoechstens als Folge.
- Schliesse mit genau einer konkreten Sache fuer das naechste Match, bevorzugt aus den Trainingszielen.
- Keine Floskeln ("Kopf hoch", "weiter so", "insgesamt"). Beginne nicht mit dem Ergebnis oder der Map."""


def _fmt(v: float) -> str:
    return f"{v:.2f}" if abs(v) < 10 else f"{v:.1f}"


def facts(data: dict, history: list[dict], goals: list[str]) -> str:
    """Faktenblock fuer die KI. history = Exporte VOR diesem Match, aelteste zuerst."""
    from .findings import evaluate
    m = data.get("match") or {}
    lines = [f"Match: {m.get('map', '?')}, {m.get('result', '?')} {m.get('score_own', '?')}:{m.get('score_enemy', '?')}, "
             f"{m.get('total_rounds', '?')} Runden."]
    past: dict[str, list[float]] = {}
    for h in history[-LOOKBACK:]:
        for f in evaluate(h.get("player") or {}):
            past.setdefault(f.key, []).append(f.value)
    sev = {"ok": "im gruenen Bereich", "warning": "auffaellig", "critical": "deutlich schwach"}
    for f in evaluate(data.get("player") or {}):
        ref = past.get(f.key) or []
        base = f" (dein Median der letzten {len(ref)}: {_fmt(median(ref))}{f.unit})" if len(ref) >= 3 else ""
        note = " [haengt am Rundengewinn]" if f.key in OUTCOME else ""
        lines.append(f"- {f.label}: {_fmt(f.value)}{f.unit}{base}, {sev.get(f.severity, f.severity)}{note}."
                     + (f" Tipp: {f.advice}" if f.is_issue and f.key not in OUTCOME else ""))
    if goals:
        lines.append("Trainingsziele (belegt aus seinen Matches): " + "; ".join(goals) + ".")
    return "\n".join(lines)


def path_for(config_path: Path, export_name: str) -> Path:
    return Path(config_path).parent / DIR / (Path(export_name).stem + ".txt")


def load(config_path: Path, export_name: str) -> str | None:
    try:
        return path_for(config_path, export_name).read_text(encoding="utf-8")
    except OSError:
        return None


def _exports(cfg: dict) -> list[Path]:
    from .obsidian import export_sort_key
    vault = (cfg.get("obsidian_vault_path") or "").strip()
    if not vault:
        return []
    return sorted((Path(vault) / cfg.get("coach_subfolder", "CS2-Coach") / "exports").glob("*_coach.json"),
                  key=export_sort_key)


def generate_pending(cfg: dict, config_path: Path, log=print, limit: int = PER_SYNC, ask=None) -> int:
    """KI-Nachbesprechung fuer die neuesten Matches ohne eine. Gibt die Zahl der neuen zurueck."""
    from .briefing import ask_ai
    from .findings import training_priorities
    ask = ask or ask_ai
    files = _exports(cfg)
    todo = [f for f in reversed(files) if load(config_path, f.name) is None][:limit]
    if not todo:
        return 0
    datas = {}
    for f in files:
        try:
            datas[f.name] = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    ordered = [datas[f.name] for f in files if f.name in datas]
    try:
        goals = [t.label for t in training_priorities(ordered)]
    except Exception:  # noqa: BLE001 - Ziele sind nur Beiwerk
        goals = []
    made = 0
    for f in todo:
        if f.name not in datas:
            continue
        idx = [g.name for g in files].index(f.name)
        history = [datas[g.name] for g in files[:idx] if g.name in datas]
        text = ask(cfg, facts(datas[f.name], history, goals), log=log, system=SYSTEM,
                   instruction="Schreibe die Nachbesprechung zu diesem Match.", max_len=MAX_LEN)
        if not text:
            continue
        out = path_for(config_path, f.name)
        out.parent.mkdir(exist_ok=True)
        out.write_text(text, encoding="utf-8")
        made += 1
        log(f"KI-Nachbesprechung: {f.name}")
    return made
