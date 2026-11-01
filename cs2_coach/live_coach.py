"""Live-Coach in echten Matches ueber Game State Integration (GSI).

Der eigene CS2-Client schickt mehrmals pro Sekunde seinen Zustand an
/api/gsi/<token> (offizielle Valve-Schnittstelle; nur die eigenen Daten,
keine Gegnerpositionen). Der Coach zaehlt geworfene Granaten und Granaten,
die du mit ins Grab nimmst, und gibt kurze Hinweise auf /live (Handy oder
zweiter Bildschirm, optional vorgelesen).

Warum Granaten: ueber 93 Matches hingen geworfene Granaten pro Runde am
deutlichsten mit Siegen zusammen (d = 0,94, robust; siehe CLAUDE.md). Das
Briefing sagt es vor dem Match - im Match vergisst man es. Das Ziel pro Runde
ist der Median deiner eigenen Siege, nicht ein fester Wert.

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
LATE_SECONDS = 35  # so viel Rundenzeit uebrig und noch keine Granate geworfen -> Hinweis
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

def target_per_round(cfg: dict, map_name: str | None = None) -> float | None:
    """Median der Granaten pro Runde in deinen gewonnenen Matches (auf dieser Map, wenn >= 5)."""
    from .utility_analysis import _rates
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
        rates = _rates(data)
        if not rates:
            continue
        all_wins.append(rates["total"])
        if map_name and map_name.removeprefix("de_") in f.name:
            map_wins.append(rates["total"])
    pool = map_wins if len(map_wins) >= 5 else all_wins
    return round(median(pool), 2) if pool else None


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
            "target": target_per_round(cfg, map_name), "rounds": {}, "half": 1, "round": 0,
            "last_nades": {}, "alive": True, "hint_id": 0, "log": []}


def _round(state: dict, n: int) -> dict:
    return state["rounds"].setdefault(str(n), {"thrown": 0, "wasted": 0, "half": state["half"]})


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
        _hint(state, f"Halbzeit: {thrown} Granaten in {rounds} Runden ({thrown / max(rounds, 1):.1f} pro Runde"
                     + (f", Ziel {state['target']:.1f}" if state.get("target") else "") + f"), {wasted} mit ins Grab.",
              "halftime")
        state["half"] = 2

    if n != state["round"]:
        prev = state["rounds"].get(str(state["round"]))
        state["round"] = n
        state["late_hinted"] = False
        _round(state, n)
        if n > 1 and phase == "freezetime":
            thrown, rounds, wasted = half_stats(state)
            done = max(rounds - 1, 0)  # die neue Runde laeuft noch nicht
            target = state.get("target")
            text = f"Runde {n}: {thrown} Granaten in {done} Runden"
            if target and done:
                soll = target * done
                text += (f" - im Soll (Ziel {soll:.0f})." if thrown >= soll
                         else f" - Ziel waeren {soll:.0f}. Diese Runde Granaten einplanen!")
            if prev and prev.get("wasted"):
                text += f" Letzte Runde {prev['wasted']} mit ins Grab genommen."
            _hint(state, text, "round")

    if not own and player.get("steamid") and phase == "live" and state.get("alive", True):
        # Du bist tot: GSI zeigt jetzt den Mitspieler, den du beobachtest.
        _round(state, n)["wasted"] += sum(state.get("last_nades", {}).values())
        state["alive"], state["last_nades"] = False, {}
    if own:
        nades = grenades(player)
        health = int((player.get("state") or {}).get("health") or 0)
        cur = _round(state, n)
        if phase == "live" and state.get("alive", True):
            for k, before in state.get("last_nades", {}).items():
                drop = before - nades.get(k, 0)
                if drop > 0 and health > 0:
                    cur["thrown"] += drop
            if health == 0 and state.get("last_nades"):
                cur["wasted"] += sum(state["last_nades"].values())
        state["alive"] = health > 0
        state["last_nades"] = nades if health > 0 else {}
        left = float((payload.get("phase_countdowns") or {}).get("phase_ends_in") or 99)
        if (phase == "live" and health > 0 and nades and not cur["thrown"] and left <= LATE_SECONDS
                and not state.get("late_hinted")):
            state["late_hinted"] = True
            _hint(state, f"Noch {_names(nades)} - jetzt einsetzen, noch {left:.0f} s.", "late")

    if mp.get("phase") == "gameover" and not state.get("over"):
        state["over"] = True
        thrown = sum(r["thrown"] for r in state["rounds"].values())
        wasted = sum(r["wasted"] for r in state["rounds"].values())
        rounds = len(state["rounds"])
        ct, t = (mp.get("team_ct") or {}).get("score"), (mp.get("team_t") or {}).get("score")
        state["summary"] = {"thrown": thrown, "wasted": wasted, "rounds": rounds, "score": f"{ct}:{t}"}
        _hint(state, f"Match vorbei ({ct}:{t}): {thrown} Granaten in {rounds} Runden "
                     f"({thrown / max(rounds, 1):.1f} pro Runde), {wasted} mit ins Grab.", "gameover")
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
- Granaten sind laut seinen eigenen Daten der staerkste Sieg-Faktor; das Ziel ist der Median aus seinen Siegen."""


def ai_facts(state: dict) -> str:
    rows = sorted(state.get("rounds", {}).items(), key=lambda kv: int(kv[0]))
    per_round = ", ".join(f"R{k}: {v['thrown']} geworfen" + (f"/{v['wasted']} ins Grab" if v["wasted"] else "")
                          for k, v in rows)
    thrown = sum(v["thrown"] for _, v in rows)
    wasted = sum(v["wasted"] for _, v in rows)
    target = state.get("target")
    lines = [f"Map {state.get('map')}, {len(rows)} Runden gespielt, Halbzeit {state.get('half')}.",
             f"Granaten geworfen: {thrown} ({thrown / max(len(rows), 1):.1f} pro Runde)"
             + (f", Ziel aus seinen Siegen {target:.2f} pro Runde" if target else "") + ".",
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
