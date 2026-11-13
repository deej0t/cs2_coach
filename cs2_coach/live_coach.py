"""Live-Coach in echten Matches ueber Game State Integration (GSI).

Der eigene CS2-Client schickt mehrmals pro Sekunde seinen Zustand an
/api/gsi/<token> (offizielle Valve-Schnittstelle; nur die eigenen Daten,
keine Gegnerpositionen). Der Coach zaehlt geworfene Granaten und Granaten,
die du mit ins Grab nimmst, und gibt kurze Hinweise auf /live (Handy oder
zweiter Bildschirm, optional vorgelesen).

Warum Granaten, und warum frueh: seit 13.11. zaehlt die *fruehe Granate im
Full Buy* - in Full-Buy-Runden, die du 35 s ueberlebst, eine Granate in den
ersten 35 s (wie utility_analysis.early_utility und das Briefing-Ziel).
Die blosse Granatenzahl steigt zum grossen Teil, weil man Runden gewinnt
(der Gegner wirft in deinen Siegen 1,05 statt 1,44 pro Runde); die fruehe
Granate trennt Siege belastbar (79,5 gegen 64,8 %, d = +0,73), je Runde
63 gegen 56 % gewonnen. Ziel ist der Median deiner eigenen Siege.

Bis 13.11. zaehlte er nur Granaten pro Runde: ueber 17 Matches ohne
messbaren Effekt (d = 0,10) - "wirf mehr" war im Match zu abstrakt.

Zustand liegt in .live_state.json neben der Config (mehrere Gunicorn-Worker),
fertige Matches in live_matches/ - Grundlage fuer den spaeteren Vergleich
"mit Live-Coach gegen ohne".
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from statistics import median

STATE_FILE = ".live_state.json"
LOCK_FILE = ".live_state.lock"
TOKEN_FILE = ".gsi_token"
MATCHES_DIR = "live_matches"
# Nur echte Matches. Am 1.11. lief der Live-Coach im !training und auf der Aim-Map mit -
# dort sind Granaten-Hinweise Rauschen (im Duell wirft man selten), und aim_multigame
# waere als "Match" gespeichert worden.
# Ausschliessen statt zulassen: welchen Modus-Namen GSI fuer Premier meldet, ist nicht
# gemessen - eine Zulass-Liste koennte echte Matches verschlucken.
SKIP_MODES = {"deathmatch", "casual", "armsrace", "gungameprogressive", "gungametrbomb", "training",
              "custom", "survival", "coopmission"}
EARLY_SECONDS = 35     # wie utility_analysis.EARLY_SECONDS
EARLY_HINT_AT = 20     # so lange im Full Buy ohne Granate -> Erinnerung
FULL_BUY = 3700        # wie parser._FORCE_THRESHOLD (Ausruestungswert bei Rundenstart)
PISTOL_ROUNDS = (1, 13)
GRENADE_NAMES = {"weapon_hegrenade": "HE", "weapon_molotov": "Molotov", "weapon_incgrenade": "Brandgranate",
                 "weapon_flashbang": "Flash", "weapon_smokegrenade": "Smoke", "weapon_decoy": "Decoy"}


# -- Ablage ----------------------------------------------------------------------

def load_or_create_token(config_path: Path) -> str:
    """Geheimer Teil der GSI-URL (wie .serverlog_token: O_EXCL, damit alle Worker denselben haben)."""
    import secrets
    path = Path(config_path).parent / TOKEN_FILE
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        for _ in range(50):
            token = path.read_text(encoding="utf-8").strip()
            if token:
                return token
            time.sleep(0.02)
        raise
    token = secrets.token_urlsafe(24)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token)
    return token


class _Lock:
    def __init__(self, config_path: Path):
        self.path = Path(config_path).parent / LOCK_FILE

    def __enter__(self):
        for _ in range(200):
            try:
                os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > 10:
                        self.path.unlink()
                except OSError:
                    pass
                time.sleep(0.02)
        return self

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except OSError:
            pass


def load_state(config_path: Path) -> dict:
    try:
        return json.loads((Path(config_path).parent / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(config_path: Path, state: dict) -> None:
    (Path(config_path).parent / STATE_FILE).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


# -- Ziel aus den eigenen Siegen ------------------------------------------------------

def target_early(cfg: dict, map_name: str | None = None) -> float | None:
    """Median-Quote "fruehe Granate im Full Buy" (%) in deinen Siegen (auf dieser Map, wenn >= 5)."""
    from .training import EARLY_KEY, _value
    vault = (cfg.get("obsidian_vault_path") or "").strip()
    if not vault:
        return None
    folder = Path(vault) / cfg.get("coach_subfolder", "CS2-Coach") / "exports"
    all_wins, map_wins = [], []
    for f in folder.glob("*_coach.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (data.get("match") or {}).get("result") != "Sieg":
            continue
        rate = _value(data, EARLY_KEY)
        if rate is None:
            continue
        all_wins.append(rate)
        if map_name and map_name.removeprefix("de_") in f.name:
            map_wins.append(rate)
    pool = map_wins if len(map_wins) >= 5 else all_wins
    return round(median(pool), 1) if pool else None


# -- GSI auswerten ------------------------------------------------------------------

def grenades(player: dict) -> dict[str, int]:
    """Granaten im Inventar: {"weapon_hegrenade": 1, "weapon_flashbang": 2}."""
    out: dict[str, int] = {}
    for w in ((player or {}).get("weapons") or {}).values():
        # Decoys zaehlen nicht - das Ziel aus den Exporten kennt nur Smoke, Flash, HE, Molotov.
        if (w or {}).get("type") == "Grenade" and w.get("name") != "weapon_decoy":
            name = w.get("name", "")
            out[name] = out.get(name, 0) + max(1, int(w.get("ammo_reserve") or 1))
    return out


def _names(nades: dict[str, int]) -> str:
    return ", ".join(f"{n}x {GRENADE_NAMES.get(k, k)}" if n > 1 else GRENADE_NAMES.get(k, k)
                     for k, n in nades.items())


def _hint(state: dict, text: str, kind: str) -> None:
    state["hint_id"] = state.get("hint_id", 0) + 1
    state["hint"] = {"id": state["hint_id"], "text": text, "kind": kind, "at": time.time()}
    state.setdefault("log", []).append(state["hint"])
    state["log"] = state["log"][-12:]


def _new_match(map_name: str, cfg: dict) -> dict:
    return {"map": map_name, "started": datetime.now().isoformat(timespec="seconds"),
            "target": target_early(cfg, map_name), "rounds": {}, "half": 1, "round": 0,
            "last_nades": {}, "alive": True, "hint_id": 0, "log": []}


def _round(state: dict, n: int) -> dict:
    return state["rounds"].setdefault(str(n), {"thrown": 0, "wasted": 0, "half": state["half"]})


def early_stats(state: dict, half: int | None = None) -> tuple[int, int]:
    """(Runden mit frueher Granate, gezaehlte Full-Buy-Runden).

    Gezaehlt wird eine Runde, sobald sie entschieden ist: frueh geworfen,
    oder 35 s ueberlebt bzw. vorbei. Vor 35 s gestorben = nicht gezaehlt.
    """
    k = n = 0
    for key, r in state.get("rounds", {}).items():
        if not r.get("full") or (half is not None and r.get("half") != half):
            continue
        if r.get("early"):
            k, n = k + 1, n + 1
            continue
        died = r.get("died_s")
        if died is not None and died < EARLY_SECONDS:
            continue
        if r.get("survived_early") or int(key) != state.get("round"):
            n += 1
    return k, n


def _early_text(state: dict, half: int | None = None) -> str:
    k, n = early_stats(state, half)
    if not n:
        return ""
    target = state.get("target")
    return f"fruehe Granate {k}/{n} Full Buys" + (f" (Ziel {target:.0f} %)" if target else "")


def half_stats(state: dict) -> tuple[int, int, int]:
    """(geworfen, Runden, mit ins Grab) in der laufenden Halbzeit, nur abgeschlossene + laufende Runde."""
    rs = [r for r in state.get("rounds", {}).values() if r.get("half") == state.get("half")]
    return sum(r["thrown"] for r in rs), len(rs), sum(r["wasted"] for r in rs)


def update(state: dict, payload: dict, cfg: dict) -> dict:
    """Einen GSI-Stapel verarbeiten; gibt den neuen Zustand zurueck."""
    me = str(cfg.get("steam_id") or (payload.get("provider") or {}).get("steamid") or "")
    mp, rnd, player = payload.get("map") or {}, payload.get("round") or {}, payload.get("player") or {}
    if not mp:  # Hauptmenue
        return state
    map_name = mp.get("name", "")
    if (mp.get("phase") == "warmup" or mp.get("mode") in SKIP_MODES
            or not map_name.startswith(("de_", "cs_"))):
        return state
    if not state or state.get("map") != map_name or state.get("over"):
        if mp.get("phase") == "gameover":
            return state
        state = _new_match(map_name, cfg)
    n = int(mp.get("round") or 0) + 1  # GSI zaehlt abgeschlossene Runden
    phase = rnd.get("phase", "")
    own = str(player.get("steamid", "")) == me  # tot: GSI zeigt den beobachteten Mitspieler

    if mp.get("phase") == "intermission" and state["half"] == 1 and n > 12:
        thrown, rounds, wasted = half_stats(state)
        early = _early_text(state, 1)
        _hint(state, "Halbzeit: " + (f"{early}, " if early else "")
                     + f"{thrown} Granaten in {rounds} Runden, {wasted} mit ins Grab.", "halftime")
        state["half"] = 2

    if n != state["round"]:
        prev = state["rounds"].get(str(state["round"]))
        state["round"] = n
        state["late_hinted"] = False
        _round(state, n)
        if n > 1 and phase == "freezetime":
            early = _early_text(state)
            text = f"Runde {n}: " + (early + "." if early else "noch kein Full Buy gezaehlt.")
            if n not in PISTOL_ROUNDS:
                text += " Im Full Buy: eine Granate in den ersten 35 s."
            if prev and prev.get("wasted"):
                text += f" Letzte Runde {prev['wasted']} mit ins Grab genommen."
            _hint(state, text, "round")

    if not own and player.get("steamid") and phase == "live" and state.get("alive", True):
        # Du bist tot: GSI zeigt jetzt den Mitspieler, den du beobachtest.
        _round(state, n)["wasted"] += sum(state.get("last_nades", {}).values())
        state["alive"], state["last_nades"] = False, {}
    stamp = (payload.get("provider") or {}).get("timestamp")
    now = float(stamp) if stamp is not None else time.time()
    if own:
        nades = grenades(player)
        pstate = player.get("state") or {}
        health = int(pstate.get("health") or 0)
        cur = _round(state, n)
        if phase == "live" and "live_at" not in cur:
            # Rundenstart: Uhr fuer "frueh" und Kaufart wie im Parser
            cur["live_at"] = now
            cur["full"] = (n not in PISTOL_ROUNDS and health > 0
                           and int(pstate.get("equip_value") or 0) >= FULL_BUY)
        elapsed = now - cur.get("live_at", now)
        if phase == "live" and state.get("alive", True):
            for k, before in state.get("last_nades", {}).items():
                drop = before - nades.get(k, 0)
                if drop > 0 and health > 0:
                    cur["thrown"] += drop
                    if elapsed <= EARLY_SECONDS:
                        cur["early"] = True
            if health == 0 and state.get("last_nades"):
                cur["wasted"] += sum(state["last_nades"].values())
            if health == 0 and "died_s" not in cur:
                cur["died_s"] = round(elapsed, 1)
            if health > 0 and elapsed >= EARLY_SECONDS:
                cur["survived_early"] = True
        state["alive"] = health > 0
        state["last_nades"] = nades if health > 0 else {}
        if (phase == "live" and cur.get("full") and health > 0 and nades and not cur.get("early")
                and EARLY_HINT_AT <= elapsed < EARLY_SECONDS and not state.get("late_hinted")):
            state["late_hinted"] = True
            _hint(state, f"Noch {EARLY_SECONDS - elapsed:.0f} s fuer die fruehe Granate: {_names(nades)}.",
                  "early")

    if mp.get("phase") == "gameover" and not state.get("over"):
        state["over"] = True
        thrown = sum(r["thrown"] for r in state["rounds"].values())
        wasted = sum(r["wasted"] for r in state["rounds"].values())
        rounds = len(state["rounds"])
        ct, t = (mp.get("team_ct") or {}).get("score"), (mp.get("team_t") or {}).get("score")
        k, m = early_stats(state)
        state["summary"] = {"thrown": thrown, "wasted": wasted, "rounds": rounds, "score": f"{ct}:{t}",
                            "early": k, "full_buys": m}
        early = _early_text(state)
        _hint(state, f"Match vorbei ({ct}:{t}): " + (f"{early}, " if early else "")
                     + f"{thrown} Granaten in {rounds} Runden, {wasted} mit ins Grab.", "gameover")
    return state


def save_match(config_path: Path, state: dict) -> Path | None:
    if not state.get("summary"):
        return None
    folder = Path(config_path).parent / MATCHES_DIR
    folder.mkdir(exist_ok=True)
    path = folder / f"{state['started'].replace(':', '-')}_{state['map']}.json"
    path.write_text(json.dumps({k: state[k] for k in ("map", "started", "target", "rounds", "summary")},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def ingest(cfg: dict, config_path: Path, payload: dict) -> None:
    from .training_server import is_active
    if is_active(config_path):
        return
    with _Lock(config_path):
        before = load_state(config_path)
        state = update(dict(before), payload, cfg)
        if state.get("over") and not before.get("over"):
            save_match(config_path, state)
        if state != before:
            save_state(config_path, state)
    # KI-Hinweis nur zu Halbzeit und Matchende: Gemini braucht Sekunden bis Minuten
    # (503 und Wiederholungen, siehe Briefing) - fuer Hinweise in der Runde zu langsam,
    # die bleiben regelbasiert und sofort.
    hint = state.get("hint") or {}
    if hint.get("kind") in ("halftime", "gameover") and hint.get("id") != (before.get("hint") or {}).get("id"):
        threading.Thread(target=ai_hint, args=(cfg, config_path, hint["id"]), daemon=True).start()


AI_SYSTEM = """Du bist der CS2-Coach eines einzelnen Spielers, der gerade ein Match spielt.
- Deutsch, du-Form, hoechstens 2 kurze Saetze, zusammen hoechstens 220 Zeichen - er liest es in der Halbzeit.
- Nur aus den Fakten. Erfinde keine Zahlen. Kein Markdown, keine Emojis.
- Ein konkreter Hinweis fuer die naechste Halbzeit bzw. das naechste Match, kein Lob ohne Inhalt.
- Laut seinen eigenen Daten zaehlt die fruehe Granate im Full Buy (in den ersten 35 s, solange er lebt);
  das Ziel ist der Median-Anteil aus seinen Siegen."""


def ai_facts(state: dict) -> str:
    rows = sorted(state.get("rounds", {}).items(), key=lambda kv: int(kv[0]))
    per_round = ", ".join(f"R{k}: {v['thrown']} geworfen" + (f"/{v['wasted']} ins Grab" if v["wasted"] else "")
                          for k, v in rows)
    thrown = sum(v["thrown"] for _, v in rows)
    wasted = sum(v["wasted"] for _, v in rows)
    target = state.get("target")
    k, m = early_stats(state)
    lines = [f"Map {state.get('map')}, {len(rows)} Runden gespielt, Halbzeit {state.get('half')}.",
             f"Fruehe Granate im Full Buy: in {k} von {m} Full-Buy-Runden"
             + (f", Ziel aus seinen Siegen {target:.0f} %" if target else "") + ".",
             f"Granaten geworfen: {thrown} ({thrown / max(len(rows), 1):.1f} pro Runde).",
             f"Mit ins Grab genommen: {wasted}.", f"Je Runde: {per_round}."]
    if state.get("summary"):
        lines.append(f"Endstand {state['summary']['score']} (CT:T).")
    return "\n".join(lines)


def ai_hint(cfg: dict, config_path: Path, after_id: int, ask_fn=None) -> str | None:
    from .ingame_coach import ask, clean
    state = load_state(config_path)
    try:
        text = (ask_fn or ask)(cfg, AI_SYSTEM, ai_facts(state) + "\n\nGib den Hinweis.")
    except Exception as e:  # noqa: BLE001 - ohne KI bleibt der Regel-Hinweis stehen
        print(f"[live] KI-Hinweis: {e}", flush=True)
        return None
    if not text:
        return None
    text = clean(text, 240)
    with _Lock(config_path):
        state = load_state(config_path)
        if state.get("map"):
            _hint(state, f"Coach: {text}", "ai")
            save_state(config_path, state)
    return text


def gsi_config(url: str, token: str) -> str:
    """Datei fuer ...\\game\\csgo\\cfg\\gamestate_integration_cs2coach.cfg"""
    return f'''"CS2 Coach Live"
{{
    "uri"       "{url}"
    "timeout"   "5.0"
    "buffer"    "0.1"
    "throttle"  "0.5"
    "heartbeat" "10.0"
    "auth"
    {{
        "token" "{token}"
    }}
    "data"
    {{
        "provider"         "1"
        "map"              "1"
        "round"            "1"
        "player_id"        "1"
        "player_state"     "1"
        "player_weapons"   "1"
        "phase_countdowns" "1"
    }}
}}
'''
