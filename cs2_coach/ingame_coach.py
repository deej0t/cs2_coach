"""KI-Coach im Spiel-Chat: Fragen mit !coach, Erklaerung des letzten Duells mit !tipp.

Dieselbe KI wie der Web-Chat (/chat, ``ai_provider``), aber mit anderem
Auftrag: Antworten muessen in den Spiel-Chat passen - hoechstens drei kurze
Saetze, sofort umsetzbar. Grundlage sind dieselben Fakten wie im
Tages-Briefing (Trainingsziele und Schwerpunkte aus ``findings``) plus, wenn
ein Duell-Training laeuft, die Lage dort und das letzte Duell.

Die Antwort sieht nur der Fragende: Plugin-Befehl ``coach_chat`` (1.4.0)
schreibt private Chatzeilen. Ohne Plugin faellt sie auf ``say`` zurueck.

Nur fuer den Server-Besitzer (``steam_id``, ``training_admins``): die KI
kennt seine Daten, und jede Anfrage kostet.
"""

from __future__ import annotations

import base64
import json
import re
import textwrap
import time
from datetime import datetime
from pathlib import Path

COMMANDS = {"coach", "frage", "tipp"}
MAX_ANSWER = 330
LINE_WIDTH = 110
MAX_LINES = 4
RETRY_WAITS = (5,)  # im Spiel wartet niemand zweieinhalb Minuten wie beim Briefing
FACTS_TTL = 600  # Spielerfakten zehn Minuten wiederverwenden
LAST_DUEL_FILE = ".coach_last_duel.json"

SYSTEM = """Du bist der persoenliche CS2-Coach eines einzelnen Spielers und antwortest im Spiel-Chat, waehrend er spielt.
- Deutsch, du-Form, hoechstens 3 kurze Saetze, zusammen hoechstens 300 Zeichen.
- Konkret und sofort umsetzbar: was er in der naechsten Runde oder im naechsten Duell anders machen soll.
- Verwende nur die Fakten unten. Erfinde keine Zahlen. Geben die Fakten etwas nicht her, sag das in einem Satz.
- Kein Markdown, keine Aufzaehlung, keine Emojis."""

_facts_cache: dict[str, tuple[float, str]] = {}


# --------------------------------------------------------------------------
# Ausgabe
# --------------------------------------------------------------------------

def chat_line(uid, text: str) -> str:
    return f"coach_chat {uid} {base64.b64encode(text.encode('utf-8')).decode('ascii')}"


def clean(text: str, limit: int = MAX_ANSWER) -> str:
    """Markdown weg, Leerraum zusammen, an einer Satzgrenze auf limit kuerzen."""
    text = re.sub(r"[*#`_>]+", "", str(text or ""))
    text = re.sub(r"^\s*[-•]\s*", "", text, flags=re.M)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > 80 else cut.rstrip() + " ..."


def wrap(text: str, max_lines: int = MAX_LINES) -> list[str]:
    return textwrap.wrap(text, LINE_WIDTH)[:max_lines]


def tell(send, uid, text: str, max_lines: int = MAX_LINES) -> None:
    """Privat an den Spieler; ohne Plugin 1.4.0 oeffentlich per say."""
    lines = wrap(text, max_lines) if "\n" not in text else text.split("\n")
    reply = send(chat_line(uid, "\n".join(lines)))
    if not reply or "coach_chat ok" not in reply[0]:
        from .server_live import _say_commands
        send(*_say_commands(lines))


# --------------------------------------------------------------------------
# Fakten
# --------------------------------------------------------------------------

def player_facts(cfg: dict, config_path: Path, now: float | None = None) -> str:
    """Dieselben Fakten wie im Tages-Briefing (Ziele, Schwerpunkte, letzte Matches)."""
    now = time.time() if now is None else now
    key = str(config_path)
    cached = _facts_cache.get(key)
    if cached and now - cached[0] < FACTS_TTL:
        return cached[1]
    from .briefing import build_facts
    from .training import current_plan

    vault_raw = (cfg.get("obsidian_vault_path") or "").strip()
    exports = []
    if vault_raw:
        for f in sorted((Path(vault_raw) / cfg.get("coach_subfolder", "CS2-Coach") / "exports").glob("*_coach.json")):
            try:
                exports.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
    if not exports:
        facts = "Keine Matchdaten vorhanden."
    else:
        plan = current_plan(exports, Path(config_path).parent / ".training_state.json",
                            datetime.now().strftime("%Y-%m-%d %H:%M"))
        facts = build_facts(exports, plan, "")[0]
    _facts_cache[key] = (now, facts)
    return facts


def save_last_duel(config_path: Path, duel: dict) -> None:
    (Path(config_path).parent / LAST_DUEL_FILE).write_text(json.dumps(duel, ensure_ascii=False), encoding="utf-8")


def load_last_duel(config_path: Path) -> dict | None:
    try:
        return json.loads((Path(config_path).parent / LAST_DUEL_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def duel_facts(duel: dict) -> str:
    """Das letzte Duell als Faktenzeilen fuer die KI (nur Gemessenes)."""
    out = [f"Situation aus dem Match {duel.get('source', '?')} auf {duel.get('map', '?')}: "
           f"Gegner {duel.get('opponent', '?')} mit {duel.get('weapon', '?')}, "
           f"er kam von {duel.get('direction', '?')}.",
           f"Ergebnis im Training: {dict(win='gewonnen', loss='verloren').get(duel.get('outcome'), 'unentschieden')}"
           f" (Versuch {duel.get('tries', 1)})."]
    first = duel.get("first")
    if first:
        out.append("Zuerst getroffen hat: " + ("der Spieler" if first == "you" else "der Gegner") + ".")
    out.append(f"Treffer des Spielers: {duel.get('you_hits', 0)}, davon am Kopf: {duel.get('you_head', 0)}. "
               f"Treffer des Gegners: {duel.get('bot_hits', 0)}.")
    seen = _measure_facts(duel)
    if seen:
        out.append(seen)
    if duel.get("tip"):
        out.append(f"Automatischer Hinweis: {duel['tip']}")
    return "\n".join(out)


def _measure_facts(d: dict) -> str:
    """Messwerte des Plugins als Satz fuer die KI (nur was gemessen wurde)."""
    from .training_server import aim_side
    parts = []
    if "seen_err" in d:
        parts.append(f"Fadenkreuz beim Auftauchen des Gegners {d['seen_err']:.0f} Grad daneben "
                     f"({aim_side(d.get('dpitch', 0), d.get('dyaw', 0))})")
    if "react_ms" in d:
        parts.append(f"erster Schuss {d['react_ms']:.0f} ms nach dem Sehen" if d["react_ms"] >= 0
                     else "vor dem Sehen geschossen (vorgeschossen)")
    if "hit_ms" in d:
        parts.append(f"erster Treffer {d['hit_ms']:.0f} ms nach dem Sehen")
    if "peek_ms" in d:
        parts.append(f"der Gegner sah ihn {d['peek_ms']:.0f} ms frueher" if d["peek_ms"] > 0
                     else f"er sah den Gegner {-d['peek_ms']:.0f} ms frueher")
    if d.get("kill_errs"):
        parts.append("Fadenkreuz 0,5 s vor seinen Kills: " + ", ".join(f"{k:.0f} Grad" for k in d["kill_errs"]))
    return ("Gemessen: " + "; ".join(parts) + ".") if parts else ""


def training_facts(config_path: Path) -> str:
    """Laufendes Training plus Bilanz aller Trainings (wie auf /training).

    Ohne die Bilanz antwortete die KI am 30.10. auf "Wie werde ich besser in
    Duellen auf Mirage?" mit "Dazu liegen mir keine Daten vor".
    """
    from .training_server import hardest, load_sessions, load_state, weapon_label
    lines = []
    state = load_state(config_path)
    if state:
        res = state.get("results", [])
        lines.append(f"Duell-Training laeuft auf {state.get('map')}: Duell {state.get('pos', 0) + 1} von "
                     f"{len(state.get('queue', []))}, Stand {res.count('win')} gewonnen zu {res.count('loss')} "
                     f"verloren, Bot-Stufe {state.get('level')}/5.")
    else:
        lines.append("Gerade laeuft kein Duell-Training.")
    sessions = load_sessions(config_path)
    by_map: dict[str, list[int]] = {}
    for s in sessions:
        won_played = by_map.setdefault(s.get("map") or "?", [0, 0])
        won_played[0] += s["won"]
        won_played[1] += s["played"]
    for m, (won, played) in sorted(by_map.items()):
        lines.append(f"Bisher im Duell-Training auf {m}: {won} von {played} Duellen gewonnen.")
    measured = next((s for s in sessions if s.get("measures", {}).get("seen_n")), None)
    if measured:
        m = measured["measures"]
        lines.append(f"Letztes gemessenes Training ({measured.get('map')}): Median Fadenkreuz beim Auftauchen "
                     f"{m['seen_err']:.1f} Grad"
                     + (f", erster Schuss {m['react_ms']:.0f} ms nach dem Sehen" if m["react_n"] else "") + ".")
    for h in hardest(sessions, limit=3):
        lines.append(f"Oft verloren: {h.get('source')} gegen {h.get('opponent')} mit "
                     f"{weapon_label(h.get('weapon', ''))} ({h['lost']}x verloren, {h['won']}x gewonnen).")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# KI
# --------------------------------------------------------------------------

FAZIT_LIMIT = 520
# Nur die Trainingsdaten, keine Match-Statistik: das erste Fazit (30.10.) nannte als
# "deutlichstes Problem" zu wenige HE-Granaten (0.43 vs 0.31 pro Runde) - ein Ziel aus
# dem Tages-Briefing. Im Training hatte er oft gar keine HE ("Ich kann doch keine
# HE-Granaten nutzen, wenn ich keine zu der Situation habe").
FAZIT_SYSTEM = """Du bist der persoenliche CS2-Coach eines einzelnen Spielers. Er hat gerade ein Training auf dem
Server beendet: Situationen aus seinen eigenen Matches, nachgespielt gegen Bots.
- Deutsch, du-Form, hoechstens 4 kurze Saetze, zusammen hoechstens 480 Zeichen.
- Satz 1: das Ergebnis. Dann das deutlichste Muster bei den Niederlagen - nur aus den Trainingsdaten
  (wer zuerst traf, wer wen zuerst sah, Fadenkreuz beim Auftauchen und vor Kills in Grad, Treffer,
  Waffe des Gegners). Reaktionszeiten nur nennen, nicht als gut oder schlecht bewerten.
- Zum Schluss genau eine Sache, die er in solchen Situationen als Naechstes ueben soll.
- Rate nur zu Ausruestung, die er in der Situation laut Daten wirklich hatte. Keine Ratschlaege zu
  Granaten, Wirtschaft oder Match-Statistik.
- Verwende nur die Fakten, erfinde keine Zahlen. Kein Markdown, keine Aufzaehlung, keine Emojis."""


def session_facts(session: dict) -> str:
    duels = session.get("duels") or []
    won = sum(1 for d in duels if d.get("outcome") == "win")
    from .training_server import MAX_LEVEL
    party = session.get("party") or []
    out = [f"Training ({session.get('mode', 'duell')}) auf {session.get('map')}: {won} von {len(duels)} gewonnen, "
           f"Bot-Stufe am Ende {session.get('level_end')}/{MAX_LEVEL}."
           + (f" Zusammen mit {', '.join(party)} gespielt." if party else "")]
    from .training_server import session_measures
    m = session_measures(duels)
    if m["seen_n"]:
        out.append(f"Median Fadenkreuz beim Auftauchen: {m['seen_err']:.1f} Grad ({m['seen_n']} Duelle).")
    if m["react_n"]:
        out.append(f"Median erster Schuss nach dem Sehen: {m['react_ms']:.0f} ms ({m['react_n']} Duelle).")
    if m["kill_n"]:
        line = f"Median Fadenkreuz 0,5 s vor Kills im Training: {m['kill_err']:.1f} Grad ({m['kill_n']} Kills)"
        if session.get("match_cp") is not None:
            line += f"; in seinen letzten Matches (gleiche Messung): {session['match_cp']:.1f} Grad"
        out.append(line + ".")
    for d in duels:
        res = dict(win="gewonnen", loss="verloren").get(d.get("outcome"), "unentschieden")
        first = {"you": "du zuerst getroffen", "bot": "Gegner zuerst getroffen"}.get(d.get("first"), "")
        out.append(f"- Duell {d.get('n')} ({d.get('source')}, Gegner mit {d.get('weapon')}): {res}"
                   f"{', ' + first if first else ''}, deine Treffer {d.get('you_hits', '?')} (Kopf {d.get('you_head', '?')}),"
                   f" Treffer des Gegners {d.get('bot_hits', '?')}, Versuche {d.get('tries', 1)}"
                   + (f", deine Ausruestung: {', '.join(d['kit'])}" if d.get("kit") else "") + ".")
        seen = _measure_facts(d)
        if seen:
            out.append("  " + seen)
    return "\n".join(out)


def session_fazit(cfg: dict, config_path: Path, session_file: Path, ask_fn=None) -> str | None:
    """KI-Fazit eines beendeten Trainings; wird auch in der Session-Datei gespeichert (fuer /training)."""
    session = json.loads(Path(session_file).read_text(encoding="utf-8"))
    from .training_server import match_crosshair_median
    session["match_cp"] = match_crosshair_median(cfg)
    text = (ask_fn or ask)(cfg, FAZIT_SYSTEM, session_facts(session) + "\n\nSchreibe das Fazit.")
    if not text:
        return None
    text = clean(text, FAZIT_LIMIT)
    session["fazit"] = text
    Path(session_file).write_text(json.dumps(session, ensure_ascii=False, indent=1), encoding="utf-8")
    return text


def ask(cfg: dict, system: str, prompt: str, sleep=time.sleep) -> str | None:
    from .ai_chat import call_gemini, call_ollama
    text = ""
    for wait in (0, *RETRY_WAITS):
        if wait:
            sleep(wait)
        if cfg.get("ai_provider") == "gemini" and cfg.get("gemini_api_key"):
            text = call_gemini(prompt, system, cfg["gemini_api_key"], cfg.get("ai_model") or "gemini-2.0-flash")
        else:
            text = call_ollama(prompt, system, cfg.get("ollama_url", "http://localhost:11434"),
                               cfg.get("ai_model") or "llama3.1:8b")
        if text and not text.startswith("**Fehler"):
            return clean(text)
    print(f"[ingame_coach] KI nicht verfuegbar: {(text or 'leere Antwort')[:120]}", flush=True)
    return None


def handle(cfg: dict, config_path: Path, send, sid: str, uid, cmd: str, arg: str, ask_fn=None) -> None:
    """Einen !coach / !frage / !tipp beantworten (laeuft im Hintergrund-Thread)."""
    from .training_server import _admins
    if sid not in _admins(cfg):
        tell(send, uid, "Der KI-Coach ist dem Server-Besitzer vorbehalten.")
        return
    duel = load_last_duel(config_path)
    if cmd == "tipp":
        if not duel:
            tell(send, uid, "Noch kein Duell gespielt. Starte eins mit !training.")
            return
        prompt = duel_facts(duel) + "\n\nErklaere kurz, was der Spieler in genau diesem Duell besser machen kann."
    elif not arg.strip():
        tell(send, uid, "Frag mich etwas: !coach <Frage>, z. B. !coach wie halte ich A-Rampe? Nach einem Duell: !tipp")
        return
    else:
        prompt = f"Frage des Spielers: {arg.strip()[:300]}"
    tell(send, uid, "Moment, ich schaue nach ...")
    context = (SYSTEM + "\n\nFakten zum Spieler:\n" + player_facts(cfg, config_path)
               + "\n\nGerade auf dem Server:\n" + training_facts(config_path)
               + ("\n\nLetztes Duell im Training:\n" + duel_facts(duel) if duel else ""))
    answer = (ask_fn or ask)(cfg, context, prompt)
    tell(send, uid, answer or "Die KI antwortet gerade nicht - versuch es gleich noch einmal.")
