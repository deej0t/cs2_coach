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
LOWER_BETTER = {"crosshair_placement"}

SYSTEM = """Du bist der persoenliche CS2-Coach eines Spielers und schreibst die Nachbesprechung zu EINEM Match.

- Deutsch, per du, direkt wie ein Trainer im Gespraech. 4 bis 6 Saetze, hoechstens 700 Zeichen. Kein Markdown, keine Aufzaehlung.
- Nur was in den FAKTEN steht. Jede Zahl, die du nennst, muss genau so in den FAKTEN stehen - immer als Ziffern, nie ausgeschrieben. Erfinde keine Spielstaende oder Rundennummern.
- Vergleiche mit SEINEM Normalwert (Median der Matches davor), nicht mit Profis: was war in diesem Match anders als sonst - besser und schlechter?
- K/D, ADR, KAST und Survival steigen einfach mit gewonnenen Runden. Erklaere damit nicht das Ergebnis, nenne sie hoechstens als Folge.
- Schliesse mit dem FOKUS FUERS NAECHSTE MATCH aus den Fakten - genau diesem, mit seinem Tipp in eigenen Worten.
- "Besser als sonst" und "Schlechter als sonst" stehen in den Fakten. Lobe nichts, was dort als schlechter steht, und umgekehrt.
- Keine Floskeln ("Kopf hoch", "weiter so", "insgesamt", "konzentriere dich voll"). Beginne nicht mit dem Ergebnis, der Map oder "Dein".
- Nutze den Rundenverlauf (Serien, Seiten, fruehe Tode, Opening-Duelle), damit man merkt, dass es um GENAU dieses Match geht.
- Klingt nicht wie die FRUEHEREN NACHBESPRECHUNGEN: andere Satzanfaenge, anderer Aufbau, andere Schwerpunkte."""


def _fmt(v: float) -> str:
    return f"{v:.2f}" if abs(v) < 10 else f"{v:.1f}"


def _timeline(data: dict) -> list[str]:
    """Rundenverlauf in Worten: Seiten, laengste Serien, fruehe Tode, Mehrfach-Kills."""
    rounds = data.get("round_timeline") or []
    if not rounds:
        return []
    out = []
    for side in ("CT", "T"):
        rs = [r for r in rounds if r.get("side") == side]
        if rs:
            out.append(f"Als {side}: {sum(1 for r in rs if r.get('won'))} von {len(rs)} Runden gewonnen.")
    best = worst = cur = 0
    last = None
    for r in rounds:
        won = bool(r.get("won"))
        cur = cur + 1 if won == last else 1
        last = won
        if won:
            best = max(best, cur)
        else:
            worst = max(worst, cur)
    out.append(f"Laengste Siegesserie {best}, laengste Niederlagenserie {worst} Runden.")
    early = sum(1 for r in rounds if r.get("died_early"))
    multi = sum(1 for r in rounds if (r.get("player_kills") or 0) >= 3)
    out.append(f"Frueh gestorben in {early} Runden, 3+ Kills in {multi} Runden.")
    return out


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
    better, worse = [], []  # (relative Abweichung, Finding)
    for f in evaluate(data.get("player") or {}):
        ref = past.get(f.key) or []
        base = f" (dein Median der letzten {len(ref)}: {_fmt(median(ref))}{f.unit})" if len(ref) >= 3 else ""
        if len(ref) >= 3 and f.key not in OUTCOME and median(ref):
            rel = (f.value - median(ref)) / abs(median(ref)) * (-1 if f.key in LOWER_BETTER else 1)
            if abs(rel) >= 0.05:
                (better if rel > 0 else worse).append((abs(rel), f))
        note = " [haengt am Rundengewinn]" if f.key in OUTCOME else ""
        lines.append(f"- {f.label}: {_fmt(f.value)}{f.unit}{base}, {sev.get(f.severity, f.severity)}{note}."
                     + (f" Tipp: {f.advice}" if f.is_issue and f.key not in OUTCOME else ""))
    p = data.get("player") or {}
    if "opening_kills" in p:
        lines.append(f"Opening-Duelle: {p.get('opening_kills', 0)} gewonnen, {p.get('opening_deaths', 0)} verloren; "
                     f"Trade-Kills {p.get('trade_kills', 0)}.")
    lines += _timeline(data)
    if better:
        lines.append("Besser als sonst: " + ", ".join(f.label for _, f in sorted(better, key=lambda x: -x[0])) + ".")
    if worse:
        lines.append("Schlechter als sonst: " + ", ".join(f.label for _, f in sorted(worse, key=lambda x: -x[0])) + ".")
    # Fokus legt der Coach fest, nicht die KI: groesster Rueckschritt, sonst das erste Trainingsziel
    focus = focus_of(worse)
    if focus is not None:
        lines.append(f"FOKUS FUERS NAECHSTE MATCH: {focus.label} - {focus.advice}")
    elif goals:
        lines.append(f"FOKUS FUERS NAECHSTE MATCH: {goals[0]}")
    if goals:
        lines.append("Trainingsziele (belegt aus seinen Matches): " + "; ".join(goals) + ".")
    return "\n".join(lines)


def instruction(previous: list[str]) -> str:
    """Fruehere Texte gehen in die Anweisung, nicht in die Fakten - ihre Zahlen gehoeren zu
    anderen Matches und duerfen die Zahlenpruefung nicht bestehen."""
    text = "Schreibe die Nachbesprechung zu diesem Match."
    if previous:
        text += ("\n\nFRUEHERE NACHBESPRECHUNGEN (andere Matches - nicht so klingen, keine Zahlen daraus):\n"
                 + "\n---\n".join(previous))
    return text


def focus_of(worse: list) -> object | None:
    """Fokus = groesster Rueckschritt gegenueber dem eigenen Median (ohne rundenabhaengige Werte)."""
    return max(worse, key=lambda x: x[0])[1] if worse else None


def worse_of(data: dict, history: list[dict]) -> list:
    from .findings import evaluate
    past: dict[str, list[float]] = {}
    for h in history[-LOOKBACK:]:
        for f in evaluate(h.get("player") or {}):
            past.setdefault(f.key, []).append(f.value)
    out = []
    for f in evaluate(data.get("player") or {}):
        ref = past.get(f.key) or []
        if len(ref) >= 3 and f.key not in OUTCOME and median(ref):
            rel = (f.value - median(ref)) / abs(median(ref)) * (-1 if f.key in LOWER_BETTER else 1)
            if rel <= -0.05:
                out.append((-rel, f))
    return out


FOCUS_FILE = "focus.json"


def load_focus(config_path: Path) -> dict:
    """Fokus aus dem neuesten Match: {export, map, key, label, advice} - fuer Training und Briefing."""
    try:
        return json.loads((Path(config_path).parent / DIR / FOCUS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _notify(cfg: dict, config_path: Path, data: dict, text: str, focus, log) -> None:
    """Nachbesprechung aufs Handy: Discord (briefing_webhook/discord_webhook) und Telegram."""
    m = data.get("match") or {}
    head = f"**Nachbesprechung {m.get('map', '')} {m.get('score_own', '')}:{m.get('score_enemy', '')}** ({m.get('date', '')})"
    task = ""
    if focus is not None:
        bsp = "de_" + str(m.get("map", "")).lower()
        short = bsp.removeprefix("de_")
        task = (f"\n\nHausaufgabe: {focus.label} - auf dem Server `!granaten {short}`."
                if focus.key == "utility" else
                f"\n\nHausaufgabe: {focus.label} - auf dem Server `!training {short}` "
                "(deine verlorenen Duelle aus diesem Match kommen zuerst).")
    _send_text(cfg, config_path, f"{head}\n{text}{task}", log)


def _send_text(cfg: dict, config_path: Path, msg: str, log) -> None:
    """Text an Discord (briefing_webhook/discord_webhook) und Telegram - Fehler nur ins Log."""
    hook = cfg.get("briefing_webhook") or cfg.get("discord_webhook") or ""
    try:
        import requests
        if hook:
            requests.post(hook, json={"content": msg[:1900]}, timeout=15)
        token = (cfg.get("telegram_bot_token") or "").strip()
        if token:
            from . import telegram
            chat = telegram.find_chat_id(token, config_path)
            if chat:
                telegram.send(token, chat, msg)  # send() wandelt selbst in HTML um
    except Exception as e:  # noqa: BLE001 - die Nachbesprechung steht trotzdem auf der Seite
        log(f"Nicht verschickt: {e}")


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


def generate_pending(cfg: dict, config_path: Path, log=print, limit: int = PER_SYNC, ask=None,
                     notify: bool = True) -> int:
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
    previous = [t for t in (load(config_path, g.name) for g in reversed(files)) if t][:2]
    for f in todo:
        if f.name not in datas:
            continue
        idx = [g.name for g in files].index(f.name)
        history = [datas[g.name] for g in files[:idx] if g.name in datas]
        text = ask(cfg, facts(datas[f.name], history, goals), log=log, system=SYSTEM,
                   instruction=instruction(previous), max_len=MAX_LEN)
        if not text:
            continue
        out = path_for(config_path, f.name)
        out.parent.mkdir(exist_ok=True)
        out.write_text(text, encoding="utf-8")
        made += 1
        previous = [text, *previous][:2]
        if f.name == files[-1].name:  # nur das neueste Match bestimmt die Hausaufgabe
            focus = focus_of(worse_of(datas[f.name], history))
            m = datas[f.name].get("match") or {}
            (out.parent / FOCUS_FILE).write_text(json.dumps(
                {"export": f.name, "map": "de_" + str(m.get("map", "")).lower(),
                 **({"key": focus.key, "label": focus.label, "advice": focus.advice} if focus else {})}),
                encoding="utf-8")
            if notify:
                _notify(cfg, config_path, datas[f.name], text, focus, log)
        log(f"KI-Nachbesprechung: {f.name}")
    return made
