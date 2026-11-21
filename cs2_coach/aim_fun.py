"""Unterhaltung auf Aim-Maps (aim_multigame): Ansagen, Ausgleich, Waffen-Abstimmung.

Wunsch 21.11.: aim_multigame unterhaltsamer machen, gespielt wird meist 2v2
oder 2v1. Alles laeuft ueber den Log-Empfang (server_live.on_batch), Zeile
fuer Zeile in Reihenfolge, der Zustand liegt in ``.aim_fun.json`` neben der
Config (CS2 schickt den naechsten Stapel erst nach der Antwort).

- Nemesis: A erwischt B mehrfach in Folge (ohne Gegenkill) -> Ansage; B
  beendet das -> "Rache".
- Kopfgeld: wer eine Serie von BOUNTY+ Kills (ohne Tod, ueber Runden
  hinweg) beendet, wird angesagt.
- Clutch: der letzte Ueberlebende gegen 2+ gewinnt die Runde.
- Zwischenstand alle STATS_EVERY Runden: Kills:Tode und HS-Quote aller.
- Spielende: Rangliste im Chat (beste Serie, schnellster Kill, HS-Quote);
  ``last_game`` ergaenzt die Discord-Meldung (server_live.game_summary).
- Ausgleich: fuehrt ein Team mit BALANCE_LEAD Runden seit dem letzten
  Ausgleich, wechselt der Beste des fuehrenden Teams (bei gleich grossen
  Teams Tausch mit dem Schwaechsten des anderen). Abschaltbar: !ausgleich aus.
- !waffe <name>: Mehrheit der Menschen -> nur diese Waffe (plus Messer).
  Plugin ``coach_weapon_mode`` ersetzt beim Spawn und bei jedem Aufheben
  sofort (21.11.: coach_give 1,5 s nach Rundenstart tauschte sichtbar).

Ausgabe nur per Plugin ``coach_chat`` (PrintToChat des Servers): ein ``say``
loest das Chat-Skript der Map aus, das bei jeder Chatzeile eine Exception
wirft (30.10., vor einem WatchDog-Haenger).
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from . import server_stats as ss

STATE_FILE = ".aim_fun.json"
DEFAULT_MAPS = ("aim_multigame",)
NEMESIS_AT = (4, 6, 10, 15)   # Ansage bei so vielen Kills in Folge gegen denselben (3 kam im
                              # Probelauf mit dem Log vom 21.11. im 1v1 fast jede Minute)
BOUNTY = 5                    # ab dieser Serie ist ein Kopfgeld ausgesetzt
STATS_EVERY = 5               # Zwischenstand alle n Runden
BALANCE_LEAD = 5              # Rundenvorsprung seit dem letzten Ausgleich
YES = {"j", "ja", "y", "yes"}
COMMANDS = {"waffe", "weapon", "w", "reset", "normal", "ausgleich", "balance"}  # !j: vote_open()

WEAPONS = {
    "deagle": "weapon_deagle", "deag": "weapon_deagle", "r8": "weapon_revolver", "revolver": "weapon_revolver",
    "usp": "weapon_usp_silencer", "glock": "weapon_glock", "p250": "weapon_p250", "p2000": "weapon_hkp2000",
    "fiveseven": "weapon_fiveseven", "57": "weapon_fiveseven", "tec9": "weapon_tec9", "cz": "weapon_cz75a",
    "dualies": "weapon_elite", "ak": "weapon_ak47", "ak47": "weapon_ak47", "m4": "weapon_m4a1",
    "m4a4": "weapon_m4a1", "m4a1": "weapon_m4a1_silencer", "m4a1s": "weapon_m4a1_silencer",
    "galil": "weapon_galilar", "famas": "weapon_famas", "aug": "weapon_aug", "sg": "weapon_sg556",
    "krieg": "weapon_sg556", "awp": "weapon_awp", "scout": "weapon_ssg08", "ssg": "weapon_ssg08",
    "mp9": "weapon_mp9", "mac10": "weapon_mac10", "mp7": "weapon_mp7", "mp5": "weapon_mp5sd",
    "ump": "weapon_ump45", "p90": "weapon_p90", "bizon": "weapon_bizon", "nova": "weapon_nova",
    "xm": "weapon_xm1014", "mag7": "weapon_mag7", "sawedoff": "weapon_sawedoff",
    "negev": "weapon_negev", "m249": "weapon_m249", "messer": "", "knife": "",
}

_TEAM = {"TERRORIST": "T", "CT": "CT"}
_SWITCH = re.compile(r'^"(?P<name>.*?)<(?P<uid>-?\d+)><(?P<sid>[^>]*)>" switched from team <[^>]*> to <(?P<team>[^>]*)>')
_ROUND_END = re.compile(r'^Team "(?P<team>CT|TERRORIST)" triggered "[^"]+" \(CT "(?P<ct>\d+)"\) \(T "(?P<t>\d+)"\)')
_ON_MAP = re.compile(r'(?:on map|Match_Start" on|Loading map|Started map) "(?P<map>[^"]+)"')
_lock = threading.Lock()


# --------------------------------------------------------------------------
# Zustand
# --------------------------------------------------------------------------

def _path(config_path: Path) -> Path:
    return Path(config_path).parent / STATE_FILE


def load(config_path: Path) -> dict:
    try:
        return json.loads(_path(config_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(config_path: Path, st: dict) -> None:
    p = _path(config_path)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st), encoding="utf-8")
    tmp.replace(p)


def _new_game(st: dict) -> None:
    st.update(streak={}, pairs={}, game={}, rounds=0, since={"T": 0, "CT": 0},
              alive={}, clutch=None, round_t=None)


def fun_maps(cfg: dict) -> tuple[str, ...]:
    return tuple(cfg.get("aim_fun_maps") or DEFAULT_MAPS)


def active(cfg: dict, st: dict) -> bool:
    return st.get("map") in fun_maps(cfg)


# --------------------------------------------------------------------------
# Hilfen
# --------------------------------------------------------------------------

def _key(name: str, sid: str) -> str:
    return f"BOT:{name}" if sid == "BOT" else sid


def _ts(stamp: str) -> float | None:
    try:
        return datetime.strptime(stamp[:21], "%m/%d/%Y - %H:%M:%S").timestamp() + (
            float("0" + stamp[21:]) if stamp[21:].startswith(".") else 0)
    except ValueError:
        return None


def _seen(st: dict, name: str, uid: str, sid: str, team: str | None = None) -> str:
    k = _key(name, sid)
    r = st.setdefault("roster", {}).setdefault(k, {})
    r.update(name=name, uid=int(uid), bot=sid == "BOT")
    if team is not None:
        r["team"] = _TEAM.get(team)
    return k


def _name(st: dict, k: str) -> str:
    return st.get("roster", {}).get(k, {}).get("name", k)


def _humans(st: dict) -> list[dict]:
    return [r for r in st.get("roster", {}).values() if not r.get("bot") and r.get("team")]


def _stats(st: dict, k: str) -> dict:
    return st.setdefault("game", {}).setdefault(k, {"k": 0, "d": 0, "hs": 0, "best": 0, "fast": None})


def _hs(s: dict) -> str:
    return f"{round(100 * s['hs'] / s['k'])} %" if s["k"] else "-"


def _chat(st: dict, text: str) -> list[str]:
    """Eine Chatzeile an jeden Menschen (coach_chat), nie per say."""
    from .ingame_coach import chat_line
    return [chat_line(r["uid"], text) for r in _humans(st)]


# --------------------------------------------------------------------------
# Log-Zeilen
# --------------------------------------------------------------------------

def on_lines(cfg: dict, config_path: Path, lines: list[str], send) -> dict:
    """Stapel verarbeiten; gibt den Zustand zurueck (fuer Tests)."""
    with _lock:
        st = load(config_path)
        if "game" not in st:
            _new_game(st)
        out: list[str] = []
        gives: list[str] = []
        for raw in lines:
            m = ss._LINE.match(raw)
            if not m:
                continue
            out += _line(cfg, st, m.group(1), m.group(2), gives)
        save(config_path, st)
    if out or gives:
        send(*gives, *out)
    return st


def weapon_command(weapon: str | None) -> str:
    """Plugin 1.28.0: None = aus, "" = nur Messer, sonst weapon_xyz."""
    return f"coach_weapon_mode {'off' if weapon is None else weapon or 'knife'}"


def _line(cfg: dict, st: dict, stamp: str, body: str, gives: list[str]) -> list[str]:
    if (mm := _ON_MAP.search(body)) and st.get("map") != mm.group("map"):
        st["map"] = mm.group("map")
        st["roster"] = {}
        _new_game(st)
        if st.pop("weapon", None) is not None:
            gives.append(weapon_command(None))
    if body.startswith('World triggered "Match_Start"'):
        _new_game(st)
        return []
    if (s := _SWITCH.match(body)):
        _seen(st, s["name"], s["uid"], s["sid"], s["team"])
        return []
    if (lv := ss._LEAVE.match(body)):
        st.get("roster", {}).pop(_key(lv["aname"], lv["asid"]), None)
        return []
    if not active(cfg, st):
        return []
    if body.startswith('World triggered "Round_Start"'):
        st["round_t"] = _ts(stamp)
        st["clutch"] = None
        st["alive"] = {k: r["team"] for k, r in st.get("roster", {}).items() if r.get("team")}
        if (w := st.get("weapon")) is not None:
            # Das Plugin ersetzt selbst beim Spawn/Aufheben; hier nur auffrischen, falls
            # es nach Mapwechsel oder Neustart nichts mehr weiss.
            gives.append(weapon_command(w))
        return []
    if (k := ss._KILL.match(body)):
        return _kill(st, stamp, k)
    if (at := ss._ATTACK.match(body)):
        _seen(st, at["aname"], at["auid"], at["asid"], at["ateam"])
        _seen(st, at["vname"], at["vuid"], at["vsid"], at["vteam"])
        return []
    if (e := _ROUND_END.match(body)):
        return _round_end(cfg, st, _TEAM[e["team"]], int(e["t"]), int(e["ct"]))
    if body.startswith("Game Over:"):
        return _game_over(st)
    return []


def _kill(st: dict, stamp: str, m: re.Match) -> list[str]:
    a = _seen(st, m["aname"], m["auid"], m["asid"], m["ateam"])
    v = _seen(st, m["vname"], m["vuid"], m["vsid"], m["vteam"])
    st.get("alive", {}).pop(v, None)
    out = []
    if a != v and m["ateam"] != m["vteam"]:
        an, vn = _name(st, a), _name(st, v)
        sa, sv = _stats(st, a), _stats(st, v)
        sa["k"] += 1
        sv["d"] += 1
        if "headshot" in (m["flags"] or ""):
            sa["hs"] += 1
        if st.get("round_t") and (t := _ts(stamp)) is not None and t >= st["round_t"]:
            dt = round(t - st["round_t"], 1)
            if sa["fast"] is None or dt < sa["fast"]:
                sa["fast"] = dt
        streak = st.setdefault("streak", {})
        streak[a] = streak.get(a, 0) + 1
        sa["best"] = max(sa["best"], streak[a])
        bounty = streak.get(v, 0) >= BOUNTY
        if bounty:
            out.append(f"KOPFGELD! {an} beendet die {streak[v]}er-Serie von {vn}.")
        streak[v] = 0
        pairs = st.setdefault("pairs", {})
        back = pairs.pop(f"{v}|{a}", 0)
        if back >= NEMESIS_AT[0] and not bounty:  # im 1v1 ist das derselbe Moment
            out.append(f"RACHE! {an} erwischt endlich {vn} - nach {back} Toden in Folge.")
        n = pairs[f"{a}|{v}"] = pairs.get(f"{a}|{v}", 0) + 1
        if n in NEMESIS_AT:
            out.append(f"NEMESIS: {an} hat {vn} {n}x in Folge erwischt.")
    # Clutch: einer allein gegen 2+ - Kandidat merken, Ansage erst beim Rundensieg
    alive = st.get("alive", {})
    if not st.get("clutch"):
        for side, other in (("T", "CT"), ("CT", "T")):
            mine = [k for k, s in alive.items() if s == side]
            enemies = sum(1 for s in alive.values() if s == other)
            if len(mine) == 1 and enemies >= 2:
                st["clutch"] = {"k": mine[0], "side": side, "vs": enemies}
    return [line for text in out for line in _chat(st, text)]


def _round_end(cfg: dict, st: dict, winner: str, t: int, ct: int) -> list[str]:
    out = []
    c = st.get("clutch")
    if c and c["side"] == winner:
        out.append(f"CLUTCH! {_name(st, c['k'])} gewinnt das 1v{c['vs']}.")
    st["clutch"] = None
    st["score"] = {"T": t, "CT": ct}
    st["rounds"] = st.get("rounds", 0) + 1
    since = st.setdefault("since", {"T": 0, "CT": 0})
    since[winner] = since.get(winner, 0) + 1
    if st["rounds"] % STATS_EVERY == 0 and (table := _table(st)):
        out.append(f"Zwischenstand nach {st['rounds']} Runden: {table}")
    lines = [line for text in out for line in _chat(st, text)]
    return lines + _balance(cfg, st)


def _table(st: dict, limit: int = 6) -> str:
    rows = sorted(((k, s) for k, s in st.get("game", {}).items() if k in st.get("roster", {})),
                  key=lambda x: (-x[1]["k"], x[1]["d"]))
    return " · ".join(f"{_name(st, k)} {s['k']}:{s['d']} ({_hs(s)} HS)" for k, s in rows[:limit])


def _game_over(st: dict) -> list[str]:
    game = {k: s for k, s in st.get("game", {}).items() if s["k"] or s["d"]}
    if not game:
        return []
    names = {k: st.get("roster", {}).get(k, {}).get("name") or k for k in game}
    extras = []
    best = max(game.items(), key=lambda x: x[1]["best"])
    if best[1]["best"] >= 2:
        extras.append(f"Laengste Serie: {names[best[0]]} ({best[1]['best']})")
    fast = [(k, s) for k, s in game.items() if s["fast"] is not None]
    if fast:
        k, s = min(fast, key=lambda x: x[1]["fast"])
        extras.append(f"Schnellster Kill: {names[k]} ({str(s['fast']).replace('.', ',')} s)")
    aim = [(k, s) for k, s in game.items() if s["k"] >= 5]
    if aim:
        k, s = max(aim, key=lambda x: x[1]["hs"] / x[1]["k"])
        extras.append(f"Beste HS-Quote: {names[k]} ({_hs(s)})")
    st["last_game"] = {"map": st.get("map"), "at": time.time(), "lines": extras}
    out = []
    if extras:
        out.append("Spielende - " + " · ".join(extras))
    if (table := _table(st)):
        out.append(f"Endstand: {table}")
    return [line for text in out for line in _chat(st, text)]


def _balance(cfg: dict, st: dict) -> list[str]:
    if st.get("balance_off") or cfg.get("aim_fun_balance") is False:
        return []
    since = st.get("since", {})
    lead, trail = ("T", "CT") if since.get("T", 0) > since.get("CT", 0) else ("CT", "T")
    if since.get(lead, 0) - since.get(trail, 0) < BALANCE_LEAD:
        return []
    humans = {k: r for k, r in st.get("roster", {}).items() if not r.get("bot") and r.get("team")}
    leaders = [k for k, r in humans.items() if r["team"] == lead]
    trailers = [k for k, r in humans.items() if r["team"] == trail]
    if not leaders or len(leaders) < len(trailers) or len(leaders) + len(trailers) < 3:
        return []  # 1v1 oder der Einzelne fuehrt gegen zwei: Wechsel macht es nur schlimmer

    def strength(k):
        s = _stats(st, k)
        return (s["k"] + 1) / (s["d"] + 1)

    best = max(leaders, key=strength)
    cmds = [f"coach_team {humans[best]['uid']} {trail.lower()}"]
    moved = [f"{_name(st, best)} -> {trail}"]
    humans[best]["team"] = trail
    if len(leaders) == len(trailers):
        weak = min(trailers, key=strength)
        cmds.append(f"coach_team {humans[weak]['uid']} {lead.lower()}")
        moved.append(f"{_name(st, weak)} -> {lead}")
        humans[weak]["team"] = lead
    st["since"] = {"T": 0, "CT": 0}
    text = (f"AUSGLEICH: {lead} fuehrt mit {BALANCE_LEAD}+ Runden - " + ", ".join(moved)
            + ". (Abschalten: !ausgleich aus)")
    return _chat(st, text) + cmds


# --------------------------------------------------------------------------
# Chat-Befehle
# --------------------------------------------------------------------------

def handle(cfg: dict, config_path: Path, send, sid: str, name: str, cmd: str, arg: str,
           now: float | None = None) -> list[str]:
    """Antwortzeilen (gehen per coach_chat an alle); Ausfuehrung direkt per RCON."""
    now = now or time.time()
    with _lock:
        st = load(config_path)
        if not active(cfg, st):
            return ["Nur auf " + ", ".join(fun_maps(cfg)) + " (!map aim)."]
        if cmd in ("ausgleich", "balance"):
            word = arg.strip().lower()
            if word in ("aus", "off", "0"):
                st["balance_off"] = True
            elif word in ("an", "on", "1"):
                st["balance_off"] = False
            save(config_path, st)
            state = "aus" if st.get("balance_off") or cfg.get("aim_fun_balance") is False else "an"
            return [f"Automatischer Ausgleich ist {state} (ab {BALANCE_LEAD} Runden Vorsprung). !ausgleich an/aus"]
        if cmd in ("reset", "normal") or arg.strip().lower() in ("aus", "off", "normal", "reset"):
            # Zurueck zu den Arena-Waffen der Map: ohne Abstimmung (Wunsch 21.11.)
            had = st.pop("weapon", None) is not None
            st.pop("weapon_vote", None)
            save(config_path, st)
            if send:
                send(weapon_command(None))
            return ["Waffen-Runde beendet - ab dem naechsten Spawn wieder die Arena-Waffen."
                    if had else "Keine Waffen-Runde aktiv - es gelten die Arena-Waffen."]
        vote = st.get("weapon_vote")
        if vote and now > vote["until"]:
            vote = None
        if cmd in YES:
            if not vote:
                return ["Gerade laeuft keine Waffen-Abstimmung. Start: !w <waffe>"]
            target = vote["target"]
        else:
            word = arg.strip().lower().replace("-", "").replace(" ", "")
            if not word:
                cur = st.get("weapon")
                return [("Waffen-Runde: " + (cur.removeprefix("weapon_") or "nur Messer")
                         if cur is not None else "Keine Waffen-Runde aktiv.")
                        + " Start: !w <deagle|ak|awp|scout|usp|messer|...> · Zustimmen: !j · Zurueck: !reset"]
            if word not in WEAPONS:
                return [f"Unbekannte Waffe '{arg}'. Z. B.: deagle, ak, m4a1, awp, scout, usp, r8, messer."]
            target = WEAPONS[word]
            if not vote or vote["target"] != target:
                vote = {"target": target, "votes": [], "until": now + 30}
        humans = _humans(st)
        needed = len(humans) // 2 + 1
        if sid not in vote["votes"]:
            vote["votes"].append(sid)
        label = target.removeprefix("weapon_") or "nur Messer"
        if len(vote["votes"]) >= needed:
            st.pop("weapon_vote", None)
            st["weapon"] = target
            save(config_path, st)
            if send:
                send(weapon_command(target))
            return [f"Mehrheit: ab sofort {label}. Zurueck zu den Arena-Waffen: !reset"]
        st["weapon_vote"] = vote
        save(config_path, st)
        return [f"{name} will {label} - zustimmen mit !j ({len(vote['votes'])}/{needed}, 30 s)."]


def last_game_lines(config_path: Path, max_age: float = 600) -> list[str]:
    """Extras des gerade beendeten Spiels fuer die Discord-Meldung."""
    lg = load(config_path).get("last_game") or {}
    return list(lg.get("lines") or []) if time.time() - lg.get("at", 0) <= max_age else []


def chat_reply(config_path: Path, lines: list[str]) -> list[str]:
    st = load(config_path)
    return [c for text in lines for c in _chat(st, text)]


def vote_open(config_path: Path, now: float | None = None) -> bool:
    """Laeuft eine Waffen-Abstimmung? Dann gehoert !j/!ja ihr, sonst der Map-Abstimmung."""
    vote = load(config_path).get("weapon_vote")
    return bool(vote) and (now or time.time()) <= vote["until"]
