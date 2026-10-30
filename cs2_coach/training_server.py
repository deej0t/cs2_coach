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

import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from . import server_stats as ss
from .practice import _calc_view_angle, _map_to_bspname

DEFAULT_MAPS = ["de_ancient", "de_anubis", "de_dust2", "de_inferno", "de_mirage", "de_nuke",
                "de_overpass", "de_train"]
STATE_FILE = ".training_server.json"
LOCK_FILE = ".training_server.lock"
MAX_SCENARIOS = 20
MIN_DISTANCE = 150  # naeher = Messer/Nahkampf-Zufall, kein Duell
FREEZE_TIME = 3
START_LEVEL = 1  # bot_difficulty 0-3
# Start so viele Sekunden VOR deinem Tod, an den Positionen aus der Demo.
# Rueckmeldung nach dem ersten Training: am Todesort "ist man zu schnell
# konfrontiert" - dort laeuft das Duell schon.
LEAD_SECONDS = 3
TICKRATE = 64
NON_GUNS = ("knife", "bayonet", "hegrenade", "inferno", "molotov", "incgrenade", "flashbang",
            "smokegrenade", "decoy", "world", "taser", "planted_c4")

COMMANDS = {"training", "train", "skip", "leichter", "schwerer", "stop", "pause", "weiter"}
IDLE_ROUNDS = 2  # so viele Runden ohne Treffer in Folge = pausieren
PAUSE_TIMEOUT = 900  # Sekunden pausiert, dann endet das Training

# Ohne Messer, Pistole und C4: die Waffe des Gegners ist die einzige des Bots,
# also hat er sie in der Hand. Im ersten Training hielten die Bots anfangs das
# Messer ("die sind anfangs unbewaffnet"), der T-Bot trug ausserdem die Bombe.
SETUP = [
    "sv_cheats 1", "sv_hibernate_when_empty 0", "mp_autoteambalance 0", "mp_limitteams 0",
    "bot_quota_mode normal", "bot_join_after_player 0", "bot_kick", "bot_quota 0",
    "bot_freeze 0", "bot_chatter off", "mp_warmuptime 999", "mp_warmup_start",
    f"mp_freezetime {FREEZE_TIME}", "mp_roundtime 1", "mp_round_restart_delay 2",
    "mp_maxrounds 99", "mp_buytime 0", "mp_free_armor 2", "mp_death_drop_gun 0",
    "mp_t_default_primary weapon_ak47", "mp_ct_default_primary weapon_m4a1",
    'mp_t_default_secondary ""', 'mp_ct_default_secondary ""',
    'mp_t_default_melee ""', 'mp_ct_default_melee ""', "mp_give_player_c4 0",
    "mp_halftime 0", "mp_match_can_clinch 0",
]
RESTORE = ["bot_kick", "bot_quota 0", "bot_freeze 0", 'bot_loadout ""', "mp_warmuptime 30",
           'mp_t_default_primary ""', 'mp_ct_default_primary ""',
           "mp_t_default_secondary weapon_glock", "mp_ct_default_secondary weapon_hkp2000",
           "mp_t_default_melee weapon_knife", "mp_ct_default_melee weapon_knife", "mp_give_player_c4 1",
           "sv_cheats 0", "sv_hibernate_when_empty 1"]

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
    Ergebnis je Todes-Tick: {"me": [x,y,z,pitch,yaw], "foe": [...], "lead": s}.
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
    df = parser.parse_ticks(["X", "Y", "Z", "pitch", "yaw"], ticks=sorted({s for s, _ in wanted.values()}))
    out = {}
    for tick, (start, killer) in wanted.items():
        rows = df[df["tick"] == start]

        def at(sid):
            r = rows[rows["steamid"].astype(str) == sid]
            if r.empty:
                return None
            r = r.iloc[0]
            return [round(float(r["X"]), 1), round(float(r["Y"]), 1), round(float(r["Z"]), 1),
                    _angle(r["pitch"]), _angle(r["yaw"])]

        mine, foe = at(me), at(killer)
        if mine and foe:
            out[tick] = {"me": mine, "foe": foe, "lead": round((tick - start) / TICKRATE, 1)}
    return out


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
        key = f"lead{lead:g}"
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
                data.get("match", {}).get("demo_file", "")))
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
                if time.time() - self.path.stat().st_mtime > 30:
                    self.path.unlink(missing_ok=True)  # verwaist
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


def _level_text(state: dict) -> str:
    return f"Bot-Stufe {state['level']}/3"


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

    def command(self, sid: str, name: str, cmd: str, arg: str) -> list[str]:
        """Antwortzeilen fuer einen Trainingsbefehl."""
        if sid not in _admins(self.cfg):
            return ["Das Training kann nur der Server-Besitzer starten."]
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
                state.update(paused=True, paused_at=time.time())
                save_state(self.path, state)
                self.send("mp_pause_match")
                self.timer(PAUSE_TIMEOUT, lambda stamp=state["paused_at"]: self._check_paused(stamp))
                return ["Pause ab der nächsten Runde. Weiter mit !weiter."]
            if cmd == "weiter":
                if not state.get("paused"):
                    return ["Das Training läuft schon."]
                state.update(paused=False, idle=0)
                save_state(self.path, state)
                self.send("mp_unpause_match")
                return ["Weiter geht's."]
            if cmd in ("leichter", "schwerer"):
                self._shift(state, -1 if cmd == "leichter" else 1)
                save_state(self.path, state)
                self.send(f"bot_difficulty {state['level']}")
                return [_level_text(state)]
        return []

    def _start(self, sid: str, name: str, arg: str) -> list[str]:
        all_sc = self.scenarios()
        if not arg:
            avail = ", ".join(f"{m.removeprefix('de_')} ({len(v)})" for m, v in sorted(all_sc.items()))
            return [f"Maps mit Situationen: {avail or 'keine'}. Start: !training <map>"]
        bsp = arg.lower().strip()
        bsp = bsp if bsp.startswith("de_") else f"de_{bsp}"
        if bsp not in self.maps():
            return [f"{bsp} ist nicht im Trainings-Pool ({', '.join(m.removeprefix('de_') for m in self.maps())})."]
        if not all_sc.get(bsp):
            return [f"Keine verlorenen Duelle auf {bsp} in deinen Matches."]
        if is_active(self.path):
            return ["Es läuft schon ein Training. Erst !stop."]
        version = self.send("coach_tp_version")[0]
        if not version.strip().startswith("coach_tp "):
            # Nach CS2-Updates ist Metamod oft inkompatibel; der cs2-waechter schaltet
            # es dann ab, damit der Server ueberhaupt laeuft - ohne Plugin kein Training.
            return ["Trainings-Plugin nicht geladen (nach einem CS2-Update?). Training gerade nicht möglich."]
        others = [h for h in _humans(self.send("status")[0]) if h != name]
        if others:
            return [f"Erst wenn der Server leer ist - gerade online: {', '.join(others)}."]
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
                     "pre": {str(i): v for i, v in pre.items()},
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
        state["level"] = max(0, min(3, state["level"] + step))

    def _stop(self, state: dict, why: str) -> list[str]:
        res = state.get("results", [])
        won = sum(1 for r in res if r == "win")
        summary = f"{why}: {won}/{len(res)} Duelle gewonnen" if res else why
        restore = (self.cfg.get("server_restore_command") or "").strip() or "map de_mirage"
        save_state(self.path, {})
        self.send(_say(summary), *RESTORE, restore)
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
            if ss.steam64(m.group("asid")) == state["human"] and phase in ("setup", "team"):
                # Gemessen 30.10.: Team gewaehlt 2 s bevor das Setup lief - in "setup"
                # nur merken, _setup holt dann den Bot, statt auf einen zweiten Wechsel zu warten.
                state.update(team=to, human_uid=uid)
                if phase == "team":
                    self._add_bot(state)
            elif m.group("asid") == "BOT" and phase in ("bot", "running"):
                if state.get("bot_uid") is None:
                    state.update(bot_uid=uid, phase="running", round=0, pos=0)
                    self.send(_say("Los geht's. !skip, !leichter / !schwerer, !pause / !weiter, !stop."),
                              "mp_warmup_end")
                elif uid != state["bot_uid"]:
                    # Am 30.10. brachte ein bot_add zwei Bots - jeder weitere fliegt.
                    self.send(f"kickid {uid}")
            return state
        if phase in ("team", "bot", "running") and (m := ss._LEAVE.match(msg)) \
                and ss.steam64(m.group("asid")) == state["human"]:
            # Sonst bliebe der Server mit sv_cheats 1 und Bot im Trainingsmodus.
            # Nicht in "loading"/"setup": beim Mapwechsel verbinden sich alle neu.
            self._stop(state, "Training abgebrochen (Verbindung getrennt)")
            return {}
        if phase != "running":
            return state
        if msg.startswith('World triggered "Round_Start"'):
            return self._round_start(state)
        if (m := ss._ATTACK.match(msg)) and ss.steam64(m.group("asid")) == state["human"]:
            state["active"] = True  # du hast in dieser Runde getroffen
            return state
        if (m := ss._KILL.match(msg)) and not state.get("outcome"):
            killer = ss.steam64(m.group("asid"))
            victim = ss.steam64(m.group("vsid"))
            if killer == state["human"] and m.group("vsid") == "BOT":
                state.update(outcome="win", active=True)
            elif victim == state["human"]:
                state["outcome"] = "loss"
            return state
        if msg.startswith('World triggered "Round_End"'):
            if not state.get("round"):
                return state  # Ende der Aufwaermphase
            skipped = state.pop("skip", False)
            if not skipped and not state.get("active"):
                return self._idle_round(state)
            state["idle"] = 0
            if not skipped:
                self._result(state, state.get("outcome") or "draw")
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
        if state["idle"] >= IDLE_ROUNDS and not state.get("paused"):
            stamp = time.time()
            state.update(paused=True, paused_at=stamp)
            self.send("mp_pause_match",
                      _say(f"Pausiert - {IDLE_ROUNDS} Runden ohne Treffer. Weiter mit !weiter, Ende mit !stop."))
            self.timer(PAUSE_TIMEOUT, lambda: self._check_paused(stamp))
        else:
            self.send(_say("Kein Treffer - das Duell kommt noch einmal."))
        return state

    def _check_paused(self, stamp: float) -> None:
        with _Lock(self.path):
            state = load_state(self.path)
            if state.get("paused") and state.get("paused_at") == stamp:
                self._stop(state, f"Training beendet ({PAUSE_TIMEOUT // 60} Minuten pausiert)")

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
            self.send(f"bot_difficulty {state['level']}", _say(f"Stark! Schwerer: {_level_text(state)}"))
        elif wins <= 1:
            self._shift(state, -1)
            state["window"] = []
            self.send(f"bot_difficulty {state['level']}", _say(f"Etwas leichter: {_level_text(state)}"))
        else:
            state["window"] = window[-4:]

    def _scenario(self, state: dict) -> Scenario:
        return self.scenarios()[state["map"]][state["queue"][state["pos"]]]

    def _round_start(self, state: dict) -> dict:
        """Beide an ihre Positionen LEAD_SECONDS vor deinem Tod (aus der Demo), mit Blickrichtung.

        Kein Einfrieren mehr nach der Freeze-Time: eingefrorene Bots schiessen
        nicht, im ersten Training fiel jeder Bot ohne einen Schuss.
        """
        state["round"] = state.get("round", 0) + 1
        state.update(outcome=None, active=False)
        sc = self._scenario(state)
        pre = state.get("pre", {}).get(str(state["queue"][state["pos"]]))
        if pre:
            me, foe = pre["me"], pre["foe"]
            when = f"{pre['lead']:g} s vor dem Duell"
        else:
            pitch, yaw = _calc_view_angle(sc.x, sc.y, sc.z + 64, sc.ex, sc.ey, sc.ez + 64)
            bpitch, byaw = _calc_view_angle(sc.ex, sc.ey, sc.ez + 64, sc.x, sc.y, sc.z + 64)
            me, foe = [sc.x, sc.y, sc.z, pitch, yaw], [sc.ex, sc.ey, sc.ez, bpitch, byaw]
            when = "am Todesort (Demo fehlt)"
        n, total = state["pos"] + 1, len(state["queue"])
        replies = self.send(f"coach_tp {state['human_uid']} {' '.join(str(v) for v in me)}",
                            f"coach_tp {state['bot_uid']} {' '.join(str(v) for v in foe)}",
                            _say(f"Duell {n}/{total} ({sc.source}): {sc.opponent} mit {sc.weapon}, "
                                 f"{when}, {_level_text(state)}"))
        failed = [r.strip() for r in replies[:2] if "coach_tp ok" not in r]
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
            self.send(*SETUP, f"bot_difficulty {state['level']}", _say(hint))
            if team:
                self._add_bot(state)
            else:
                state["phase"] = "team"
            save_state(self.path, state)

    def _add_bot(self, state: dict) -> None:
        """Genau einen Bot ins Gegnerteam holen; sein Teambeitritt startet das Training."""
        state["phase"] = "bot"
        bot_team = "ct" if state["team"] == "TERRORIST" else "t"
        first = self.scenarios()[state["map"]][state["queue"][0]]
        # bot_quota statt bot_add: am 30.10. brachte ein "bot_add_ct" zwei Bots
        # (die Map-Konfiguration setzt bot_quota_mode competitive).
        self.send(f'bot_loadout "{first.weapon}"', "bot_quota_mode normal",
                  f"bot_join_team {bot_team}", "bot_quota 1")


def state_summary(config_path: Path) -> dict | None:
    """Fuer die Webseite: laeuft ein Training, und wie steht es?"""
    state = load_state(config_path)
    if not state:
        return None
    res = state.get("results", [])
    return {"map": state.get("map"), "phase": state.get("phase"), "pos": state.get("pos", 0),
            "total": len(state.get("queue", [])), "won": res.count("win"), "played": len(res)}


__all__ = ["Director", "Scenario", "build_scenarios", "is_active", "COMMANDS", "asdict"]
