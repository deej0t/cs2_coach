"""Duell-Training auf dem eigenen CS2-Server: verlorene Duelle aus den eigenen Demos nachspielen.

Jeder Tod in einem Export (``kill_positions``, t="d") ist eine Situation:
deine Position, die des Gegners, seine Waffe. Der Coach setzt dich und einen
Bot zu Rundenbeginn genau dorthin, gibt dem Bot die Waffe deines Gegners und
laesst ihn nach einer Startverzoegerung los. Die Bot-KI pusht dann selbst.

Gesteuert wird per Chat (nur der Besitzer, ``steam_id`` in der Config):
``!training <map>``, ``!skip``, ``!leichter``, ``!schwerer``, ``!stop``.
Der Regisseur reagiert auf das Server-Log (server_live.on_batch) und
steuert per RCON; nur das Versetzen braucht das eigene Plugin.

Versetzt wird ueber das eigene Server-Plugin ``coach_tp`` (server_plugin/,
Metamod + CounterStrikeSharp): ``coach_tp <userid> x y z pitch yaw``. Am
30.10.2026 gemessen bewegt kein Konsolenbefehl einen echten Spieler - mit
``ent_setpos`` stand dj im Training weiter am T-Spawn; Bots liessen sich nur
eingefroren versetzen, und die Zuordnung Entity <-> Spieler war fehleranfaellig.
Die userids stehen ohnehin im Log (``dj<0>``, ``Orlow<3><BOT>``).

Weitere Messungen vom 30.10., die den Ablauf bestimmen:
- ``bot_loadout m4a1`` gibt Bots die Waffe zum naechsten Rundenstart.
- Eingefrorene Bots (``bot_freeze 1``) schiessen nicht - darum wird nach
  der Freeze-Time nicht mehr eingefroren; Schwierigkeit = ``bot_difficulty``.
- Start ``LEAD_SECONDS`` vor deinem Tod an den Demo-Positionen (``prepare``):
  am Todesort war man "zu schnell konfrontiert".
- Ein Mapwechsel setzt ``sv_hibernate_when_empty`` zurueck; im Ruhemodus
  laeuft keine Spiellogik.
- Ein ``bot_add_ct`` brachte zwei Bots; jeder weitere wird gekickt.

Nur Maps aus ``training_maps`` (Premier/FACEIT-Pool). Die Exporte sagen
nicht, ob ein Match Premier war - darum eine feste Liste.
"""

from __future__ import annotations

import base64
import html
import json
import math
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from . import server_stats as ss
from .practice import _calc_view_angle, _map_to_bspname

# Cache auf Wunsch des Spielers dabei ("die Map Cache fehlt"); er spielt sie oft (20 Matches).
DEFAULT_MAPS = ["de_ancient", "de_anubis", "de_cache", "de_dust2", "de_inferno", "de_mirage", "de_nuke",
                "de_overpass", "de_train"]
STATE_FILE = ".training_server.json"
LOCK_FILE = ".training_server.lock"
MAX_SCENARIOS = 20
MIN_DISTANCE = 150  # naeher = Messer/Nahkampf-Zufall, kein Duell
FREEZE_TIME = 3
# bot_difficulty 0-3. Start bei 2: auf 1 gewann der Spieler am 30.10. 28 von 29 Duellen
# (damals allerdings vor allem, weil der Bot auf seinem Weg nicht zielen konnte).
START_LEVEL = 3
MAX_LEVEL = 5
# Stufe 4 und 5: Zielhilfe im Plugin (coach_bot_skill <uid> <reaktion_ms> <halten_ms>).
# Auch mit der KI-Uebernahme beim ersten Sichtkontakt gewann der Spieler am 30.10.
# 19 von 20 Duellen auf bot_difficulty 3 ("selbst die Stufe 3 ist zu schwach"); der
# Bot traf nur zweimal zuerst, in 15 Siegen gar nicht. 250 ms ~ menschliche
# Reaktionszeit, 150 ms ~ sehr guter Spieler.
AIM = {4: (250, 300), 5: (150, 800)}
# Start so viele Sekunden VOR deinem Tod, an den Positionen aus der Demo.
# Rueckmeldung nach dem ersten Training: am Todesort "ist man zu schnell
# konfrontiert" - dort laeuft das Duell schon.
LEAD_SECONDS = 3
TICKRATE = 64
PATH_HZ = 16  # Wegpunkte pro Sekunde fuer den Bot; das Plugin interpoliert dazwischen
PATH_CHUNK = 8  # noqa: E501
# demoparser2 liefert das Inventar mit Anzeigenamen (gemessen 30.10.:
# "Glock-18", "M4A4", "High Explosive Grenade", "Skeleton Knife").
WEAPONS = {
    "Glock-18": "glock", "USP-S": "usp_silencer", "P2000": "hkp2000", "P250": "p250",
    "Five-SeveN": "fiveseven", "Tec-9": "tec9", "CZ75-Auto": "cz75a", "Desert Eagle": "deagle",
    "R8 Revolver": "revolver", "Dual Berettas": "elite", "MAC-10": "mac10", "MP9": "mp9",
    "MP7": "mp7", "MP5-SD": "mp5sd", "UMP-45": "ump45", "P90": "p90", "PP-Bizon": "bizon",
    "Galil AR": "galilar", "FAMAS": "famas", "AK-47": "ak47", "M4A4": "m4a1",
    "M4A1-S": "m4a1_silencer", "SG 553": "sg556", "AUG": "aug", "SSG 08": "ssg08", "AWP": "awp",
    "G3SG1": "g3sg1", "SCAR-20": "scar20", "Nova": "nova", "XM1014": "xm1014", "MAG-7": "mag7",
    "Sawed-Off": "sawedoff", "M249": "m249", "Negev": "negev", "Zeus x27": "taser",
    "Flashbang": "flashbang", "Smoke Grenade": "smokegrenade", "High Explosive Grenade": "hegrenade",
    "Molotov": "molotov", "Incendiary Grenade": "incgrenade", "Decoy Grenade": "decoy",
}
GRENADES = {"weapon_flashbang", "weapon_smokegrenade", "weapon_hegrenade", "weapon_molotov",
            "weapon_incgrenade", "weapon_decoy"}  # Punkte pro RCON-Befehl (10 Punkte mit voller Genauigkeit waren 869 Zeichen)
NON_GUNS = ("knife", "bayonet", "hegrenade", "inferno", "molotov", "incgrenade", "flashbang",
            "smokegrenade", "decoy", "world", "taser", "planted_c4")

MODE_COMMANDS = {"retake": "retake", "halten": "halten", "sitehold": "halten", "hold": "halten",
                 "situation": "situation", "duell": "duell"}
# Training zu mehreren: der Besitzer laedt ein, der Freund sagt zu. Mitmachen und
# Aussteigen darf jeder - das sind die einzigen Befehle ohne Besitzer-Pruefung.
PARTY_COMMANDS = {"einladen", "ausladen", "mitmachen", "aussteigen"}
PARTY_FILE = ".training_party.json"
COMMANDS = {"training", "train", "skip", "leichter", "schwerer", "stop", "pause", "weiter", "menu", "menue",
            *MODE_COMMANDS, *PARTY_COMMANDS}
SESSIONS_DIR = "training_sessions"
ITEMS_FILE = ".training_items.json"  # Szenarien mit mehreren Gegnern (bis ~1 MB, nicht in den Zustand)
MODE_ALIASES = {"duell": "duell", "duel": "duell", "situation": "situation", "multi": "situation",
                "retake": "retake", "halten": "halten", "hold": "halten", "site": "halten"}
GREEN, RED, YELLOW, PURPLE, GREY = "#4ade80", "#f87171", "#fbbf24", "#9184d9", "#aaaaaa"
IDLE_ROUNDS = 2  # so viele Runden ohne Treffer in Folge = pausieren
PAUSE_TIMEOUT = 900  # Sekunden pausiert, dann endet das Training

# Ausruestung kommt pro Duell aus der Demo (coach_give): du bekommst dein
# damaliges Inventar, der Bot die Schusswaffen deines Gegners ohne Messer.
# Erst wurden Messer und Pistole fuer alle abgeschaltet, damit Bots nicht mit
# dem Messer dastehen - dann hattest du nur noch die AK ("nicht mal die
# Pistole, Utilities oder Messer"). C4 bleibt aus.
SETUP = [
    "sv_cheats 1", "sv_hibernate_when_empty 0", "mp_autoteambalance 0", "mp_limitteams 0",
    "bot_quota_mode normal", "bot_join_after_player 0", "bot_kick", "bot_quota 0",
    "bot_freeze 0", "bot_chatter off", "mp_warmuptime 999", "mp_warmup_start",
    f"mp_freezetime {FREEZE_TIME}", "mp_roundtime 1", "mp_round_restart_delay 2",
    "mp_maxrounds 99", "mp_buytime 0", "mp_free_armor 2", "mp_death_drop_gun 0",
    "mp_t_default_primary weapon_ak47", "mp_ct_default_primary weapon_m4a1",
    "mp_give_player_c4 0",
    "mp_halftime 0", "mp_match_can_clinch 0", "mp_ignore_round_win_conditions 0",
    # Mitspieler starten im Duell an deiner Stelle - durchlaessig, sonst stecken sie ineinander.
    "mp_solid_teammates 0",
]
RESTORE = ["bot_kick", "bot_quota 0", "bot_freeze 0", 'bot_loadout ""', "mp_warmuptime 30",
           'mp_t_default_primary ""', 'mp_ct_default_primary ""',
           "mp_t_default_secondary weapon_glock", "mp_ct_default_secondary weapon_hkp2000",
           "mp_t_default_melee weapon_knife", "mp_ct_default_melee weapon_knife", "mp_give_player_c4 1",
           "mp_ignore_round_win_conditions 0", "sv_cheats 0", "sv_hibernate_when_empty 1"]
# Nach dem Training noch so lange auf der Map bleiben (Fazit lesen, gleich weiter
# trainieren). Vorher wechselte die Map sofort - "abrupt".
FAREWELL_SECONDS = 30

# Teamwechsel-Zeilen haben ein Feld weniger als Kill-Zeilen (gemessen):
# "K<4><BOT>" switched from team <Unassigned> to <CT>
_TEAM = re.compile(r'^"(?P<aname>.*?)<(?P<auid>-?\d+)><(?P<asid>[^>]*)>" switched from team <[^>]*> to <(?P<to>[^>]+)>')
# Nach einem Mapwechsel steht nur "Loading map" im Log, kein "Started map".
_LOADING = re.compile(r'^Loading map "(?P<map>[^"]+)"')
SETUP_WAIT = 8  # Sekunden nach "Loading map": erst dann ist die server.cfg durch
LOAD_TIMEOUT = 120  # Map bis dahin nicht geladen = Training verwerfen


@dataclass
class Scenario:
    map: str
    x: float
    y: float
    z: float
    ex: float
    ey: float
    ez: float
    weapon: str
    opponent: str
    source: str  # z. B. "27.10. R3"
    tick: int = 0  # Tick deines Todes in der Demo
    demo: str = ""  # Dateiname der Demo (match.demo_file)
    export: str = ""  # Export-Datei (Link in der Auswertung)
    round: int = 0


# --------------------------------------------------------------------------
# Situationen aus den Exporten
# --------------------------------------------------------------------------

def _angle(v: float) -> float:
    """Demo-Winkel (0..360) auf -180..180 bringen; Teleport erwartet das."""
    v = float(v) % 360
    return round(v - 360 if v > 180 else v, 1)


def pre_positions(demo_path: Path, me: str, ticks: list[int], lead: float = LEAD_SECONDS,
                  parser_factory=None) -> dict[int, dict]:
    """Positionen und Blickwinkel von dir und deinem Killer ``lead`` Sekunden vor jedem Tod.

    Gemessen am 30.10. an einer 244-MB-Demo: 3,3 s fuer die Events, 0,3 s fuer
    die Ticks. Nie vor das Ende der Freeze-Time derselben Runde zurueck.
    Ergebnis je Todes-Tick: {"me": [x,y,z,pitch,yaw], "foe": [...], "lead": s,
    "path": [[x,y,z,pitch,yaw], ...]} - "path" ist der Weg deines Killers vom
    Start bis zu deinem Tod, PATH_HZ Punkte pro Sekunde (gemessen: 49 Punkte
    fuer 3 s in 0,3 s; im Mittel 118 u/s, Spitze 236 - Laufen und Gehen).
    """
    if parser_factory is None:
        from demoparser2 import DemoParser as parser_factory
    parser = parser_factory(str(demo_path))
    deaths = parser.parse_event("player_death")
    freeze = parser.parse_event("round_freeze_end")
    freeze_ticks = sorted(int(t) for t in freeze["tick"]) if len(freeze) else []
    wanted: dict[int, tuple[int, str]] = {}
    for tick in ticks:
        row = deaths[(deaths["tick"] == tick) & (deaths["user_steamid"].astype(str) == me)]
        if row.empty:
            continue
        start = tick - int(lead * TICKRATE)
        before = [f for f in freeze_ticks if f <= tick]
        if before:
            start = max(start, before[-1])
        wanted[tick] = (start, str(row.iloc[0]["attacker_steamid"]))
    if not wanted:
        return {}
    step = TICKRATE // PATH_HZ
    path_ticks = {t: list(range(start, t + 1, step)) for t, (start, _) in wanted.items()}
    all_ticks = sorted({s for s, _ in wanted.values()} | {x for ts in path_ticks.values() for x in ts})
    df = parser.parse_ticks(["X", "Y", "Z", "pitch", "yaw", "inventory", "active_weapon_name"], ticks=all_ticks)
    out = {}
    for tick, (start, killer) in wanted.items():
        def at(sid, when):
            rows = df[df["tick"] == when]
            r = rows[rows["steamid"].astype(str) == sid]
            if r.empty:
                return None
            r = r.iloc[0]
            return [round(float(r["X"]), 1), round(float(r["Y"]), 1), round(float(r["Z"]), 1),
                    _angle(r["pitch"]), _angle(r["yaw"])]

        def items(sid, when):
            """[aktive Waffe, alle Items] als Klassennamen; unbekannte Namen fallen weg."""
            rows = df[(df["tick"] == when) & (df["steamid"].astype(str) == sid)]
            if rows.empty:
                return []
            r = rows.iloc[0]
            raw = r.get("inventory")
            try:
                inv = [classname(x) for x in raw] if not isinstance(raw, str) else []
            except TypeError:
                inv = []  # NaN: kein Inventar fuer diesen Spieler/Tick
            return [classname(r.get("active_weapon_name") or "")] + [x for x in inv if x]

        mine, foe = at(me, start), at(killer, start)
        if mine and foe:
            path = [pt for pt in (at(killer, x) for x in path_ticks[tick]) if pt]
            out[tick] = {"me": mine, "foe": foe, "lead": round((tick - start) / TICKRATE, 1), "path": path,
                         "me_items": items(me, start), "foe_items": items(killer, start)}
    return out


def classname(display: str) -> str:
    """Anzeigename aus der Demo -> weapon_*; jedes Messer -> weapon_knife; unbekannt -> ""."""
    name = str(display or "")
    if any(k in name.lower() for k in ("knife", "bayonet", "karambit", "daggers")):
        return "weapon_knife"
    return f"weapon_{WEAPONS[name]}" if name in WEAPONS else ""


def lead_seconds(cfg: dict) -> float:
    """Vorlauf in Sekunden (Config ``training_lead_seconds``, Standard LEAD_SECONDS)."""
    try:
        return max(0.0, min(15.0, float(cfg.get("training_lead_seconds") or LEAD_SECONDS)))
    except (TypeError, ValueError):
        return float(LEAD_SECONDS)


def prepare(cfg: dict, config_path: Path, scenarios: list[Scenario], parser_factory=None) -> dict[int, dict]:
    """Vorlauf-Positionen fuer die gewaehlten Situationen, je Demo zwischengespeichert.

    Rueckgabe: Index in ``scenarios`` -> Positionen. Situationen ohne Demo
    (geloescht, nie geladen) fehlen und starten am Todesort.
    """
    folder = (cfg.get("demo_folder") or "").strip()
    me = str(cfg.get("steam_id") or "")
    lead = lead_seconds(cfg)
    if not folder or not me:
        return {}
    cache_dir = Path(config_path).parent / "training_cache"
    out: dict[int, dict] = {}
    by_demo: dict[str, list[int]] = {}
    for i, sc in enumerate(scenarios):
        if sc.demo and sc.tick:
            by_demo.setdefault(sc.demo, []).append(i)
    for demo, idx in by_demo.items():
        cache = cache_dir / f"{Path(demo).stem}.json"
        try:
            known = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            known = {}
        key = f"v3-lead{lead:g}"  # v2: mit Weg des Gegners, v3: mit Inventar
        missing = [scenarios[i].tick for i in idx if str(scenarios[i].tick) not in known.get(key, {})]
        path = Path(folder) / demo
        if missing and path.exists():
            try:
                found = pre_positions(path, me, missing, lead, parser_factory=parser_factory)
            except Exception as e:  # noqa: BLE001 - kaputte Demo: diese Duelle starten am Todesort
                print(f"[training] {demo}: {e}", flush=True)
                found = {}
            known.setdefault(key, {}).update({str(t): v for t, v in found.items()})
            cache_dir.mkdir(exist_ok=True)
            cache.write_text(json.dumps(known), encoding="utf-8")
        for i in idx:
            pre = known.get(key, {}).get(str(scenarios[i].tick))
            if pre:
                out[i] = pre
    return out

def _is_gun(weapon: str) -> bool:
    return bool(weapon) and not weapon.startswith(NON_GUNS)


def build_scenarios(exports_dir: Path, maps: list[str] | None = None) -> dict[str, list[Scenario]]:
    """Alle verlorenen Duelle mit Schusswaffe, je Map, neueste zuerst."""
    allowed = set(maps or DEFAULT_MAPS)
    out: dict[str, list[Scenario]] = {}
    files = sorted(Path(exports_dir).glob("*_coach.json"), reverse=True) if exports_dir else []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        bsp = _map_to_bspname(data.get("match", {}).get("map", ""))
        if bsp not in allowed:
            continue
        date = data.get("match", {}).get("date", "")
        label = f"{date[8:10]}.{date[5:7]}." if len(date) >= 10 else f.stem[:10]
        for kp in data.get("kill_positions") or []:
            if kp.get("t") != "d" or not _is_gun(kp.get("w", "")):
                continue
            dist = ((kp["x"] - kp["ex"]) ** 2 + (kp["y"] - kp["ey"]) ** 2) ** 0.5
            if dist < MIN_DISTANCE:
                continue
            out.setdefault(bsp, []).append(Scenario(
                bsp, kp["x"], kp["y"], kp["z"], kp["ex"], kp["ey"], kp["ez"], kp["w"],
                kp.get("e", "?"), f"{label} R{kp.get('r', '?')}", int(kp.get("tk") or 0),
                data.get("match", {}).get("demo_file", ""), f.name, int(kp.get("r") or 0)))
    return out


# --------------------------------------------------------------------------
# Zustand (Datei neben der Config, gesperrt ueber mehrere Gunicorn-Worker)
# --------------------------------------------------------------------------

class _Lock:
    def __init__(self, config_path: Path):
        self.path = Path(config_path).parent / LOCK_FILE

    def __enter__(self):
        for _ in range(100):
            try:
                os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > 30:
                        self.path.unlink(missing_ok=True)  # verwaist
                except FileNotFoundError:
                    pass  # gerade freigegeben - gleich nochmal versuchen (stuerzte am 30.10. ab)
                time.sleep(0.05)
        raise TimeoutError("Trainings-Zustand gesperrt")

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def load_state(config_path: Path) -> dict:
    try:
        return json.loads((Path(config_path).parent / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(config_path: Path, state: dict) -> None:
    path = Path(config_path).parent / STATE_FILE
    if not state:
        path.unlink(missing_ok=True)
        return
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def is_active(config_path: Path) -> bool:
    return (Path(config_path).parent / STATE_FILE).exists()


# --------------------------------------------------------------------------
# Regisseur
# --------------------------------------------------------------------------

def _say(text: str) -> str:
    from .server_live import PREFIX, _safe
    return f"say {_safe(PREFIX + ' ' + text)[:120]}"


def _humans(status: str) -> list[str]:
    """Namen der verbundenen Menschen aus der RCON-Ausgabe von ``status``."""
    out = []
    for line in status.splitlines():
        m = re.match(r"^\s*\d+\s+(\S+)\s+\d+\s+\d+\s+\w+\s+\d+\s+\S+\s+'(.*)'\s*$", line)
        if m and m.group(1) != "BOT":
            out.append(m.group(2))
    return out


def _fmt(pt) -> str:
    """Punkt als "x y z pitch yaw" mit einer Nachkommastelle (RCON-Zeilen bleiben kurz)."""
    return " ".join(f"{float(v):.1f}" for v in pt)


# --------------------------------------------------------------------------
# Anzeige im Spiel (Plugin 1.3.0: coach_hud, coach_menu)
# --------------------------------------------------------------------------

def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def hud(uid, seconds: float, body: str) -> str:
    """Grosse Einblendung in der Bildschirmmitte; seconds=0 loescht sie."""
    return f"coach_hud {uid} {seconds:g} {_b64(body or ' ')}"


def menu(uid, title: str, items: list) -> str:
    return f"coach_menu {uid} {_b64(json.dumps({'title': title, 'items': items}, ensure_ascii=False))}"


def _font(text: str, color: str, size: str = "m") -> str:
    return f"<font class='fontSize-{size}' color='{color}'>{text}</font>"


def weapon_label(name: str) -> str:
    """"m4a1" / "weapon_m4a1" -> "M4A4" (Anzeigename wie im Spiel)."""
    short = str(name).removeprefix("weapon_")
    return next((k for k, v in WEAPONS.items() if v == short), short.upper())


def direction(me, foe) -> str:
    """Woher kommt der Gegner, relativ zu deiner Blickrichtung? "links vorne, 24 m"."""
    bearing = math.degrees(math.atan2(foe[1] - me[1], foe[0] - me[0]))
    rel = (bearing - me[4] + 180) % 360 - 180  # Source: positiv = links
    side = "links" if rel > 0 else "rechts"
    a = abs(rel)
    if a <= 22.5:
        where = "vorne"
    elif a <= 67.5:
        where = f"{side} vorne"
    elif a <= 112.5:
        where = side
    elif a <= 157.5:
        where = f"{side} hinten"
    else:
        where = "hinten"
    meters = math.dist(me[:2], foe[:2]) * 0.0254  # 1 Unit = 1 Zoll
    return f"{where}, {meters:.0f} m"


_STAT_KEYS = {"seen": "seen_ms", "err": "seen_err", "dpitch": "dpitch", "dyaw": "dyaw",
              "react": "react_ms", "hit": "hit_ms", "peek": "peek_ms"}
MEASURE_KEYS = ("seen_err", "dpitch", "dyaw", "react_ms", "hit_ms", "peek_ms", "kill_errs")


def parse_stats(reply: str) -> dict:
    """Antwort von coach_stats (Plugin 1.10.0) -> Messwerte; "-" = nicht gemessen.

    War der Gegner schon beim Freeze-Ende sichtbar (start=1), sagen Sicht-
    zeitpunkt, Fadenkreuz und Reaktion nichts: der Blick wurde beim Versetzen
    gesetzt, und in der Freeze-Time kann niemand schiessen. Dann zaehlen nur
    die Kills.
    """
    reply = (reply or "").strip()
    if not reply.startswith("coach_stats "):
        return {}
    out: dict = {}
    try:
        for part in reply.split()[2:]:
            key, _, val = part.partition("=")
            if not val or val == "-":
                continue
            if key == "kills":
                out["kill_errs"] = [float(x) for x in val.split(",") if x]
            elif key == "start":
                out["at_start"] = val == "1"
            elif key in _STAT_KEYS:
                out[_STAT_KEYS[key]] = float(val)
    except ValueError:
        return {}
    if out.pop("at_start", False):
        out = {k: v for k, v in out.items() if k == "kill_errs"}
    return out


def aim_side(dpitch: float, dyaw: float) -> str:
    """Groesster Anteil des Fadenkreuz-Fehlers. Source: Pitch positiv = nach unten,
    Yaw positiv = nach links - dyaw > 0 heisst also: links am Gegner vorbei."""
    if abs(dpitch) >= abs(dyaw):
        return "zu tief" if dpitch > 0 else "zu hoch"
    return "links daneben" if dyaw > 0 else "rechts daneben"


def measure_line(m: dict) -> str:
    """"Fadenkreuz 12° (zu tief) · Reaktion 280 ms · er sah dich 150 ms frueher" - nur Gemessenes."""
    parts = []
    if "seen_err" in m:
        parts.append(f"Fadenkreuz {m['seen_err']:.0f}° ({aim_side(m.get('dpitch', 0), m.get('dyaw', 0))})"
                     if m["seen_err"] >= 3 else f"Fadenkreuz {m['seen_err']:.0f}°")
    if "react_ms" in m:
        parts.append(f"Reaktion {m['react_ms']:.0f} ms" if m["react_ms"] >= 0 else "vorgeschossen")
    if m.get("peek_ms", 0) >= 100:
        parts.append(f"er sah dich {m['peek_ms']:.0f} ms frueher")
    elif m.get("peek_ms", 0) <= -100:
        parts.append(f"du sahst ihn {-m['peek_ms']:.0f} ms frueher")
    return " · ".join(parts)


def _median(values):
    from statistics import median
    values = [v for v in values if v is not None]
    return median(values) if values else None


def session_measures(duels: list[dict]) -> dict:
    """Mediane eines Trainings (Median wie im Match-Parser: die Verteilung ist schief)."""
    seen = [d["seen_err"] for d in duels if "seen_err" in d]
    react = [d["react_ms"] for d in duels if d.get("react_ms", -1) >= 0]
    kills = [k for d in duels for k in d.get("kill_errs") or []]
    return {"seen_err": _median(seen), "seen_n": len(seen), "react_ms": _median(react), "react_n": len(react),
            "kill_err": _median(kills), "kill_n": len(kills)}


def match_crosshair_median(cfg: dict, last: int = 10) -> float | None:
    """Fadenkreuz 0,5 s vor Kills in deinen letzten Matches (Median der Match-Mediane).

    Dieselbe Messung wie kill_errs im Training (Plugin rechnet wie der Parser),
    also direkt vergleichbar - anders als das Fadenkreuz beim Auftauchen.
    """
    from .obsidian import export_sort_key
    vault = (cfg.get("obsidian_vault_path") or "").strip()
    if not vault:
        return None
    files = sorted((Path(vault) / cfg.get("coach_subfolder", "CS2-Coach") / "exports").glob("*_coach.json"),
                   key=export_sort_key)
    values = []
    for f in reversed(files):
        try:
            cp = json.loads(f.read_text(encoding="utf-8")).get("player", {}).get("crosshair_placement") or {}
        except (OSError, ValueError):
            continue
        if cp.get("kills_analyzed") and cp.get("median_degrees") is not None:
            values.append(float(cp["median_degrees"]))
        if len(values) >= last:
            break
    return _median(values)


def duel_tip(rnd: dict, outcome: str) -> str | None:
    """Kurzer Hinweis nach einem verlorenen Duell - nur aus dem, was das Log misst.

    Aus dem Log: Treffer, Trefferzone, wer zuerst getroffen hat. Vom Plugin
    (1.10.0): wer wen zuerst sah und wo dein Fadenkreuz war, als er auftauchte.
    Die Reaktionszeit wird angezeigt, aber noch nicht bewertet - ab wann sie
    "langsam" ist, haengt davon ab, wie frueh die Spotted-Maske anschlaegt, und
    das ist noch nicht gegen echte Duelle kalibriert. Ein verlorenes Duell ohne
    Treffer zaehlt nicht (Leerlauf), kommt hier also nicht an.
    """
    if outcome != "loss":
        return None
    if rnd.get("peek_ms", 0) >= 250:
        return (f"Er sah dich {rnd['peek_ms']:.0f} ms bevor du ihn - du standest offen. "
                "Enger an der Deckung halten oder einen anderen Winkel nehmen.")
    if rnd.get("seen_err", 0) >= 10:
        return (f"Als er auftauchte, war dein Fadenkreuz {rnd['seen_err']:.0f}° daneben "
                f"({aim_side(rnd.get('dpitch', 0), rnd.get('dyaw', 0))}) - dorthin vorzielen, wo er rauskommt.")
    hits, head = rnd.get("you_hits", 0), rnd.get("you_head", 0)
    if hits and not head:
        return f"Keiner deiner {hits} Treffer am Kopf - Fadenkreuz auf Kopfhoehe halten."
    if rnd.get("first") == "bot":
        return "Er hat zuerst getroffen - den Winkel vorzielen, bevor er kommt."
    if hits >= 2:
        return f"Du hast zuerst getroffen ({hits} Treffer, {head} am Kopf), er hat trotzdem gewonnen - nachsetzen statt abzusetzen."
    return None


def _score(state: dict) -> str:
    res = state.get("results", [])
    return f"{res.count('win')}:{res.count('loss')}"


def _give_commands(human_uid, bot_uid, pre: dict | None) -> list[str]:
    """coach_give fuer beide aus dem Demo-Inventar.

    Du: alles wie damals, Messer und Granaten inklusive, die damalige Waffe in
    der Hand. Bot: nur die Schusswaffen deines Gegners - ohne Messer und
    Granaten, sonst hat er beim Loslaufen nichts Schussbereites in der Hand.
    Ohne Demo-Inventar kein Befehl (dann gilt die Standardausruestung).
    """
    out = []
    mine = (pre or {}).get("me_items") or []
    if len(mine) > 1:
        active = mine[0] or next((x for x in mine[1:] if x not in GRENADES and x != "weapon_knife"), "weapon_knife")
        out.append(f"coach_give {human_uid} {active} " + " ".join(mine[1:]))
    foe = (pre or {}).get("foe_items") or []
    guns = [x for x in foe[1:] if x and x not in GRENADES and x != "weapon_knife"]
    if guns:
        active = foe[0] if foe[0] in guns else guns[-1]
        out.append(f"coach_give {bot_uid} {active} " + " ".join(guns))
    return out


def _difficulty(state: dict) -> str:
    return f"bot_difficulty {min(int(state.get('level', START_LEVEL)), 3)}"


def _aim_commands(state: dict, uids) -> list[str]:
    """Zielhilfe je nach Stufe und - ab Plugin 1.9.0 - Jagd: nach der Uebergabe an die
    KI oder dem ersten Toten der Runde weiss der Bot, wo der naechste Mensch steht.
    Wunsch vom 30.10.: "die Bots duerfen gerne wissen wo ich bin, spaetestens nach
    dem ersten Kill"."""
    react, hold = AIM.get(int(state.get("level", START_LEVEL)), (0, 0))
    out = []
    for uid in uids:
        out += [f"coach_bot_skill {uid} {react} {hold}", f"coach_hunt {uid} 1"]
    return out


def _side(team: str) -> str:
    return "t" if team == "TERRORIST" else "ct"


def load_party(config_path: Path) -> dict:
    """{"invited": [Namen], "joined": {steam64: Name}} - bleibt ueber Trainings hinweg."""
    try:
        data = json.loads((Path(config_path).parent / PARTY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"invited": list(data.get("invited") or []), "joined": dict(data.get("joined") or {})}


def save_party(config_path: Path, party: dict) -> None:
    (Path(config_path).parent / PARTY_FILE).write_text(json.dumps(party, ensure_ascii=False), encoding="utf-8")


def _human_sids(state: dict) -> list[str]:
    """Du und alle Mitspieler, die gerade verbunden sind."""
    return [state["human"]] + [s for s, p in (state.get("party") or {}).items() if p.get("uid") is not None]


def _party_uids(state: dict) -> list:
    return [p["uid"] for p in (state.get("party") or {}).values() if p.get("uid") is not None]


def _party_hud(state: dict, seconds: float, body: str) -> list[str]:
    return [hud(uid, seconds, body) for uid in _party_uids(state)]


def _level_text(state: dict) -> str:
    return f"Bot-Stufe {state['level']}/{MAX_LEVEL}"


def _admins(cfg: dict) -> set[str]:
    ids = {str(cfg.get("steam_id") or "").strip()}
    ids |= {str(x).strip() for x in (cfg.get("training_admins") or [])}
    return {i for i in ids if i}


class Director:
    """Eine Instanz pro Aufruf; Zustand liegt in der Datei, nicht im Objekt."""

    def __init__(self, cfg: dict, config_path: Path, send, scenarios=None, timer=None):
        self.cfg = cfg
        self.path = Path(config_path)
        self.send = send  # send(*befehle) -> list[str]
        self._scenarios = scenarios
        self.timer = timer or (lambda sec, fn: threading.Timer(sec, fn).start())

    # -- Datenquellen -------------------------------------------------------

    def maps(self) -> list[str]:
        return list(self.cfg.get("training_maps") or DEFAULT_MAPS)

    def scenarios(self) -> dict[str, list[Scenario]]:
        if self._scenarios is None:
            vault = self.cfg.get("obsidian_vault_path") or ""
            folder = Path(vault) / self.cfg.get("coach_subfolder", "CS2-Coach") / "exports" if vault else None
            self._scenarios = build_scenarios(folder, self.maps()) if folder else {}
        return self._scenarios

    # -- Chat-Befehle -------------------------------------------------------

    def command(self, sid: str, name: str, cmd: str, arg: str, uid: int | None = None) -> list[str]:
        """Antwortzeilen fuer einen Trainingsbefehl."""
        if cmd in ("mitmachen", "aussteigen"):
            return self._join(sid, name, cmd, uid)
        if sid not in _admins(self.cfg):
            return ["Das Training kann nur der Server-Besitzer starten."]
        if cmd in ("einladen", "ausladen"):
            return self._invite(name, cmd, arg, uid)
        if cmd in MODE_COMMANDS:
            mode = MODE_COMMANDS[cmd]
            target = arg.lower().split()[0] if arg.strip() else self._current_map()
            target = target if target.startswith("de_") else f"de_{target}"
            if target in self.maps():
                return self._start(sid, name, f"{target} {mode}")
            if uid is not None:
                return self._open_mode_maps(uid, mode)
            return [f"Auf welcher Map? z. B. !{cmd} mirage"]
        if cmd in ("menu", "menue") or (cmd in ("training", "train") and not arg and uid is not None):
            return self._open_menu(uid)
        if cmd in ("training", "train") and arg.lower().split()[-1:] == ["menu"] and uid is not None:
            return self._open_menu(uid, arg.lower().split()[0])
        if cmd in ("training", "train"):
            # Ohne Sperre: das Auslesen der Demos dauert einige Sekunden, solange
            # duerfen Log-Stapel nicht an der Sperre haengen. _start sperrt erst
            # zum Speichern.
            return self._start(sid, name, arg)
        with _Lock(self.path):
            state = load_state(self.path)
            if not state:
                return ["Es läuft kein Training. Start: !training <map>"]
            if cmd == "stop":
                return self._stop(state, "Training beendet")
            if cmd == "skip":
                if state.get("phase") != "running":
                    return ["Das Training läuft noch nicht."]
                state["skip"] = True
                save_state(self.path, state)
                self.send("endround")
                return ["Übersprungen."]
            if cmd == "pause":
                if state.get("paused"):
                    return ["Schon pausiert. Weiter mit !weiter."]
                self._pause(state, "Pause")
                save_state(self.path, state)
                return []
            if cmd == "weiter":
                if not state.get("paused"):
                    return ["Das Training läuft schon."]
                # Dasselbe Duell noch einmal von vorn: Runde beenden, ohne zu zaehlen.
                state.update(paused=False, idle=0, redo=True)
                save_state(self.path, state)
                self.send("mp_ignore_round_win_conditions 0", "bot_freeze 0",
                          hud(state.get("human_uid"), 0, ""), "endround")
                return []
            if cmd in ("leichter", "schwerer"):
                self._shift(state, -1 if cmd == "leichter" else 1)
                save_state(self.path, state)
                self.send(_difficulty(state))
                return [_level_text(state)]
        return []

    def _start(self, sid: str, name: str, arg: str) -> list[str]:
        all_sc = self.scenarios()
        if not arg:
            avail = ", ".join(f"{m.removeprefix('de_')} ({len(v)})" for m, v in sorted(all_sc.items()))
            return [f"Maps mit Situationen: {avail or 'keine'}. Start: !training <map> [situation|retake|halten]"]
        parts = arg.lower().split()
        bsp = parts[0] if parts[0].startswith("de_") else f"de_{parts[0]}"
        mode = MODE_ALIASES.get(parts[1], "") if len(parts) > 1 else "duell"
        if not mode:
            return [f"Unbekannter Modus '{parts[1]}'. Moeglich: duell, situation, retake, halten."]
        if bsp not in self.maps():
            return [f"{bsp} ist nicht im Trainings-Pool ({', '.join(m.removeprefix('de_') for m in self.maps())})."]
        if mode != "duell":
            return self._start_multi(sid, name, bsp, mode)
        if not all_sc.get(bsp):
            return [f"Keine verlorenen Duelle auf {bsp} in deinen Matches."]
        if is_active(self.path):
            return ["Es läuft schon ein Training. Erst !stop."]
        version = self.send("coach_tp_version")[0]
        if not version.strip().startswith("coach_tp "):
            # Nach CS2-Updates ist Metamod oft inkompatibel; der cs2-waechter schaltet
            # es dann ab, damit der Server ueberhaupt laeuft - ohne Plugin kein Training.
            return ["Trainings-Plugin nicht geladen (nach einem CS2-Update?). Training gerade nicht möglich."]
        others = self._strangers(name)
        if others:
            return [f"Erst wenn der Server leer ist - gerade online: {', '.join(others)}. "
                    "Mittrainieren: !einladen <Name>"]
        n = min(MAX_SCENARIOS, len(all_sc[bsp]))
        chosen = all_sc[bsp][:n]
        demos = len({sc.demo for sc in chosen if sc.demo})
        self.send(_say(f"Training {bsp}: lese die Positionen {lead_seconds(self.cfg):g} s vor jedem Duell "
                       f"aus {demos} Demos..."))
        pre = prepare(self.cfg, self.path, chosen)
        with _Lock(self.path):
            if is_active(self.path):
                return ["Es läuft schon ein Training. Erst !stop."]
            state = {"phase": "loading", "map": bsp, "human": sid, "name": name, "queue": list(range(n)),
                     "pos": 0, "results": [], "level": START_LEVEL, "round": 0,
                     "pre": {str(i): v for i, v in pre.items()}, "party": self._party_state(),
                     "started": datetime.now().isoformat(timespec="seconds"), "t0": time.time()}
            save_state(self.path, state)
        self.timer(LOAD_TIMEOUT, self._check_loaded)
        # "map" statt "changelevel": am 30.10. hing der Server bei changelevel von
        # der Workshop-Map weg, waehrend ein Spieler verbunden war (Watchdog,
        # Neustart noetig). "map" trennt die Clients und laesst sie neu verbinden,
        # statt sie durch den Wechsel zu tragen. Ob das den Haenger vermeidet,
        # ist noch nicht belegt - darum zusaetzlich LOAD_TIMEOUT und cs2-waechter.
        late = f", {n - len(pre)} davon am Todesort (Demo fehlt)" if len(pre) < n else ""
        self.send(_say(f"{n} Duelle aus deinen Matches{late}. Map wird geladen..."),
                  "sv_hibernate_when_empty 0", f"map {bsp}")
        return []

    def _strangers(self, name: str) -> list[str]:
        """Online, aber weder du noch ein Mitspieler, der zugesagt hat."""
        joined = {n.lower() for n in load_party(self.path)["joined"].values()}
        return [h for h in _humans(self.send("status")[0]) if h != name and h.lower() not in joined]

    def _party_state(self) -> dict:
        return {sid: {"name": n, "uid": None} for sid, n in load_party(self.path)["joined"].items()}

    def _invite(self, name: str, cmd: str, arg: str, uid) -> list[str]:
        party = load_party(self.path)
        target = arg.strip().lower()
        if cmd == "ausladen":
            if not target:
                return ["Wen? !ausladen <Name>"]
            party["invited"] = [n for n in party["invited"] if target not in n.lower()]
            gone = [s for s, n in party["joined"].items() if target in n.lower()]
            for s_ in gone:
                party["joined"].pop(s_)
            save_party(self.path, party)
            with _Lock(self.path):
                state = load_state(self.path)
                if state and any(s_ in state.get("party", {}) for s_ in gone):
                    for s_ in gone:
                        state["party"].pop(s_, None)
                    save_state(self.path, state)
            return [f"Ausgeladen: {arg.strip()}."]
        online = [h for h in _humans(self.send("status")[0]) if h != name]
        if not target:
            cands = [h for h in online if h not in party["joined"].values()]
            if not cands:
                return ["Gerade ist niemand sonst online, den du einladen kannst."]
            if uid is not None:
                from .server_live import _safe
                items = [[h, f"!einladen {_safe(h)}"] for h in cands[:8]] + [["Schliessen", ""]]
                if "coach_menu ok" in self.send(menu(uid, "Mitspieler einladen", items))[0]:
                    return []
            return [f"Wen? !einladen <Name> - online: {', '.join(cands[:6])}"]
        hits = [h for h in online if target in h.lower()]
        exact = [h for h in hits if h.lower() == target]
        hits = exact or hits
        if len(hits) != 1:
            return [f"Kein eindeutiger Spieler '{arg.strip()}' online"
                    + (f" (online: {', '.join(online[:6])})." if online else ".")]
        who = hits[0]
        if who not in party["invited"]:
            party["invited"].append(who)
        save_party(self.path, party)
        return [f"{who}: {name} laedt dich zum Training ein - !mitmachen zum Mitmachen, !aussteigen zum Aufhoeren."]

    def _join(self, sid: str, name: str, cmd: str, uid) -> list[str]:
        party = load_party(self.path)
        if cmd == "aussteigen":
            was = party["joined"].pop(sid, None)
            save_party(self.path, party)
            with _Lock(self.path):
                state = load_state(self.path)
                if state and state.get("party", {}).pop(sid, None):
                    save_state(self.path, state)
            return [f"{name} trainiert nicht mehr mit."] if was else []
        invited = [n for n in party["invited"] if n.lower() == name.lower()]
        if not invited and sid not in party["joined"]:
            return [f"{name}: erst muss dich der Server-Besitzer einladen (!einladen {name})."]
        party["invited"] = [n for n in party["invited"] if n not in invited]
        party["joined"][sid] = name
        save_party(self.path, party)
        with _Lock(self.path):
            state = load_state(self.path)
            if state:
                state.setdefault("party", {})[sid] = {"name": name, "uid": uid}
                if uid is not None and state.get("team"):
                    self.send(f"coach_team {uid} {_side(state['team'])}")
                save_state(self.path, state)
        return [f"{name} trainiert mit - du startest, wo damals ein Mitspieler stand."]

    def _preflight(self, name: str) -> list[str]:
        """Plugin geladen, kein anderer Mensch, kein Training aktiv? Leere Liste = los."""
        if is_active(self.path):
            return ["Es läuft schon ein Training. Erst !stop."]
        version = self.send("coach_tp_version")[0]
        if not version.strip().startswith("coach_tp "):
            return ["Trainings-Plugin nicht geladen (nach einem CS2-Update?). Training gerade nicht möglich."]
        others = self._strangers(name)
        if others:
            return [f"Erst wenn der Server leer ist - gerade online: {', '.join(others)}. "
                    "Mittrainieren: !einladen <Name>"]
        return []

    def _start_multi(self, sid: str, name: str, bsp: str, mode: str) -> list[str]:
        """Situation, Retake oder Site halten: mehrere Gegner, alle mit ihrem echten Weg."""
        from .training_scenarios import MODES, collect
        problem = self._preflight(name)
        if problem:
            return problem
        self.send(_say(f"{MODES[mode]} auf {bsp}: suche Situationen in deinen Demos "
                       "(beim ersten Mal bis zu 1-2 Minuten)..."))
        items = collect(self.cfg, self.path, bsp, mode, limit=MAX_SCENARIOS)
        if not items:
            return [f"Keine {MODES[mode]}-Situationen auf {bsp} in deinen Demos gefunden."]
        (self.path.parent / ITEMS_FILE).write_text(json.dumps(items), encoding="utf-8")
        need = max(len(it["enemies"]) for it in items)
        with _Lock(self.path):
            if is_active(self.path):
                return ["Es läuft schon ein Training. Erst !stop."]
            save_state(self.path, {
                "phase": "loading", "map": bsp, "mode": mode, "human": sid, "name": name,
                "queue": list(range(len(items))), "bot_need": need, "pos": 0, "results": [],
                "level": START_LEVEL, "round": 0, "started": datetime.now().isoformat(timespec="seconds"),
                "t0": time.time(), "party": self._party_state()})
        self.timer(LOAD_TIMEOUT, self._check_loaded)
        self.send(_say(f"{len(items)} {MODES[mode]}-Situationen, bis zu {need} Gegner. Map wird geladen..."),
                  "sv_hibernate_when_empty 0", f"map {bsp}")
        return []

    def _items(self) -> list[dict]:
        try:
            return json.loads((self.path.parent / ITEMS_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _check_loaded(self) -> None:
        """Map nach LOAD_TIMEOUT nicht geladen: Training verwerfen.

        Sonst wuerde ein spaeteres "Loading map" - etwa beim Neustart des
        haengenden Servers, der mit +map de_mirage startet - das alte
        Training mit sv_cheats 1 aufsetzen.
        """
        with _Lock(self.path):
            state = load_state(self.path)
            if state.get("phase") != "loading":
                return
            save_state(self.path, {})
        try:
            self.send(_say("Map nicht geladen - Training abgebrochen."), *RESTORE)
        except Exception as e:  # noqa: BLE001 - haengender Server: der cs2-waechter startet ihn neu
            print(f"[training] Abbruch ohne RCON: {e}", flush=True)

    def _shift(self, state: dict, step: int) -> None:
        state["level"] = max(0, min(MAX_LEVEL, state["level"] + step))

    def _stop(self, state: dict, why: str, immediate: bool = False) -> list[str]:
        """Training beenden.

        Regulaer (fertig oder !stop) bleibt die Map noch FAREWELL_SECONDS stehen:
        Ergebnis gross, Fazit der KI im Chat, Zeit fuer ein neues !training.
        Vorher wechselte sie sofort ("abrupt"). Bei Verbindungsabbruch,
        haengendem Laden oder langer Pause sofort zurueck.
        """
        res = state.get("results", [])
        won = sum(1 for r in res if r == "win")
        summary = f"{why}: {won}/{len(res)} Duelle gewonnen" if res else why
        save_state(self.path, {})
        session = save_session(self.path, state, why) if state.get("duels") else None
        uid = state.get("human_uid")
        if immediate or uid is None:
            self._restore()
            self.send(_say(summary))
            return []
        base = (self.cfg.get("public_url") or "").strip().rstrip("/")
        link = [_say(f"Auswertung: {base.split('://')[-1]}/training")] if base and session else []
        lines = [_font("TRAINING FERTIG", PURPLE, "xl")]
        if res:
            lines.append(_font(f"{won} von {len(res)} gewonnen ({won * 100 // len(res)} %)", "#ffffff", "l"))
        lines.append(_font(f"Fazit im Chat &#183; Map wechselt in {FAREWELL_SECONDS} s &#183; !training fuer ein neues",
                           GREY, "s"))
        self.send("bot_kick", "bot_quota 0", "mp_ignore_round_win_conditions 0",
                  hud(uid, FAREWELL_SECONDS, "<br>".join(lines)), _say(summary), *link)
        if session:
            self.timer(0.1, lambda: self._fazit(uid, session))
        self.timer(FAREWELL_SECONDS, self._restore_if_idle)
        return []

    def _restore(self) -> None:
        restore = (self.cfg.get("server_restore_command") or "").strip() or "map de_mirage"
        self.send(*RESTORE, restore)

    def _restore_if_idle(self) -> None:
        if not is_active(self.path):  # hat er inzwischen ein neues Training gestartet?
            self._restore()

    def _fazit(self, uid, session: Path) -> None:
        from .ingame_coach import session_fazit, tell
        try:
            text = session_fazit(self.cfg, self.path, session)
        except Exception as e:  # noqa: BLE001 - ohne Fazit endet das Training trotzdem sauber
            print(f"[training] Fazit fehlgeschlagen: {e}", flush=True)
            text = None
        if text:
            tell(self.send, uid, f"Fazit: {text}", max_lines=6)

    def _record(self, state: dict, outcome: str, tip: str | None = None) -> None:
        """Gezaehltes Duell fuer die Auswertung und fuer !tipp merken."""
        sc = self._scenario(state)
        rnd = state.get("rnd") or {}
        duel = {"n": state["pos"] + 1, "source": sc.source, "opponent": sc.opponent, "weapon": sc.weapon,
                "outcome": outcome, "tries": state.get("tries", 0) + 1, "level": state.get("level"),
                "export": sc.export, "round": sc.round, "you_hits": rnd.get("you_hits", 0),
                "you_head": rnd.get("you_head", 0), "bot_hits": rnd.get("bot_hits", 0),
                "first": rnd.get("first"), "tip": tip, "kit": self._kit(state),
                **{k: rnd[k] for k in MEASURE_KEYS if k in rnd}}
        state.setdefault("duels", []).append(duel)
        from .ingame_coach import save_last_duel
        save_last_duel(self.path, {**duel, "map": state.get("map"), "weapon": weapon_label(sc.weapon),
                                   "direction": state.get("direction", "?")})

    def _measure(self, state: dict) -> dict:
        """Messwerte der Runde vom Plugin; altes Plugin oder keine Antwort = leer."""
        try:
            return parse_stats(self.send(f"coach_stats {state['human_uid']}")[0])
        except Exception as e:  # noqa: BLE001 - ohne Messung zaehlt das Duell trotzdem
            print(f"[training] coach_stats: {e}", flush=True)
            return {}

    def _kit(self, state: dict) -> list[str]:
        """Deine Ausruestung in dieser Situation (aus der Demo), lesbar - fuer das Fazit."""
        if state.get("mode", "duell") == "duell":
            items = (state.get("pre", {}).get(str(state["queue"][state["pos"]])) or {}).get("me_items") or []
        else:
            items = self._items()[state["queue"][state["pos"]]].get("me_items") or []
        return [weapon_label(x) for x in items[1:] if x and "knife" not in x]

    def _current_map(self) -> str:
        """Aktuelle Server-Map aus 'status' ("loaded spawngroup(  1)  : SV:  [1: de_mirage | ...")."""
        import re as _re
        m = _re.search(r"spawngroup\(\s*1\)\s*:\s*SV:\s*\[1:\s*([\w-]+)", self.send("status")[0])
        return m.group(1) if m else ""

    def _open_mode_maps(self, uid, mode: str) -> list[str]:
        from .training_scenarios import MODES
        items = [[m.removeprefix("de_").capitalize(), f"!training {m.removeprefix('de_')} {mode}"]
                 for m in self.maps()] + [["Schliessen", ""]]
        reply = self.send(menu(uid, f"{MODES[mode]}: Map waehlen", items))[0]
        if "coach_menu ok" not in reply:
            return [f"Auf welcher Map? z. B. !{mode} mirage"]
        return []

    def _open_menu(self, uid, bsp: str | None = None) -> list[str]:
        """W/S + E im Spiel statt Befehle tippen (Plugin 1.3.0). Erst Map, dann Modus."""
        from .training_scenarios import MODE_HELP, MODES
        state = load_state(self.path)
        if not state and bsp:
            short = bsp.removeprefix("de_")
            items = [[f"{MODES[m]} ({MODE_HELP[m]})", f"!training {short} {m}"] for m in MODES]
            items.append(["Zurueck", "!training"])
            title = f"{short.capitalize()}: was trainieren?"
        elif not state:
            items = [[f"{m.removeprefix('de_').capitalize()} ({len(v)} Duelle)", f"!training {m.removeprefix('de_')} menu"]
                     for m, v in sorted(self.scenarios().items())]
            items += [["Mitspieler einladen", "!einladen"], ["Schliessen", ""]]
            title = "Training: Map waehlen"
        else:
            items = [["Weiter", "!weiter"] if state.get("paused") else ["Pause", "!pause"],
                     ["Duell ueberspringen", "!skip"], ["Leichter", "!leichter"], ["Schwerer", "!schwerer"],
                     ["Mitspieler einladen", "!einladen"], ["Training beenden", "!stop"], ["Schliessen", ""]]
            title = f"Training {state.get('map', '')} &#183; Stand {_score(state)}"
        reply = self.send(menu(uid, title, items))[0]
        if "coach_menu ok" not in reply:
            return ["Menue braucht das aktuelle Server-Plugin. Befehle: !training <map>, !pause, !weiter, !skip, !stop"]
        return []

    # -- Log-Ereignisse -------------------------------------------------------

    def on_lines(self, lines: list[str]) -> None:
        with _Lock(self.path):
            state = load_state(self.path)
            if not state:
                return
            for raw in lines:
                m = ss._LINE.match(raw)
                if m:
                    state = self._event(state, m.group(2))
                    if not state:
                        save_state(self.path, {})  # beendet oder verworfen
                        return
            save_state(self.path, state)

    def _event(self, state: dict, msg: str) -> dict:
        phase = state["phase"]
        if phase == "loading" and time.time() - state.get("t0", 0) > LOAD_TIMEOUT:
            return {}  # verwaist (z. B. Server neu gestartet), nicht mehr aufsetzen
        if phase == "loading" and (m := _LOADING.match(msg)) and m.group("map") == state["map"]:
            state["phase"] = "setup"
            self.timer(SETUP_WAIT, self._setup)
            return state
        if phase in ("setup", "team", "bot", "running") and (m := _TEAM.match(msg)):
            to = m.group("to")
            uid = int(m.group("auid"))
            if to not in ("TERRORIST", "CT"):
                return state
            mate = (state.get("party") or {}).get(ss.steam64(m.group("asid")))
            if mate is not None:
                # Mitspieler gehoeren in dein Team; der Wechsel per coach_team erzeugt
                # eine zweite Zeile mit dem richtigen Team, also keine Schleife.
                mate["uid"] = uid
                if state.get("team") and to != state["team"]:
                    self.send(f"coach_team {uid} {_side(state['team'])}")
                return state
            if ss.steam64(m.group("asid")) == state["human"] and phase in ("setup", "team"):
                # Gemessen 30.10.: Team gewaehlt 2 s bevor das Setup lief - in "setup"
                # nur merken, _setup holt dann den Bot, statt auf einen zweiten Wechsel zu warten.
                state.update(team=to, human_uid=uid)
                if phase == "team":
                    self._add_bot(state)
            elif m.group("asid") == "BOT" and phase in ("bot", "running"):
                bots = state.setdefault("bot_uids", [])
                need = state.get("bot_need", 1)
                if uid in bots:
                    pass
                elif len(bots) < need:
                    bots.append(uid)
                    if len(bots) == need and phase == "bot":
                        state.update(bot_uid=bots[0], phase="running", round=0, pos=0)
                        self.send(_say("Los geht's. Alles Weitere im Menue: !menu (W/S waehlen, E bestaetigen)."),
                                  "mp_warmup_end")
                else:
                    # Am 30.10. brachte ein bot_add zwei Bots - jeder ueberzaehlige fliegt.
                    self.send(f"kickid {uid}")
            return state
        if (m := ss._LEAVE.match(msg)) and ss.steam64(m.group("asid")) in (state.get("party") or {}):
            state["party"][ss.steam64(m.group("asid"))]["uid"] = None  # Training laeuft fuer dich weiter
            return state
        if phase in ("team", "bot", "running") and (m := ss._LEAVE.match(msg)) \
                and ss.steam64(m.group("asid")) == state["human"]:
            # Sonst bliebe der Server mit sv_cheats 1 und Bot im Trainingsmodus.
            # Nicht in "loading"/"setup": beim Mapwechsel verbinden sich alle neu.
            self._stop(state, "Training abgebrochen (Verbindung getrennt)", immediate=True)
            return {}
        if phase != "running":
            return state
        if msg.startswith('World triggered "Round_Start"'):
            return self._round_start(state)
        if m := ss._ATTACK.match(msg):
            rnd = state.setdefault("rnd", {})
            if ss.steam64(m.group("asid")) in _human_sids(state):
                state["active"] = True  # jemand von euch hat in dieser Runde getroffen
            if ss.steam64(m.group("asid")) == state["human"]:
                rnd["you_hits"] = rnd.get("you_hits", 0) + 1
                rnd["you_head"] = rnd.get("you_head", 0) + (m.group("hitgroup") == "head")
                rnd.setdefault("first", "you")
            elif m.group("asid") == "BOT" and ss.steam64(m.group("vsid")) == state["human"]:
                rnd["bot_hits"] = rnd.get("bot_hits", 0) + 1
                rnd.setdefault("first", "bot")
            return state
        if (m := ss._KILL.match(msg)) and not state.get("outcome"):
            killer = ss.steam64(m.group("asid"))
            victim = ss.steam64(m.group("vsid"))
            humans = _human_sids(state)
            if killer in humans:
                state["active"] = True
            if victim in humans:
                # Zu mehreren ist die Runde erst verloren, wenn alle Menschen tot sind.
                dead = state.setdefault("rnd", {}).setdefault("dead", [])
                dead.append(victim)
                if all(h in dead for h in humans):
                    state["outcome"] = "loss"
            elif m.group("vsid") == "BOT":
                rnd = state.setdefault("rnd", {})
                active = rnd.get("active_bots")
                if active is None:  # Duell: ein Bot
                    if killer in humans:
                        state["outcome"] = "win"
                elif int(m.group("vuid")) in active:
                    rnd["bots_dead"] = rnd.get("bots_dead", 0) + 1
                    if rnd["bots_dead"] >= len(active):
                        state["outcome"] = "win"
            return state
        if msg.startswith('World triggered "Round_End"'):
            if not state.get("round"):
                return state  # Ende der Aufwaermphase
            if state.pop("redo", False):
                return state  # nach !weiter: dasselbe Duell noch einmal, nichts zaehlen
            if state.get("paused"):
                return state  # pausierte Runde (endet nur durch !weiter)
            skipped = state.pop("skip", False)
            if not skipped and not state.get("active"):
                return self._idle_round(state)
            state["idle"] = 0
            if not skipped:
                outcome = state.get("outcome") or "draw"
                state.setdefault("rnd", {}).update(self._measure(state))
                self._result(state, outcome)
                tip = duel_tip(state.get("rnd") or {}, outcome)
                self._record(state, outcome, tip)
                text, color = {"win": ("GEWONNEN", GREEN), "loss": ("VERLOREN", RED)}.get(
                    outcome, ("UNENTSCHIEDEN", YELLOW))
                seen = measure_line(state.get("rnd") or {})
                self.send(hud(state["human_uid"], 4, _font(text, color, "xl") + "<br>"
                              + _font(f"Stand {_score(state)}", GREY)
                              + (f"<br>{_font(html.escape(seen), '#ffffff', 's')}" if seen else "")
                              + (f"<br>{_font(html.escape(tip), '#ffffff', 's')}" if tip else "")),
                          *_party_hud(state, 4, _font(text, color, "xl") + "<br>" + _font(f"Stand {_score(state)}", GREY)))
                if tip:
                    from .ingame_coach import tell
                    tell(self.send, state["human_uid"], f"Tipp: {tip} Mehr dazu: !tipp")
            state["tries"] = 0
            state["pos"] = state.get("pos", 0) + 1
            if state["pos"] >= len(state["queue"]):
                self._stop(state, "Training fertig")
                return {}
            self.send(f'bot_loadout "{self._scenario(state).weapon}"')
        return state

    def _idle_round(self, state: dict) -> dict:
        """Runde ohne einen Treffer von dir: zaehlt nicht, dasselbe Duell kommt noch einmal.

        Am 30.10. lief das Training weiter, waehrend der Spieler nicht spielte:
        Bot-Kills und abgelaufene Runden zaehlten als Duelle, nach 20 Runden
        war es "fertig" und die Aim-Map kam zurueck. Nach IDLE_ROUNDS solcher
        Runden in Folge pausiert das Training; nach PAUSE_TIMEOUT endet es.
        """
        state["idle"] = state.get("idle", 0) + 1
        state["tries"] = state.get("tries", 0) + 1
        uid = state.get("human_uid")
        if state["idle"] >= IDLE_ROUNDS and not state.get("paused"):
            self._pause(state, f"{IDLE_ROUNDS} Runden ohne Treffer")
        else:
            self.send(hud(uid, 3, _font("KEIN TREFFER", YELLOW, "xl") + "<br>"
                          + _font("Das Duell kommt noch einmal", "#ffffff")))
        return state

    def _pause(self, state: dict, why: str) -> None:
        """Eigene Pause: Bot eingefroren, Runde endet nicht.

        Nicht mp_pause_match: die Match-Pause von CS2 bringt eine eigene Anzeige
        in der Bildschirmmitte mit, und das Menue "verschwindet sofort, wenn man
        in der !pause ist" (30.10.2026).
        """
        stamp = time.time()
        state.update(paused=True, paused_at=stamp)
        self.send("bot_freeze 1", "mp_ignore_round_win_conditions 1",
                  hud(state.get("human_uid"), PAUSE_TIMEOUT, _font("PAUSE", YELLOW, "xl") + "<br>"
                      + _font(why, "#ffffff") + "<br>"
                      + _font("!weiter &#183; !menu &#183; !stop", GREY, "s")),
                  _say("Pausiert. Weiter mit !weiter, Menue mit !menu."))
        self.timer(PAUSE_TIMEOUT, lambda: self._check_paused(stamp))

    def _check_paused(self, stamp: float) -> None:
        with _Lock(self.path):
            state = load_state(self.path)
            if state.get("paused") and state.get("paused_at") == stamp:
                self._stop(state, f"Training beendet ({PAUSE_TIMEOUT // 60} Minuten pausiert)", immediate=True)

    def _result(self, state: dict, result: str) -> None:
        """Ergebnis merken und nach je 5 Duellen die Stufe anpassen (Ziel: etwa die Haelfte gewinnen).

        Nach einer Anpassung beginnt das Fenster neu - sonst wuerde jedes
        weitere Duell mit 4 von 5 Siegen erneut verschaerfen.
        """
        state["results"].append(result)
        window = state.setdefault("window", [])
        window.append(result)
        if len(window) < 5:
            return
        wins = window.count("win")
        if wins >= 4:
            self._shift(state, 1)
            state["window"] = []
            self.send(_difficulty(state), _say(f"Stark! Schwerer: {_level_text(state)}"))
        elif wins <= 1:
            self._shift(state, -1)
            state["window"] = []
            self.send(_difficulty(state), _say(f"Etwas leichter: {_level_text(state)}"))
        else:
            state["window"] = window[-4:]

    def _scenario(self, state: dict) -> Scenario:
        if state.get("mode", "duell") == "duell":
            return self.scenarios()[state["map"]][state["queue"][state["pos"]]]
        item = self._items()[state["queue"][state["pos"]]]
        me, foe = item["me"], item["enemies"][0]
        weapon = next((x for x in foe["items"] if x and x not in GRENADES and x != "weapon_knife"), "")
        return Scenario(state["map"], me[0], me[1], me[2], foe["start"][0], foe["start"][1], foe["start"][2],
                        weapon.removeprefix("weapon_"), f"{len(item['enemies'])} Gegner", item.get("source", ""),
                        int(item.get("tick", 0)), "", item.get("export", ""), int(item.get("round", 0)))

    def _round_start(self, state: dict) -> dict:
        """Beide an ihre Positionen LEAD_SECONDS vor deinem Tod (aus der Demo), mit Blickrichtung.

        Kein Einfrieren mehr nach der Freeze-Time: eingefrorene Bots schiessen
        nicht, im ersten Training fiel jeder Bot ohne einen Schuss.
        """
        state["round"] = state.get("round", 0) + 1
        state.update(outcome=None, active=False, rnd={})
        if state.get("mode", "duell") != "duell":
            return self._round_start_multi(state)
        sc = self._scenario(state)
        pre = state.get("pre", {}).get(str(state["queue"][state["pos"]]))
        if pre:
            me, foe = list(pre["me"]), pre["foe"]
            # Blick dorthin, wo dein Gegner dich erschossen hat (Ende seines Wegs),
            # nicht deine Blickrichtung von damals: damit stand man "meist mit dem
            # Ruecken zum Gegner".
            target = (pre.get("path") or [foe])[-1]
            me[3], me[4] = _calc_view_angle(me[0], me[1], me[2] + 64, target[0], target[1], target[2] + 64)
            when = f"{pre['lead']:g} s vor dem Duell"
        else:
            pitch, yaw = _calc_view_angle(sc.x, sc.y, sc.z + 64, sc.ex, sc.ey, sc.ez + 64)
            bpitch, byaw = _calc_view_angle(sc.ex, sc.ey, sc.ez + 64, sc.x, sc.y, sc.z + 64)
            me, foe = [sc.x, sc.y, sc.z, pitch, yaw], [sc.ex, sc.ey, sc.ez, bpitch, byaw]
            when = "am Todesort (Demo fehlt)"
        n, total = state["pos"] + 1, len(state["queue"])
        path = (pre or {}).get("path") or []
        bot = state["bot_uid"]
        walk = (([f"coach_path_clear {bot}"]
                 + [f"coach_path_add {bot} " + " ".join(_fmt(pt) for pt in path[i:i + PATH_CHUNK])
                    for i in range(0, len(path), PATH_CHUNK)]
                 + [f"coach_path_start {bot} {PATH_HZ}"]) if len(path) >= 2 else [])
        how = "läuft den echten Weg, bis er dich sieht" if walk else "Bot-KI"
        give = _give_commands(state["human_uid"], bot, pre)
        state["direction"] = direction(me, foe)
        info = hud(state["human_uid"], FREEZE_TIME + 3, "<br>".join([
            _font(f"Duell {n}/{total} &#183; {html.escape(sc.source)}", PURPLE),
            _font(f"Gegner mit {html.escape(weapon_label(sc.weapon))}", RED, "l"),
            _font(f"kommt von {state['direction']}", "#ffffff"),
            _font(f"Stand {_score(state)} &#183; {_level_text(state)}", GREY, "s")]))
        # Mitspieler im Duell: an deiner Stelle (mp_solid_teammates 0), mit deiner Ausruestung.
        mates = []
        for uid in _party_uids(state):
            mates.append(f"coach_tp {uid} {_fmt(me)}")
            mates += [g.replace(f"coach_give {state['human_uid']} ", f"coach_give {uid} ", 1)
                      for g in give if g.startswith(f"coach_give {state['human_uid']} ")]
            mates.append(info.replace(f"coach_hud {state['human_uid']} ", f"coach_hud {uid} ", 1))
        replies = self.send(f"coach_tp {state['human_uid']} {_fmt(me)}",
                            f"coach_tp {bot} {_fmt(foe)}", *walk, *give, info, *mates, *_aim_commands(state, [bot]),
                            _say(f"Duell {n}/{total} ({sc.source}): {sc.opponent}, {when}, {how}"))
        if walk and "coach_path ok start" not in replies[1 + len(walk)]:
            failed_walk = replies[1 + len(walk)].strip() or "keine Antwort"
            if not state.get("walk_warned"):
                state["walk_warned"] = True
                self.send(_say(f"Weg ablaufen nicht moeglich ({failed_walk[:50]}) - Plugin aktuell?"))
        failed = [r.strip() for r in replies[:2] if "coach_tp ok" not in r]
        if failed and not state.get("tp_warned"):
            state["tp_warned"] = True
            self.send(_say(f"Versetzen fehlgeschlagen: {failed[0][:60] or 'keine Antwort'}"))
        return state

    def _round_start_multi(self, state: dict) -> dict:
        """Alle Gegner der Situation an ihren Startpunkt, jeder mit seinem echten Weg."""
        from .training_scenarios import MODES
        items = self._items()
        item = items[state["queue"][state["pos"]]]
        enemies = item["enemies"]
        human = state["human_uid"]
        me = list(item["me"])
        focus = enemies[0]
        target = (focus["path"] or [focus["start"]])[-1]
        me[3], me[4] = _calc_view_angle(me[0], me[1], me[2] + 64, target[0], target[1], target[2] + 64)
        cmds = [f"coach_tp {human} {_fmt(me)}"]
        mine = item.get("me_items") or []
        if len(mine) > 1:
            active_weapon = mine[0] or next((x for x in mine[1:] if x not in GRENADES and x != "weapon_knife"), "")
            cmds.append(f"coach_give {human} {active_weapon or 'weapon_knife'} " + " ".join(mine[1:]))
        active = []
        for i, uid in enumerate(state.get("bot_uids", [])):
            if i >= len(enemies):
                cmds.append(f"coach_slay {uid}")  # dieses Szenario hat weniger Gegner
                continue
            e = enemies[i]
            active.append(uid)
            cmds.append(f"coach_tp {uid} {_fmt(e['start'])}")
            if len(e["path"]) >= 2:
                cmds += ([f"coach_path_clear {uid}"]
                         + [f"coach_path_add {uid} " + " ".join(_fmt(pt) for pt in e["path"][j:j + PATH_CHUNK])
                            for j in range(0, len(e["path"]), PATH_CHUNK)]
                         + [f"coach_path_start {uid} {PATH_HZ}"])
            guns = [x for x in e["items"][1:] if x and x not in GRENADES and x != "weapon_knife"]
            if guns:
                cmds.append(f"coach_give {uid} {e['items'][0] if e['items'][0] in guns else guns[-1]} "
                            + " ".join(guns))
        state["rnd"]["active_bots"] = active
        # Mitspieler: dort, wo deine damaligen Teamkollegen standen, mit ihrer Ausruestung.
        # Mehr Freunde als Kollegen in der Demo: an deine Stelle, mit deiner Ausruestung.
        demo_mates = item.get("mates") or []
        for k, uid in enumerate(_party_uids(state)):
            spot = demo_mates[k] if k < len(demo_mates) else {"start": item["me"], "items": mine}
            pos = list(spot["start"])
            pos[3], pos[4] = _calc_view_angle(pos[0], pos[1], pos[2] + 64, target[0], target[1], target[2] + 64)
            cmds.append(f"coach_tp {uid} {_fmt(pos)}")
            kit = spot.get("items") or []
            if len(kit) > 1:
                hand = kit[0] or next((x for x in kit[1:] if x not in GRENADES and x != "weapon_knife"), "")
                cmds.append(f"coach_give {uid} {hand or 'weapon_knife'} " + " ".join(kit[1:]))
        nearest = min(enemies[:len(active)] or enemies, key=lambda e: math.dist(me[:2], e["start"][:2]))
        state["direction"] = direction(me, nearest["start"])
        n, total = state["pos"] + 1, len(state["queue"])
        weapons = ", ".join(weapon_label(next((x for x in e["items"] if x and x not in GRENADES
                                               and x != "weapon_knife"), "?")) for e in enemies[:len(active)])
        body = "<br>".join([
            _font(f"{MODES[state['mode']]} {n}/{total} &#183; {html.escape(item.get('source', ''))}", PURPLE),
            _font(f"{len(active)} Gegner: {html.escape(weapons)}", RED, "l"),
            _font(f"naechster kommt von {state['direction']}", "#ffffff"),
            _font(f"Stand {_score(state)} &#183; {_level_text(state)}", GREY, "s")])
        cmds.append(hud(human, FREEZE_TIME + 4, body))
        cmds += _party_hud(state, FREEZE_TIME + 4, body)
        cmds.append(_say(f"{MODES[state['mode']]} {n}/{total} ({item.get('source', '')}): {len(active)} Gegner, "
                         "jeder laeuft seinen echten Weg, bis er dich sieht"))
        cmds += _aim_commands(state, active)
        replies = self.send(*cmds)
        failed = [r.strip() for c, r in zip(cmds, replies) if c.startswith("coach_tp ") and "coach_tp ok" not in r]
        if failed and not state.get("tp_warned"):
            state["tp_warned"] = True
            self.send(_say(f"Versetzen fehlgeschlagen: {failed[0][:60] or 'keine Antwort'}"))
        return state

    def _setup(self) -> None:
        with _Lock(self.path):
            state = load_state(self.path)
            if state.get("phase") != "setup":
                return
            team = state.get("team")
            hint = (f"Team {'T' if team == 'TERRORIST' else 'CT'} erkannt, Bot kommt gleich." if team
                    else "Wähle ein Team (T oder CT) - der Bot kommt auf die andere Seite.")
            self.send(*SETUP, _difficulty(state), _say(hint))
            if team:
                self._add_bot(state)
            else:
                state["phase"] = "team"
            save_state(self.path, state)

    def _add_bot(self, state: dict) -> None:
        """Genau einen Bot ins Gegnerteam holen; sein Teambeitritt startet das Training."""
        state["phase"] = "bot"
        bot_team = "ct" if state["team"] == "TERRORIST" else "t"
        need = state.get("bot_need", 1)
        loadout = []
        if state.get("mode", "duell") == "duell":
            loadout = [f'bot_loadout "{self.scenarios()[state["map"]][state["queue"][0]].weapon}"']
        # bot_quota statt bot_add: am 30.10. brachte ein "bot_add_ct" zwei Bots
        # (die Map-Konfiguration setzt bot_quota_mode competitive).
        mates = [f"coach_team {uid} {_side(state['team'])}" for uid in _party_uids(state)]
        self.send(*loadout, *mates, "bot_quota_mode normal", f"bot_join_team {bot_team}", f"bot_quota {need}")


def sessions_dir(config_path: Path) -> Path:
    return Path(config_path).parent / SESSIONS_DIR


def save_session(config_path: Path, state: dict, why: str) -> Path:
    """Ein Training als JSON neben der Config ablegen (Grundlage fuer /training)."""
    folder = sessions_dir(config_path)
    folder.mkdir(exist_ok=True)
    started = state.get("started") or datetime.now().isoformat(timespec="seconds")
    path = folder / f"{started.replace(':', '-')}_{state.get('map', 'map')}.json"
    path.write_text(json.dumps({
        "map": state.get("map"), "started": started, "ended": datetime.now().isoformat(timespec="seconds"),
        "why": why, "level_end": state.get("level"), "duels": state.get("duels", []),
        "mode": state.get("mode", "duell"),
        "party": sorted(p.get("name", "") for p in (state.get("party") or {}).values())},
        ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load_sessions(config_path: Path) -> list[dict]:
    """Alle gespeicherten Trainings, neueste zuerst."""
    out = []
    for f in sorted(sessions_dir(config_path).glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        duels = data.get("duels") or []
        data["won"] = sum(1 for d in duels if d.get("outcome") == "win")
        data["played"] = len(duels)
        data["measures"] = session_measures(duels)
        out.append(data)
    return out


def hardest(sessions: list[dict], limit: int = 8) -> list[dict]:
    """Situationen, die du am oeftesten verloren hast (ueber alle Trainings)."""
    agg: dict[tuple, dict] = {}
    for s in sessions:
        for d in s.get("duels", []):
            key = (d.get("export"), d.get("round"), d.get("source"))
            a = agg.setdefault(key, {**d, "map": s.get("map"), "lost": 0, "won": 0})
            if d.get("outcome") == "loss":
                a["lost"] += 1
            elif d.get("outcome") == "win":
                a["won"] += 1
    rows = [a for a in agg.values() if a["lost"]]
    rows.sort(key=lambda a: (-a["lost"], a["won"]))
    return rows[:limit]


def state_summary(config_path: Path) -> dict | None:
    """Fuer die Webseite: laeuft ein Training, und wie steht es?"""
    state = load_state(config_path)
    if not state:
        return None
    res = state.get("results", [])
    return {"map": state.get("map"), "phase": state.get("phase"), "pos": state.get("pos", 0),
            "total": len(state.get("queue", [])), "won": res.count("win"), "played": len(res)}


__all__ = ["Director", "Scenario", "build_scenarios", "is_active", "COMMANDS", "asdict"]
