"""Taegliches Trainings-Briefing: was, wie, wann - und ob es wirkt.

Ablauf: Fakten berechnen (training.py, evidence.py), Darstellung mit
fester Struktur (briefing_view.py), Versand an Discord/Telegram und als
Notiz in den Vault. Die KI schreibt seit 2026-10-16 nur noch ein, zwei
einleitende Saetze ohne Zahlen; vorher formulierte sie das ganze Briefing
und es sah jeden Tag anders aus.

Die KI rechnet nichts. Sie bekommt fertige Zahlen samt Urteil und darf
nur diese verwenden. Das ist kein Detail: dieses Projekt hat mehrfach
Ratschlaege aus Rauschen gegeben ("Tilt-Effekt erkannt", "bester Tag:
Montag" aus 5 Matches). Ein Sprachmodell, das frei formuliert, wuerde
genau das wieder tun. Deshalb:

- Der Systemprompt verbietet Zahlen und Ursachen, die nicht in den Fakten
  stehen, und verlangt "noch offen", wo das Urteil offen ist.
- Enthaelt die Einleitung eine Zahl, die in den Fakten nicht vorkommt,
  oder ist sie zu lang, wird sie verworfen; das Briefing geht dann ohne
  Einleitung raus. Ebenso bei Fehlern der KI-Schnittstelle (call_gemini und
  call_ollama geben dann "**Fehler:** ..." zurueck, statt zu werfen).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .briefing_view import render
from .training import RECENT, _when, current_plan

SYSTEM = """Du bist ein CS2-Trainer. Schreibe die Einleitung fuer das heutige Trainings-Briefing eines einzelnen Spielers.

- Deutsch, per du, persoenlich und motivierend, 1-2 kurze Saetze, hoechstens 220 Zeichen.
- Sag, worauf er heute den Fokus legen soll - nur Ziele, die in den FAKTEN stehen.
- Keine Zahlen. Keine Ursachen, die nicht in den FAKTEN stehen. Behaupte keinen Fortschritt, wenn dort "noch offen" oder "zu wenige Matches" steht.
- Kein Markdown, keine Ueberschrift, keine Aufzaehlung, keine Begruessungsformel wie "Hallo"."""

#: Laengste akzeptierte Einleitung. Laengere Antworten halten sich nicht
#: an die Vorgabe und werden verworfen.
MAX_INTRO = 400


def _main_map(exports: list[dict]) -> str:
    """Meistgespielte Map der letzten zehn Matches, ohne "de_"."""
    names = [((e.get("match") or {}).get("map") or "").lower().removeprefix("de_")
             for e in exports[-10:]]
    names = [n for n in names if n]
    return max(set(names), key=names.count) if names else "mirage"


def _num(x) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".") if isinstance(x, float) else str(x)


def build_facts(exports: list[dict], plan: dict, last_match: str) -> tuple[str, str]:
    """Faktentext fuer die KI und neuester Match-Zeitpunkt."""
    def when(e):
        m = e.get("match") or {}
        return m.get("datetime") or m.get("date") or ""

    exports = sorted(exports, key=when)
    newest = when(exports[-1]) if exports else last_match
    if last_match:
        new = [e for e in exports if when(e) > last_match]
        title = f"Neue Matches seit dem letzten Briefing: {len(new)}"
    else:
        # Erstes Briefing: sonst stuende hier die gesamte Historie als "neu".
        new = exports[-3:]
        title = "Letzte Matches (erstes Briefing):"

    lines = ["FAKTEN", "", "Trainingsziele (Zielwert = was der Spieler in seinen Siegen tut):"]
    if not plan["goals"]:
        lines.append("- Keine offenen Trainingsziele.")
    for g in plan["goals"]:
        per = f" - etwa eine pro {_num(round(1 / g.target, 1))} Runden" if g.unit == "/Runde" and g.target else ""
        lines.append(f"- {g.label}: Zielwert {_num(g.target)}{g.unit}{per}. "
                     f"Letzte {RECENT} Matches: {_num(g.recent)}{g.unit}. Grund: {g.why}")
        if g.change:
            c = g.change
            verdict = {"solid_positive": "belastbar besser", "solid_negative": "belastbar schlechter",
                       "solid_negligible": "belegt unveraendert"}.get(c["verdict"], "noch offen")
            lines.append(f"  Fortschritt seit {g.since}: {_num(c['mean_a'])} gegen vorher {_num(c['mean_b'])} "
                         f"({c['n_a']} gegen {c['n_b']} Matches) - {verdict}.")
        else:
            lines.append(f"  Fortschritt seit {g.since}: zu wenige Matches ({g.note})")
    w = plan.get("win_change")
    if w and "verdict" in w:
        verdict = {"solid_positive": "belastbar hoeher", "solid_negative": "belastbar niedriger"}.get(
            w["verdict"], "noch offen")
        lines.append(f"Siegquote seit Start der Ziele: {_num(w['mean_a'])} % gegen vorher "
                     f"{_num(w['mean_b'])} % - {verdict}.")
    elif w:
        lines.append(f"Siegquote seit Start der Ziele: zu wenige Matches ({w.get('note', '')}).")

    lines += ["", title]
    for e in new[-8:]:
        m, p = e.get("match") or {}, e.get("player") or {}
        rounds = m.get("total_rounds") or 0
        he = sum(1 for u in e.get("utility_positions") or [] if u.get("t") == "h")
        mo = sum(1 for u in e.get("utility_positions") or [] if u.get("t") == "m")
        lines.append(f"- {when(e)} {m.get('map')} {m.get('score_own')}:{m.get('score_enemy')} "
                     f"{m.get('result')}, Rating {p.get('rating')}, ADR {p.get('adr')}, "
                     f"HE {he} und Molotovs {mo} in {rounds} Runden")
    if not new:
        lines.append("- Keine. Valve stellt Demos mit 2-3 Tagen Verzoegerung bereit.")

    return "\n".join(lines), newest


_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _unknown_numbers(text: str, facts: str) -> list[str]:
    """Zahlen im KI-Text, die in den Fakten nicht vorkommen (einstellige ausgenommen)."""
    known = {n.replace(",", ".") for n in _NUM.findall(facts)}
    return [n for n in _NUM.findall(text)
            if n.replace(",", ".") not in known and not (n.isdigit() and len(n) == 1)]


#: Wartezeiten zwischen KI-Versuchen (Sekunden). Gemini antwortete am
#: 2026-10-16 zweimal hintereinander mit 503 (ueberlastet), kurz danach
#: wieder normal - ein einzelner Versuch reicht nicht.
RETRY_WAITS = (30, 120)


def ask_ai(cfg: dict, facts: str, log=print, sleep=None) -> str | None:
    """KI-Text oder None, wenn nicht verfuegbar oder nicht an die Fakten gebunden."""
    import time

    from .ai_chat import call_gemini, call_ollama

    sleep = sleep or time.sleep
    prompt = facts + "\n\nSchreibe die Einleitung fuer heute."
    text = ""
    for wait in (0, *RETRY_WAITS):
        if wait:
            log(f"KI nicht verfuegbar ({text[:80]}), neuer Versuch in {wait} s.")
            sleep(wait)
        if cfg.get("ai_provider") == "gemini" and cfg.get("gemini_api_key"):
            text = call_gemini(prompt, SYSTEM, cfg["gemini_api_key"],
                               cfg.get("ai_model") or "gemini-2.0-flash")
        else:
            text = call_ollama(prompt, SYSTEM, cfg.get("ollama_url", "http://localhost:11434"),
                               cfg.get("ai_model") or "llama3.1:8b")
        if text and not text.startswith("**Fehler"):
            break
    if not text or text.startswith("**Fehler"):
        log(f"KI nicht verfuegbar: {(text or 'leere Antwort')[:120]}")
        return None
    text = text.strip()
    if len(text) > MAX_INTRO:
        log(f"KI-Text verworfen, zu lang ({len(text)} Zeichen).")
        return None
    unknown = _unknown_numbers(text, facts)
    if unknown:
        log(f"KI-Text verworfen, Zahlen ohne Grundlage: {unknown[:6]}")
        return None
    return text.strip()


def _chunks(text: str, size: int = 1900) -> list[str]:
    out, cur = [], ""
    for para in text.split("\n\n"):
        if len(cur) + len(para) + 2 > size and cur:
            out.append(cur)
            cur = ""
        cur = (cur + "\n\n" + para) if cur else para
    if cur:
        out.append(cur)
    return [c[:size] for c in out]


def _channels(cfg: dict, config_path: Path, post, telegram_post, log) -> list:
    """(Name, Versandfunktion) je eingerichtetem Kanal."""
    out = []
    webhook = cfg.get("briefing_webhook") or cfg.get("discord_webhook") or ""
    if webhook:
        def discord(text):
            nonlocal post
            if post is None:
                import requests
                post = requests.post
            for part in _chunks(text):
                try:
                    r = post(webhook, json={"content": part}, timeout=15)
                except Exception as e:  # noqa: BLE001 - naechster Durchlauf versucht es erneut
                    log(f"Discord nicht erreichbar: {e}")
                    return False
                if getattr(r, "status_code", 204) >= 400:
                    log(f"Discord lehnt ab: HTTP {r.status_code}")
                    return False
            return True
        out.append(("discord", discord))

    token = (cfg.get("telegram_bot_token") or "").strip()
    if token:
        from . import telegram
        chat = telegram.find_chat_id(token, config_path)
        if not chat:
            # Kein Fehler, der einen erneuten Versuch rechtfertigt - sonst
            # liefe alle 15 Minuten die KI, bis der Nutzer /start schickt.
            log("Telegram: noch keine Nachricht an den Bot - bitte dem Bot einmal /start schicken.")
        else:
            def tg(text):
                ok = telegram.send(token, chat, text, post=telegram_post)
                if not ok:
                    log("Telegram lehnt ab oder ist nicht erreichbar.")
                return ok
            out.append(("telegram", tg))
    return out


def run(cfg: dict, config_path: Path, send: bool = False, force: bool = False,
        now: datetime | None = None, log=print, post=None, telegram_post=None,
        use_ai: bool = True) -> str | None:
    """Briefing erzeugen; mit send=True an Discord und in den Vault.

    Hoechstens einmal am Tag (ausser force), damit ein Neustart des
    Containers kein zweites Briefing ausloest.
    """
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    state_path = Path(config_path).parent / ".briefing_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if send and not force and state.get("last_sent") == today:
        log("Briefing fuer heute wurde schon verschickt.")
        return None

    # Rohwert pruefen: Path("") ist ".", also das Arbeitsverzeichnis.
    vault_raw = (cfg.get("obsidian_vault_path") or "").strip()
    vault = Path(vault_raw)
    sub = cfg.get("coach_subfolder", "CS2-Coach")
    exports = []
    for f in (sorted((vault / sub / "exports").glob("*_coach.json")) if vault_raw else []):
        try:
            exports.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue

    plan = current_plan(exports, Path(config_path).parent / ".training_state.json",
                        now.strftime("%Y-%m-%d %H:%M"))
    facts, newest = build_facts(exports, plan, state.get("last_match", ""))
    # Ohne KI fuer die Vorschau in der WebUI: die KI braucht mit
    # Wiederholungen bis zu zweieinhalb Minuten.
    intro = ask_ai(cfg, facts, log=log) if use_ai else None
    last = state.get("last_match", "")
    ordered = sorted(exports, key=_when)
    new = [e for e in ordered if _when(e) > last] if last else ordered[-3:]
    text = render(plan, ordered, new, first=not last, now=now, main_map=_main_map(ordered),
                  intro=intro, link=(cfg.get("public_url") or "").strip())
    body = text

    if not send:
        return text

    if vault_raw:
        notes = vault / sub / "Briefings"
        notes.mkdir(parents=True, exist_ok=True)
        (notes / f"{today}.md").write_text(
            f"---\ndate: {today}\ntags: [cs2-briefing]\n---\n\n{body}\n\n"
            f"<details><summary>Fakten</summary>\n\n```\n{facts}\n```\n</details>\n",
            encoding="utf-8")

    # Jeder Kanal wird einzeln als erledigt vermerkt: schlaegt Telegram
    # fehl, darf der naechste Versuch Discord nicht ein zweites Mal bedienen.
    sent = state.setdefault("sent", {})
    if sent.get("_day") != today or force:
        sent.clear()
        sent["_day"] = today
    failed = False
    for name, deliver in _channels(cfg, Path(config_path), post, telegram_post, log):
        if sent.get(name):
            continue
        if deliver(text):
            sent[name] = True
            state_path.write_text(json.dumps(state), encoding="utf-8")
        else:
            failed = True
    if failed:
        return None

    state.update(last_sent=today, last_match=newest)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return text
