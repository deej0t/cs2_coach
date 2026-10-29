"""Server-Bestenliste: Statistiken aus dem Log des eigenen CS2-Servers.

Der Server (ich777/steamcmd:cs2 auf dem Unraid) dient als Warmup - es
laeuft jeder Modus: Deathmatch, 1v1 auf Aim-Maps, Competitive. Demos
taugen dafuer nicht: der Coach-Parser ist auf Runden gebaut, und GOTV
muesste fuer jede Map eigens aufzeichnen. Das Server-Log dagegen kennt
jeder Modus.

Datenweg: CS2 schickt sein Log per ``logaddress_add_http`` an
``/api/serverlog/<token>``. Jeder Stapel wird unveraendert an eine
Tagesdatei in ``server_logs/`` neben der Config angehaengt, davor eine
Kopfzeile ``#batch <instanz> <offset> <map>``. Die Statistik wird bei
jedem Aufruf aus diesen Rohdaten berechnet und nie gespeichert -
Regeln wie die Mindestzahl an Duellen wirken so rueckwirkend auf alles.

Gemessen am 29.10.2026 (Server 1.41.9.3): CS2 sendet Klartextzeilen
``MM/DD/YYYY - HH:MM:SS.mmm - <Ereignis>`` und die Header
``X-Server-Instance-Token`` (neu bei jedem Serverstart) und
``X-LogBytes-BeginOffset``. Instanz + Offset kennzeichnen einen Stapel
eindeutig; ein wiederholt gesendeter Stapel wird beim Lesen verworfen.

Wertung: Duell-Elo. Jeder Kill ist ein gewonnenes Duell gegen das Opfer -
das funktioniert in jedem Modus und belohnt Kills gegen starke Gegner
mehr. Bots zaehlen nicht (eigene Spalte), Selbstmorde auch nicht. Wer
weniger als ``MIN_DUELS`` Duelle hat, steht als "vorlaeufig" unten:
nach drei Kills sagt eine Elo nichts.
"""

from __future__ import annotations

import os
import re
import secrets
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Mapping

LOG_DIR = "server_logs"
TOKEN_FILE = ".serverlog_token"

ELO_START = 1000.0
ELO_K = 8.0  # klein, weil ein Abend Deathmatch Hunderte Duelle liefert
MIN_DUELS = 30
MIN_GAME_KILLS = 5  # kuerzere Abschnitte (Warmup vor Match_Start) zaehlen nicht als Spiel
MIN_RECORD_KILLS = 10  # HS-Quote eines Spiels erst ab so vielen Kills
ONLINE_WINDOW = timedelta(minutes=10)
MAX_BATCH_BYTES = 1_000_000

STEAM64_BASE = 76561197960265728

# --------------------------------------------------------------------------
# Ablage
# --------------------------------------------------------------------------


def log_dir(config_path: Path) -> Path:
    return Path(config_path).parent / LOG_DIR


def load_or_create_token(config_path: Path) -> str:
    """Geheimer Teil der Empfangs-URL, neben der Config abgelegt.

    Nicht in config.yaml: settings_save() schreibt das Config-Dict des
    jeweiligen Workers zurueck und wuerde einen nachtraeglich erzeugten
    Schluessel verlieren. O_EXCL wie beim Secret Key, damit parallel
    startende Gunicorn-Worker denselben Token bekommen.
    """
    path = Path(config_path).parent / TOKEN_FILE
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        for _ in range(50):
            token = path.read_text(encoding="ascii").strip()
            if token:
                return token
            time.sleep(0.02)
        return ""
    token = secrets.token_urlsafe(24)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(token)
    return token


def _header_value(headers: Mapping, name: str, default: str = "-") -> str:
    value = headers.get(name) or default
    return re.sub(r"\s+", "_", str(value))[:64] or default


def store_batch(config_path: Path, headers: Mapping, body: bytes, now: datetime | None = None) -> int:
    """Einen Log-Stapel anhaengen. Gibt die Zahl der Zeilen zurueck.

    Ein einziger write() mit O_APPEND - CS2 sendet ohnehin erst den
    naechsten Stapel, wenn der vorige beantwortet ist.
    """
    text = body.decode("utf-8", errors="replace").replace("\r", "")
    lines = [ln for ln in text.split("\n") if ln.strip() and not ln.startswith("#batch ")]
    if not lines:
        return 0
    now = now or datetime.now()
    # Empfangszeit statt Server-Uhr: der Server loggt in seiner eigenen
    # Zeitzone (nach einem Neuerstellen ohne TZ in UTC, gemessen 30.10.2026).
    day = now.strftime("%Y-%m-%d")
    head = "#batch {} {} {} {}".format(
        _header_value(headers, "X-Server-Instance-Token"),
        _header_value(headers, "X-LogBytes-BeginOffset"),
        _header_value(headers, "X-Game-Map"),
        now.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    folder = log_dir(config_path)
    folder.mkdir(parents=True, exist_ok=True)
    data = (head + "\n" + "\n".join(lines) + "\n").encode("utf-8")
    fd = os.open(folder / f"{day}.log", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return len(lines)


# --------------------------------------------------------------------------
# Log-Zeilen
# --------------------------------------------------------------------------

_LINE = re.compile(r"^(?:L )?(\d\d/\d\d/\d{4} - \d\d:\d\d:\d\d(?:\.\d+)?) - (.*)$")


def _player(p: str) -> str:
    return rf'"(?P<{p}name>.*?)<(?P<{p}uid>-?\d+)><(?P<{p}sid>[^>]*)><(?P<{p}team>[^>]*)>"'


_POS = r"(?: \[[^\]]*\])?"
_KILL = re.compile(_player("a") + _POS + " killed " + _player("v") + _POS
                   + r' with "(?P<weapon>[^"]*)"(?: \((?P<flags>[^)]*)\))?')
_ATTACK = re.compile(_player("a") + _POS + " attacked " + _player("v") + _POS
                     + r' with "(?P<weapon>[^"]*)".*?\(hitgroup "(?P<hitgroup>[^"]*)"\)')
_ASSIST = re.compile(_player("a") + r" (?:flash-)?assisted killing " + _player("v"))
_ENTER = re.compile(_player("a") + r" entered the game")
_LEAVE = re.compile(_player("a") + r" disconnected")
_MAP = re.compile(r'^(?:Loading map|Started map) "(?P<map>[^"]+)"')
_MATCH_START = re.compile(r'^World triggered "Match_Start" on "(?P<map>[^"]+)"')
_GAME_OVER = re.compile(r"^Game Over: (?P<mode>\S+) \S+ (?P<map>\S+) score (?P<s1>\d+):(?P<s2>\d+)")


def _parse_ts(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    for fmt in ("%m/%d/%Y - %H:%M:%S.%f", "%m/%d/%Y - %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def steam64(sid: str) -> str | None:
    """SteamID aus dem Log (``[U:1:N]`` oder ``STEAM_1:Y:Z``) als SteamID64.

    None fuer Bots, Konsole und alles Unbekannte - die zaehlen nicht.
    """
    m = re.fullmatch(r"\[U:1:(\d+)\]", sid)
    if m:
        return str(STEAM64_BASE + int(m.group(1)))
    m = re.fullmatch(r"STEAM_\d:([01]):(\d+)", sid)
    if m:
        return str(STEAM64_BASE + int(m.group(2)) * 2 + int(m.group(1)))
    return None


@dataclass
class Event:
    ts: datetime
    kind: str  # kill, hit, assist, enter, leave
    game: int
    instance: str = ""
    a: str | None = None  # SteamID64 des Handelnden, None = Bot/Welt
    v: str | None = None  # SteamID64 des Opfers
    a_bot: bool = False
    v_bot: bool = False
    weapon: str = ""
    headshot: bool = False
    hitgroup: str = ""


@dataclass
class Game:
    id: int
    instance: str
    map: str
    start: datetime | None = None
    end: datetime | None = None
    mode: str = ""
    score: str = ""
    finished: bool = False


@dataclass
class ServerLog:
    events: list[Event] = field(default_factory=list)
    games: dict[int, Game] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    last_ts: datetime | None = None
    last_instance: str = ""
    last_map: str = ""


def _batches(lines: Iterable[str]):
    """Rohzeilen in (Kopf-Felder, Zeilen) gruppieren."""
    head: list[str] = ["#batch"]
    body: list[str] = []
    for raw in lines:
        raw = raw.rstrip("\n").replace("\x00", "")
        if raw.startswith("#batch "):
            if body:
                yield head, body
            head, body = raw.split(" "), []
        elif raw:
            body.append(raw)
    if body:
        yield head, body


def _clock_shift(recv: datetime | None, first: datetime | None) -> timedelta:
    """Abstand Server-Uhr -> Empfangszeit, auf Viertelstunden gerundet (= Zeitzone).

    Die Rundung schluckt die Sekunden, die ein Stapel unterwegs ist.
    """
    if recv is None or first is None:
        return timedelta(0)
    return timedelta(minutes=15 * round((recv - first).total_seconds() / 900))


def parse_lines(lines: Iterable[str]) -> ServerLog:
    """Rohzeilen (inkl. ``#batch``-Koepfen) in Ereignisse und Spiele zerlegen.

    Ein neues Spiel beginnt bei Serverneustart (neue Instanz), Mapwechsel
    und Match_Start, und nach "Game Over". Doppelt gesendete Stapel werden
    am Inhalt erkannt, nicht am Offset: nach einem Neustart kamen am
    30.10.2026 zwei verschiedene Stapel mit Offset 0 (die server.cfg laeuft
    beim Start zweimal und meldet den Empfaenger jedes Mal neu an).
    Zeitstempel werden mit der Empfangszeit aus dem Kopf auf Ortszeit
    gebracht.
    """
    out = ServerLog()
    seen: set[tuple] = set()
    instance = ""
    batch_map = ""
    game: Game | None = None
    next_id = 0

    def new_game(map_name: str) -> Game:
        nonlocal next_id, game
        next_id += 1
        game = Game(id=next_id, instance=instance, map=map_name)
        out.games[game.id] = game
        return game

    def current(ts: datetime) -> Game:
        g = game if game is not None and not game.finished else new_game(batch_map)
        if g.start is None:
            g.start = ts
        g.end = ts
        return g

    def who(prefix: str, m: re.Match) -> tuple[str | None, bool]:
        sid = m.group(prefix + "sid")
        if sid == "BOT":
            return None, True
        s64 = steam64(sid)
        if s64:
            out.names[s64] = m.group(prefix + "name")
        return s64, False

    for parts, body in _batches(lines):
        token = parts[1] if len(parts) > 1 else "-"
        key = (token, tuple(body))
        if key in seen:
            continue  # derselbe Stapel ein zweites Mal gesendet
        seen.add(key)
        if token != instance:
            instance = token
            out.last_instance = instance
            game = None
        if len(parts) > 3 and parts[3] != "-":
            batch_map = out.last_map = parts[3]
        try:
            recv = datetime.fromisoformat(parts[4]) if len(parts) > 4 else None
        except ValueError:
            recv = None
        shift = None
        for raw in body:
            m = _LINE.match(raw)
            if not m:
                continue
            ts = _parse_ts(m.group(1))
            if ts is None:
                continue
            if shift is None:
                shift = _clock_shift(recv, ts)
            ts += shift
            msg = m.group(2)
            out.last_ts = ts

            if mm := (_MAP.match(msg) or _MATCH_START.match(msg)):
                # "Loading map" und "Started map" folgen direkt aufeinander:
                # ein Spiel ohne Ereignisse bekommt nur die Map, kein neues.
                batch_map = out.last_map = mm.group("map")
                if game is not None and game.start is None and not game.finished:
                    game.map = batch_map
                else:
                    new_game(batch_map)
                continue
            if mm := _GAME_OVER.match(msg):
                g = current(ts)
                g.mode, g.map = mm.group("mode"), mm.group("map")
                g.score = f"{mm.group('s1')}:{mm.group('s2')}"
                g.finished = True
                continue

            if mm := _KILL.match(msg):
                a, a_bot = who("a", mm)
                v, v_bot = who("v", mm)
                flags = (mm.group("flags") or "").split()
                weapon = mm.group("weapon")
                if weapon.startswith(("knife", "bayonet")):
                    weapon = "knife"  # Kill-Zeilen nennen den Skin ("knife_skeleton"), Treffer nur "knife"
                out.events.append(Event(ts, "kill", current(ts).id, instance, a, v, a_bot, v_bot,
                                        weapon, "headshot" in flags))
            elif mm := _ATTACK.match(msg):
                a, a_bot = who("a", mm)
                v, v_bot = who("v", mm)
                out.events.append(Event(ts, "hit", current(ts).id, instance, a, v, a_bot, v_bot,
                                        mm.group("weapon"), hitgroup=mm.group("hitgroup")))
            elif mm := _ASSIST.match(msg):
                a, a_bot = who("a", mm)
                v, v_bot = who("v", mm)
                out.events.append(Event(ts, "assist", current(ts).id, instance, a, v, a_bot, v_bot))
            elif mm := _ENTER.match(msg):
                a, _ = who("a", mm)
                if a:
                    out.events.append(Event(ts, "enter", game.id if game else 0, instance, a))
            elif mm := _LEAVE.match(msg):
                a, _ = who("a", mm)
                if a:
                    out.events.append(Event(ts, "leave", game.id if game else 0, instance, a))
    return out


def read_logs(config_path: Path) -> ServerLog:
    """Alle Tagesdateien lesen, mit Cache auf Dateigroesse + mtime."""
    folder = log_dir(config_path)
    files = sorted(folder.glob("*.log")) if folder.is_dir() else []
    key = tuple((f.name, f.stat().st_size, f.stat().st_mtime_ns) for f in files)
    cached = _CACHE.get(str(folder))
    if cached and cached[0] == key:
        return cached[1]

    def lines():
        for f in files:
            with open(f, encoding="utf-8", errors="replace") as fh:
                yield from fh

    log = parse_lines(lines())
    _CACHE[str(folder)] = (key, log)
    return log


_CACHE: dict[str, tuple] = {}


def archive_logs(config_path: Path, now: datetime | None = None) -> int:
    """Bestenliste zuruecksetzen: Rohlogs nach ``archiv/<zeitpunkt>/`` verschieben.

    Verschieben statt loeschen - ein versehentlicher Klick kostet so nichts,
    und read_logs() liest nur die oberste Ebene. Gibt die Zahl der Dateien zurueck.
    """
    folder = log_dir(config_path)
    files = sorted(folder.glob("*.log")) if folder.is_dir() else []
    if not files:
        return 0
    target = folder / "archiv" / (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    target.mkdir(parents=True, exist_ok=True)
    for f in files:
        f.rename(target / f.name)
    _CACHE.pop(str(folder), None)
    return len(files)


# --------------------------------------------------------------------------
# Auswertung
# --------------------------------------------------------------------------

def _hs_rate(head: int, total: int) -> float | None:
    return head / total if total else None


def _expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


PERIODS = {"alle": None, "30": 30, "7": 7}


def build_board(log: ServerLog, period: str = "alle", now: datetime | None = None,
                since: datetime | None = None, until: datetime | None = None) -> dict:
    """Bestenliste, Kopf-an-Kopf, Rekorde und letzte Spiele fuer einen Zeitraum.

    ``since``/``until`` ueberschreiben ``period`` (z. B. fuer eine Kalenderwoche).
    """
    now = now or datetime.now()
    days = PERIODS.get(period, None)
    if since is None and days:
        since = now - timedelta(days=days)
    events = [e for e in log.events
              if (since is None or e.ts >= since) and (until is None or e.ts < until)]

    stats: dict[str, dict] = defaultdict(lambda: {
        "kills": 0, "deaths": 0, "hs": 0, "assists": 0, "bot_kills": 0,
        "hits": 0, "head_hits": 0, "weapons": Counter(), "victims": Counter(),
        "killers": Counter(), "games": set(), "last_seen": None,
        "elo": ELO_START, "best_streak": 0,
    })
    pair = Counter()  # (killer, victim) -> Kills
    streak: dict[str, int] = defaultdict(int)
    game_stats: dict[int, dict[str, dict]] = defaultdict(lambda: defaultdict(
        lambda: {"kills": 0, "deaths": 0, "hs": 0}))
    last_game = None
    streak_records: list[tuple[int, str, int]] = []

    def seen(sid: str, e: Event):
        s = stats[sid]
        s["last_seen"] = e.ts
        if e.kind in ("kill", "hit", "assist"):
            s["games"].add(e.game)

    def end_streak(sid: str, gid):
        if streak.get(sid):
            streak_records.append((streak[sid], sid, gid))
        streak[sid] = 0

    for e in events:
        if e.game != last_game:
            for sid in list(streak):
                end_streak(sid, last_game)
            last_game = e.game
        if e.a:
            seen(e.a, e)
        if e.v:
            seen(e.v, e)
        if e.kind == "hit":
            if e.a and e.v and e.a != e.v:
                stats[e.a]["hits"] += 1
                stats[e.a]["head_hits"] += e.hitgroup == "head"
            continue
        if e.kind == "assist":
            if e.a and e.v:
                stats[e.a]["assists"] += 1
            continue
        if e.kind != "kill":
            continue
        if e.v:
            end_streak(e.v, e.game)  # jeder Tod beendet die Serie, auch durch Bot oder Fall
        if e.a and e.v_bot:
            stats[e.a]["bot_kills"] += 1
            continue
        if not (e.a and e.v) or e.a == e.v:
            continue  # Bot, Welt, Fallschaden, Selbstmord: kein Duell
        ka, kv = stats[e.a], stats[e.v]
        ka["kills"] += 1
        ka["hs"] += e.headshot
        ka["weapons"][e.weapon] += 1
        ka["victims"][e.v] += 1
        kv["deaths"] += 1
        kv["killers"][e.a] += 1
        pair[(e.a, e.v)] += 1
        gs = game_stats[e.game]
        gs[e.a]["kills"] += 1
        gs[e.a]["hs"] += e.headshot
        gs[e.v]["deaths"] += 1
        exp = _expected(ka["elo"], kv["elo"])
        ka["elo"] += ELO_K * (1 - exp)
        kv["elo"] -= ELO_K * (1 - exp)
        streak[e.a] += 1
        ka["best_streak"] = max(ka["best_streak"], streak[e.a])
    for sid in list(streak):
        end_streak(sid, last_game)

    def name(sid: str) -> str:
        return log.names.get(sid, sid)

    rows = []
    for sid, s in stats.items():
        duels = s["kills"] + s["deaths"]
        if duels == 0 and s["bot_kills"] == 0:
            continue
        rows.append({
            "steam_id": sid, "name": name(sid), "elo": round(s["elo"]), "duels": duels,
            "ranked": duels >= MIN_DUELS, "kills": s["kills"], "deaths": s["deaths"],
            "kd": s["kills"] / s["deaths"] if s["deaths"] else None,
            "hs_kills": _hs_rate(s["hs"], s["kills"]),
            "head_hits": _hs_rate(s["head_hits"], s["hits"]), "hits": s["hits"],
            "assists": s["assists"], "bot_kills": s["bot_kills"], "games": len(s["games"]),
            "best_streak": s["best_streak"], "last_seen": s["last_seen"],
            "weapon": s["weapons"].most_common(1)[0][0] if s["weapons"] else "",
            "nemesis": _top(s["killers"], name), "victim": _top(s["victims"], name),
        })
    rows.sort(key=lambda r: (not r["ranked"], -r["elo"], -r["kills"]))
    rank = 0
    for r in rows:
        if r["ranked"]:
            rank += 1
            r["rank"] = rank
        else:
            r["rank"] = None

    h2h_players = [r for r in rows if r["duels"]][:8]
    h2h = {
        "players": [{"steam_id": r["steam_id"], "name": r["name"]} for r in h2h_players],
        "kills": [[pair.get((a["steam_id"], b["steam_id"]), 0) for b in h2h_players] for a in h2h_players],
    }

    games = []
    for gid, per in game_stats.items():
        g = log.games.get(gid)
        total = sum(p["kills"] for p in per.values())
        if g is None or total < MIN_GAME_KILLS:
            continue
        players = sorted(({"name": name(sid), "steam_id": sid, **p} for sid, p in per.items()),
                         key=lambda p: (-p["kills"], p["deaths"]))
        games.append({"id": gid, "map": g.map or "?", "mode": g.mode, "score": g.score,
                      "start": g.start, "end": g.end, "finished": g.finished,
                      "minutes": round((g.end - g.start).total_seconds() / 60) if g.start and g.end else 0,
                      "players": players, "kills": total})
    games.sort(key=lambda g: g["start"] or datetime.min, reverse=True)

    records = _records(game_stats, log, name, streak_records)
    return {"rows": rows, "h2h": h2h, "games": games[:15], "records": records,
            "online": online(log, now), "period": period if period in PERIODS else "alle",
            "min_duels": MIN_DUELS, "has_data": bool(log.events),
            "last_ts": log.last_ts, "total_kills": sum(r["kills"] for r in rows)}


def _top(counter: Counter, name) -> dict | None:
    if not counter:
        return None
    sid, n = counter.most_common(1)[0]
    return {"name": name(sid), "steam_id": sid, "count": n} if n >= 3 else None


def _records(game_stats, log: ServerLog, name, streaks) -> list[dict]:
    """Hall of Fame: beste Einzelleistungen in einem Spiel."""
    out = []

    def game_label(gid):
        g = log.games.get(gid)
        if not g:
            return ""
        when = g.start.strftime("%d.%m.%Y") if g.start else ""
        return f"{g.map} · {when}".strip(" ·")

    best_kills = max(((p["kills"], sid, gid) for gid, per in game_stats.items()
                      for sid, p in per.items()), default=None)
    if best_kills and best_kills[0]:
        out.append({"title": "Meiste Kills in einem Spiel", "value": str(best_kills[0]),
                    "name": name(best_kills[1]), "where": game_label(best_kills[2])})
    if streaks:
        n, sid, gid = max(streaks)
        if n >= 3:
            out.append({"title": "Längste Killserie", "value": f"{n} ohne Tod",
                        "name": name(sid), "where": game_label(gid)})
    best_hs = max(((p["hs"] / p["kills"], p["kills"], sid, gid) for gid, per in game_stats.items()
                   for sid, p in per.items() if p["kills"] >= MIN_RECORD_KILLS), default=None)
    if best_hs:
        out.append({"title": f"Höchste HS-Quote (ab {MIN_RECORD_KILLS} Kills)",
                    "value": f"{best_hs[0] * 100:.0f} % von {best_hs[1]}",
                    "name": name(best_hs[2]), "where": game_label(best_hs[3])})
    best_kd = max(((p["kills"] / max(p["deaths"], 1), p["kills"], p["deaths"], sid, gid)
                   for gid, per in game_stats.items() for sid, p in per.items()
                   if p["kills"] >= MIN_RECORD_KILLS), default=None)
    if best_kd:
        out.append({"title": f"Beste K/D in einem Spiel (ab {MIN_RECORD_KILLS} Kills)",
                    "value": f"{best_kd[1]}:{best_kd[2]}",
                    "name": name(best_kd[3]), "where": game_label(best_kd[4])})
    return out


def online(log: ServerLog, now: datetime) -> dict:
    """Wer ist gerade auf dem Server? Nur aus der juengsten Instanz.

    Spieler, die schon vor dem Einschalten des Logs verbunden waren, tauchen
    erst mit ihrer ersten Aktion auf - ein "entered the game" gab es fuer
    sie nie.
    """
    if not log.last_ts or now - log.last_ts > ONLINE_WINDOW:
        return {"active": False, "players": [], "map": ""}
    present: set[str] = set()
    for e in log.events:
        if e.instance != log.last_instance:
            continue
        if e.kind == "enter":
            present.add(e.a)
        elif e.kind == "leave":
            present.discard(e.a)
        elif now - e.ts <= ONLINE_WINDOW:
            present.update(s for s in (e.a, e.v) if s)
    names = sorted(log.names.get(s, s) for s in present)
    return {"active": True, "players": names, "map": log.last_map}
