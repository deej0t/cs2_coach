"""Live-Funktionen der Server-Bestenliste: Chat-Befehle, Spielende, Spieler der Woche.

Alles haengt am Log-Empfang (/api/serverlog): CS2 schickt jede Zeile
sofort, auch Chat. Schreibt jemand ``!top`` in den Chat, sieht der Coach
das binnen einer Sekunde und antwortet per RCON mit ``say`` - ohne
Server-Plugin (Metamod bricht bei fast jedem CS2-Update).

- Chat-Befehle: nur mit ``server_rcon_host`` + ``server_rcon_password``.
- Spielende ("Game Over") und Spieler der Woche: Discord, Webhook
  ``server_webhook``, sonst ``discord_webhook``.

Jede Meldung wird ueber eine Markierungsdatei in ``server_logs/.posted/``
(O_EXCL) genau einmal verschickt, auch bei mehreren Gunicorn-Workern und
wiederholt gesendeten Stapeln. Der Spieler der Woche geht mit dem ersten
Stapel der neuen Woche raus - der Server sendet nur, wenn er laeuft, ein
Zeitplan ist dafuer nicht noetig.

Sicherheit: Spielernamen und Chat-Text landen in einem RCON-Befehl. Ein
Name wie ``x"; rcon_password y`` waere sonst Befehls-Einschleusung, darum
entfernt ``_safe()`` Anfuehrungszeichen, Semikolons und Steuerzeichen aus
allem, was gesendet wird.
"""

from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import rcon, server_stats as ss

PREFIX = "[Coach]"
MAX_SAY = 120  # laengere Chatzeilen schneidet CS2 ab
COOLDOWN = 3.0  # Sekunden pro Spieler zwischen zwei Befehlen
SESSION_HOURS = 6

_CHAT = re.compile(ss._player("a") + r' say(?:_team)? "(?P<text>.*)"$')
_last_command: dict[str, float] = {}

HELP = "Befehle: !top · !rank · !session · !nemesis · !vs <Name> · !help"


# --------------------------------------------------------------------------
# Hilfen
# --------------------------------------------------------------------------

def _safe(text: str) -> str:
    """Alles entfernen, was aus ``say "..."`` ausbrechen koennte."""
    text = re.sub(r'[";\x00-\x1f\x7f\\]', "", str(text))
    return re.sub(r"\s+", " ", text).strip()


def _board_url(cfg: dict) -> str:
    base = (cfg.get("public_url") or "").strip().rstrip("/")
    return f"{base}/server" if base else ""


def _short_url(cfg: dict) -> str:
    return re.sub(r"^https?://", "", _board_url(cfg))


def _pct(v) -> str:
    return f"{v * 100:.0f} %" if v is not None else "–"


def _kd(r: dict) -> str:
    if r["kd"] is not None:
        return f"{r['kd']:.2f}"
    return "∞" if r["kills"] else "–"


def _mark_once(config_path: Path, key: str) -> bool:
    """True genau beim ersten Aufruf fuer diesen Schluessel (prozessuebergreifend)."""
    folder = ss.log_dir(config_path) / ".posted"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        os.close(os.open(folder / re.sub(r"[^\w.-]", "_", key), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except FileExistsError:
        return False


def _webhook(cfg: dict) -> str:
    return (cfg.get("server_webhook") or cfg.get("discord_webhook") or "").strip()


# --------------------------------------------------------------------------
# Chat-Befehle
# --------------------------------------------------------------------------

def parse_commands(lines: list[str]) -> list[tuple[str, str, str, str]]:
    """(steam_id64, name, befehl, argument) fuer jede Chatzeile, die mit ! beginnt."""
    out = []
    for raw in lines:
        m = ss._LINE.match(raw)
        if not m:
            continue
        c = _CHAT.match(m.group(2))
        if not c:
            continue
        text = c.group("text").strip()
        if not text.startswith("!"):
            continue
        sid = ss.steam64(c.group("asid"))
        if not sid:
            continue
        cmd, _, arg = text[1:].partition(" ")
        out.append((sid, c.group("aname"), cmd.lower(), arg.strip()))
    return out


def _find_row(rows: list[dict], sid: str) -> dict | None:
    return next((r for r in rows if r["steam_id"] == sid), None)


def _rank_line(r: dict, rows: list[dict]) -> str:
    ranked = sum(1 for x in rows if x["ranked"])
    if r["ranked"]:
        place = f"Platz {r['rank']} von {ranked}"
    else:
        place = f"vorläufig, noch {ss.MIN_DUELS - r['duels']} Duelle bis zur Wertung"
    return (f"{r['name']}: {place} · Elo {r['elo']} · {r['kills']}:{r['deaths']} "
            f"(K/D {_kd(r)}) · HS {_pct(r['hs_kills'])} · Kopftreffer {_pct(r['head_hits'])}")


def answer(cmd: str, arg: str, sid: str, name: str, log: ss.ServerLog, cfg: dict,
           now: datetime | None = None) -> list[str]:
    """Antwortzeilen fuer einen Chat-Befehl (leer = unbekannter Befehl, keine Antwort)."""
    now = now or datetime.now()
    url = _short_url(cfg)
    if cmd in ("help", "hilfe", "befehle", "commands", "web", "bestenliste"):
        return [HELP] + ([f"Bestenliste: {url}"] if url else [])

    board = ss.build_board(log, "alle", now=now)
    rows = board["rows"]
    if cmd in ("top", "top5", "top10"):
        top = [r for r in rows if r["ranked"]][:5]
        if not top:
            top = rows[:5]
            if not top:
                return ["Noch keine Duelle aufgezeichnet."]
            head = f"Noch niemand mit {ss.MIN_DUELS} Duellen - vorläufig: "
        else:
            head = "Top 5: "
        parts = [f"{i}. {r['name']} {r['elo']}" for i, r in enumerate(top, 1)]
        return [head + " | ".join(parts)] + ([f"Alle Werte: {url}"] if url else [])

    if cmd in ("rank", "myrank", "elo", "stats", "me"):
        r = _find_row(rows, sid)
        if not r:
            return [f"{name}: noch keine Duelle gegen Menschen aufgezeichnet."]
        return [_rank_line(r, rows)]

    if cmd in ("session", "heute", "today"):
        since = now - timedelta(hours=SESSION_HOURS)
        sb = ss.build_board(log, now=now, since=since)
        r = _find_row(sb["rows"], sid)
        if not r or not r["duels"]:
            return [f"{name}: in den letzten {SESSION_HOURS} Stunden noch keine Duelle."]
        return [f"{name}, letzte {SESSION_HOURS} h: {r['kills']}:{r['deaths']} (K/D {_kd(r)}) · "
                f"HS {_pct(r['hs_kills'])} · beste Serie {r['best_streak']}"]

    if cmd in ("nemesis", "rivale"):
        r = _find_row(rows, sid)
        if not r or not (r["nemesis"] or r["victim"]):
            return [f"{name}: noch zu wenige Duelle (ab 3 gegen denselben Gegner)."]
        bits = []
        if r["nemesis"]:
            bits.append(f"Nemesis {r['nemesis']['name']} ({r['nemesis']['count']}x gestorben)")
        if r["victim"]:
            bits.append(f"Lieblingsopfer {r['victim']['name']} ({r['victim']['count']}x)")
        return [f"{name}: " + " · ".join(bits)]

    if cmd in ("vs", "h2h", "gegen"):
        if not arg:
            return ["Benutzung: !vs <Name>"]
        needle = arg.lower()
        others = [r for r in rows if r["steam_id"] != sid]
        match = (next((r for r in others if r["name"].lower() == needle), None)
                 or next((r for r in others if needle in r["name"].lower()), None))
        if not match:
            return [f"Kein Spieler mit '{arg}' in der Bestenliste."]
        mine = sum(1 for e in log.events if e.kind == "kill" and e.a == sid and e.v == match["steam_id"])
        theirs = sum(1 for e in log.events if e.kind == "kill" and e.a == match["steam_id"] and e.v == sid)
        return [f"{name} gegen {match['name']}: {mine}:{theirs} Kills"]
    return []


def _say_commands(lines: list[str]) -> list[str]:
    out = []
    for line in lines:
        text = _safe(f"{PREFIX} {line}")
        while text:
            out.append(f'say "{text[:MAX_SAY]}"')
            text = text[MAX_SAY:].strip()
    return out


def handle_commands(cfg: dict, config_path: Path, lines: list[str], send=None, now=None) -> int:
    """Chat-Befehle aus einem Stapel beantworten. Gibt die Zahl der Antworten zurueck."""
    host = (cfg.get("server_rcon_host") or "").strip()
    password = cfg.get("server_rcon_password") or ""
    commands = parse_commands(lines)
    if not commands or not host or not password:
        return 0
    send = send or (lambda *c: rcon.command(host, cfg.get("server_rcon_port") or 27015, password, *c))
    log = ss.read_logs(config_path)
    sent = 0
    for sid, name, cmd, arg in commands:
        if time.monotonic() - _last_command.get(sid, -COOLDOWN) < COOLDOWN:
            continue
        reply = answer(cmd, arg, sid, name, log, cfg, now=now)
        if not reply:
            continue
        _last_command[sid] = time.monotonic()
        send(*_say_commands(reply))
        sent += 1
    return sent


# --------------------------------------------------------------------------
# Discord: Spielende und Spieler der Woche
# --------------------------------------------------------------------------

def game_summary(log: ss.ServerLog, cfg: dict, now: datetime | None = None) -> tuple[str, str] | None:
    """(Schluessel, Text) fuer das zuletzt beendete Spiel, None wenn es nichts zu melden gibt."""
    finished = [g for g in log.games.values() if g.finished and g.instance == log.last_instance]
    if not finished:
        return None
    g = finished[-1]
    board = ss.build_board(log, "alle", now=now)
    game = next((x for x in board["games"] if x["id"] == g.id), None)
    if not game:
        return None  # zu wenige Kills - kein echtes Spiel
    mvp = game["players"][0]
    meta = ", ".join(x for x in (g.mode, g.score, f"{game['minutes']} min" if game["minutes"] else "") if x)
    lines = [f"🎮 **Spielende auf {g.map}**" + (f" ({meta})" if meta else ""),
             f"🏆 MVP: **{mvp['name']}** {mvp['kills']}:{mvp['deaths']}"
             + (f" · {mvp['hs'] * 100 // mvp['kills']} % HS" if mvp["kills"] else "")]
    lines.append(" · ".join(f"{p['name']} {p['kills']}:{p['deaths']}" for p in game["players"][:8]))
    if _board_url(cfg):
        lines.append(f"Bestenliste: <{_board_url(cfg)}>")
    start = g.start.strftime("%Y%m%d%H%M%S") if g.start else str(g.id)
    return f"game-{g.instance}-{start}", "\n".join(lines)


def week_window(now: datetime) -> tuple[datetime, datetime, str]:
    """Die abgeschlossene Kalenderwoche vor ``now`` (Montag 0 Uhr bis Montag 0 Uhr)."""
    this_monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start = this_monday - timedelta(days=7)
    year, week, _ = start.isocalendar()
    return start, this_monday, f"{year}-W{week:02d}"


def weekly_summary(log: ss.ServerLog, cfg: dict, now: datetime | None = None) -> tuple[str, str | None]:
    """(Schluessel, Text) fuer den Spieler der Vorwoche. Text None = niemand mit genug Duellen."""
    now = now or datetime.now()
    start, end, label = week_window(now)
    board = ss.build_board(log, now=now, since=start, until=end)
    ranked = [r for r in board["rows"] if r["ranked"]]
    key = f"week-{label}"
    if not ranked:
        return key, None
    best = ranked[0]
    week = label.split("-W")[1]
    lines = [f"🏅 **Spieler der Woche (KW {int(week)})**: **{best['name']}** - Elo {best['elo']} aus "
             f"{best['duels']} Duellen, {best['kills']}:{best['deaths']} (K/D {_kd(best)}), "
             f"{_pct(best['hs_kills'])} HS"]
    most = max(board["rows"], key=lambda r: r["kills"])
    extras = [f"Meiste Kills: {most['name']} ({most['kills']})"]
    aim = [r for r in ranked if r["hits"] >= 50]
    if aim:
        a = max(aim, key=lambda r: r["head_hits"] or 0)
        extras.append(f"Beste Kopftreffer-Quote: {a['name']} ({_pct(a['head_hits'])})")
    lines.append(" · ".join(extras))
    lines.append(f"Gewertet: alle mit mindestens {ss.MIN_DUELS} Duellen in der Woche.")
    if _board_url(cfg):
        lines.append(f"Bestenliste: <{_board_url(cfg)}>")
    return key, "\n".join(lines)


def _post(webhook: str, text: str, post=None) -> bool:
    if post is None:
        import requests
        post = requests.post
    try:
        r = post(webhook, json={"content": text}, timeout=15)
        return 200 <= r.status_code < 300
    except Exception:  # noqa: BLE001 - Discord darf den Log-Empfang nie stoeren
        return False


def handle_posts(cfg: dict, config_path: Path, lines: list[str], post=None, now=None) -> list[str]:
    """Spielende und Spieler der Woche melden. Gibt die Schluessel gesendeter Meldungen zurueck."""
    webhook = _webhook(cfg)
    if not webhook:
        return []
    sent = []
    now = now or datetime.now()
    game_over = any("Game Over:" in ln for ln in lines)
    _, _, week_label = week_window(now)
    week_due = not (ss.log_dir(config_path) / ".posted" / f"week-{week_label}").exists()
    if not (game_over or week_due):
        return []
    log = ss.read_logs(config_path)
    jobs = []
    if game_over and (g := game_summary(log, cfg, now)):
        jobs.append(g)
    if week_due:
        key, text = weekly_summary(log, cfg, now)
        if text is None:
            _mark_once(config_path, key)  # niemand gewertet: nicht bei jedem Stapel neu rechnen
        else:
            jobs.append((key, text))
    for key, text in jobs:
        if _mark_once(config_path, key):
            if _post(webhook, text, post):
                sent.append(key)
            else:
                (ss.log_dir(config_path) / ".posted" / re.sub(r"[^\w.-]", "_", key)).unlink(missing_ok=True)
    return sent


def on_batch(cfg: dict, config_path: Path, body: bytes) -> None:
    """Nach dem Speichern eines Stapels im Hintergrund: Befehle beantworten, Meldungen senden."""
    lines = body.decode("utf-8", errors="replace").replace("\r", "").split("\n")
    has_command = any(" say" in ln and '"!' in ln for ln in lines)
    game_over = any("Game Over:" in ln for ln in lines)
    week_due = bool(_webhook(cfg)) and not _week_marked(config_path)
    if not (has_command or game_over or week_due):
        return  # haeufigster Fall: nichts zu tun, ohne das Log zu lesen

    def work():
        try:
            handle_commands(cfg, config_path, lines)
        except Exception as e:  # noqa: BLE001
            print(f"[server_live] Chat-Befehl fehlgeschlagen: {e}", flush=True)
        try:
            handle_posts(cfg, config_path, lines)
        except Exception as e:  # noqa: BLE001
            print(f"[server_live] Discord-Meldung fehlgeschlagen: {e}", flush=True)

    threading.Thread(target=work, daemon=True).start()


def _week_marked(config_path: Path) -> bool:
    _, _, label = week_window(datetime.now())
    return (ss.log_dir(config_path) / ".posted" / f"week-{label}").exists()
