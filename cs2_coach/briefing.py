"""Taegliches Trainings-Briefing: was, wie, wann - und ob es wirkt.

Ablauf: Fakten berechnen (training.py, evidence.py), die KI formuliert
daraus einen Plan, Versand an Discord und als Notiz in den Vault.

Die KI rechnet nichts. Sie bekommt fertige Zahlen samt Urteil und darf
nur diese verwenden. Das ist kein Detail: dieses Projekt hat mehrfach
Ratschlaege aus Rauschen gegeben ("Tilt-Effekt erkannt", "bester Tag:
Montag" aus 5 Matches). Ein Sprachmodell, das frei formuliert, wuerde
genau das wieder tun. Deshalb:

- Der Systemprompt verbietet Zahlen und Ursachen, die nicht in den Fakten
  stehen, und verlangt "noch offen", wo das Urteil offen ist.
- Enthaelt die Antwort mehr als zwei Zahlen, die in den Fakten nicht
  vorkommen, wird sie verworfen und die Fakten werden ohne KI-Text
  geschickt. Ebenso bei Fehlern der KI-Schnittstelle (call_gemini und
  call_ollama geben dann "**Fehler:** ..." zurueck, statt zu werfen).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .training import RECENT, current_plan

SYSTEM = """Du bist ein CS2-Trainer und schreibst ein kurzes taegliches Briefing auf Deutsch fuer einen einzelnen Spieler.

Regeln, ohne Ausnahme:
- Verwende ausschliesslich Zahlen, die in den FAKTEN stehen. Rechne nichts aus, runde nicht um, erfinde keine Werte.
- Steht bei einem Vergleich "noch offen" oder "zu wenige Matches", schreibe genau das. Behaupte keinen Fortschritt und keinen Rueckschritt, den die Fakten nicht als belastbar ausweisen.
- Nenne keine Ursachen, die nicht in den Fakten stehen.
- Uebungen: je Ziel genau die Uebung, die in den FAKTEN bei diesem Ziel steht. Keine anderen.

Aufbau (Discord-Markdown, hoechstens 1500 Zeichen):
**Heute trainieren** - je Trainingsziel: Zielwert und Wert der letzten Matches nennen, dazu die passende Uebung. 2-3 Stichpunkte.
**Wann** - ein Satz: was vor der ersten Partie, was waehrend der Matches
**Fortschritt** - je Ziel ein Satz, dazu die Siegquote
**Matches** - die Match-Liste aus den FAKTEN in einem Satz zusammenfassen, mit deren Ueberschrift ("neu seit dem letzten Briefing" oder "letzte Matches"). Nur wenn die Liste "Keine" sagt, schreiben, dass keine dazugekommen sind."""


#: Uebung je Ziel. Fest zugeordnet statt der KI ueberlassen: am 2026-10-16
#: waehlte sie fuer Accuracy "10 Minuten Lineups", weil die Liste keine
#: Zieluebung enthielt. {map} ist die zuletzt meistgespielte Map.
_UTILITY = ("offline `map de_{map}`, `exec coach/utility`, Wurfpositionen ueben; "
            "im Match in jeder Kaufrunde vor dem ersten Kontakt eine werfen")
EXERCISES = {
    "util_h": _UTILITY, "util_m": _UTILITY, "util_f": _UTILITY, "util_s": _UTILITY,
    "utility": _UTILITY,
    "accuracy": ("vor der ersten Partie offline `map de_{map}`, `exec coach/warmup` "
                 "(bewegliche Bots), danach `exec coach/spray_{map}` (Spray-Transfer)"),
    "crosshair_placement": ("offline `map de_{map}`, `exec coach/practice_{map}` - Prefire auf "
                            "die Stellen, an denen du am haeufigsten stirbst, Fadenkreuz auf Kopfhoehe"),
    "counter_strafe": ("offline `map de_{map}`, `exec coach/warmup` - vor jedem Schuss "
                       "Gegenrichtung tippen, erst im Stand schiessen"),
}
_DEFAULT_EXERCISE = "vor der ersten Partie offline `map de_{map}`, `exec coach/warmup`"


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

    main_map = _main_map(exports)
    lines = ["FAKTEN", "", "Trainingsziele (Zielwert = was der Spieler in seinen Siegen tut):"]
    if not plan["goals"]:
        lines.append("- Keine offenen Trainingsziele.")
    for g in plan["goals"]:
        per = f" - etwa eine pro {_num(round(1 / g.target, 1))} Runden" if g.unit == "/Runde" and g.target else ""
        lines.append(f"- {g.label}: Zielwert {_num(g.target)}{g.unit}{per}. "
                     f"Letzte {RECENT} Matches: {_num(g.recent)}{g.unit}. Grund: {g.why}")
        lines.append("  Uebung: " + EXERCISES.get(g.key, _DEFAULT_EXERCISE).format(map=main_map))
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
    prompt = facts + "\n\nSchreibe das Briefing fuer heute."
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
    unknown = _unknown_numbers(text, facts)
    if len(unknown) > 2:
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
        now: datetime | None = None, log=print, post=None, telegram_post=None) -> str | None:
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
    ai = ask_ai(cfg, facts, log=log)
    header = f"**CS2 Coach – Briefing {now.strftime('%d.%m.%Y')}**"
    body = ai if ai else ("_KI nicht verfuegbar, hier die Fakten:_\n\n" + facts)
    text = header + "\n\n" + body

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
