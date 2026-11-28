"""Uebungsmodus (!uebung): sv_cheats 1, Zeit steht, Geld ohne Ende, Godmode, Tasten-HUD.

Wunsch 21.11.: ein reiner Trainingsmodus auf der aktuellen Map - zum Ueben von
Bewegung, Spray und Lineups, ohne dass eine Runde endet oder jemand stirbt.

- Cvars setzt der Coach per RCON; vorher liest er die aktuellen Werte und
  stellt genau diese beim Ausschalten wieder her (aim_multigame setzt eigene
  Werte, Standardwerte waeren dort falsch).
- Godmode, Geld (alle 0,5 s auf 16000) und die Tasten-Anzeige (W/A/S/D,
  JUMP, DUCK, SHIFT, L/R/L+R) macht das Plugin (``coach_practice``,
  ``coach_keys``, ab 1.29.0). ``!tasten`` schaltet die Anzeige auch ausserhalb
  des Modus fuer sich selbst.
- Einschalten darf der Besitzer (steam_id/training_admins) oder wer allein auf
  dem Server ist - sv_cheats 1 gibt jedem noclip.
- ``!rethrow`` (``!nochmal``): das Plugin (``coach_rethrow``, ab 1.30.0) fuehrt
  ``sv_rethrow_last_grenade`` im Namen des Spielers aus - aus der Client-Konsole
  und per RCON (ohne Spieler) passiert auf dem dedizierten Server nichts.
  ``!zurueck`` setzt dich an die Stelle und Blickrichtung deines letzten
  gelandeten Wurfs (``coach_nade_last``, bei Sprungwuerfen der Absprungpunkt).
- Ein Mapwechsel setzt die Cvars NICHT zurueck (28.11.: auf aim_multigame lief
  sv_showimpacts weiter). ``on_lines`` stellt deshalb beim Mapwechsel die
  gesicherten Werte wieder her; das Plugin schaltet sich selbst ab (OnMapStart).
- Wiederherstellen immer mit ``sv_cheats`` zuletzt: Cheat-Cvars
  (sv_showimpacts, sv_infinite_ammo, ...) lassen sich nach sv_cheats 0 nicht
  mehr aendern - genau das liess "!uebung aus" halb scheitern.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from .users import shared_dir   # Server/Demo-Zustand: einmal fuer alle Spieler

STATE_FILE = ".practice.json"
RETHROW = {"rethrow", "nochmal"}
IMPACTS = {"impacts", "impact", "einschlaege", "einschläge"}
BACK = {"zurueck", "zurück", "zuruck", "back"}
CLEAR = {"clear", "weg", "clean"}
COMMANDS = {"uebung", "übung", "ubung", "practice", "prac", "tasten", "keys"} | RETHROW | BACK | IMPACTS | CLEAR

SETUP = {
    "sv_cheats": "1",
    "mp_ignore_round_win_conditions": "1",  # Runde endet nie
    "mp_roundtime": "60", "mp_roundtime_defuse": "60", "mp_roundtime_hostage": "60",
    "mp_freezetime": "0",
    "mp_buytime": "99999", "mp_buy_anywhere": "1",
    "mp_maxmoney": "65535", "mp_startmoney": "16000", "mp_afterroundmoney": "16000",
    "mp_respawn_on_death_t": "1", "mp_respawn_on_death_ct": "1",
    "sv_infinite_ammo": "2",  # volle Magazine nachladen, ohne Reserve-Ende
    "sv_grenade_trajectory_prac_pipreview": "1",
    "mp_warmup_pausetimer": "1",
    "sv_showimpacts": "1",  # Einschlaege: rot = Server, blau = Client; !impacts schaltet
}
_VALUE = re.compile(r"^\s*(\S+)\s*=\s*(.*?)\s*$")


def _path(config_path: Path) -> Path:
    return shared_dir(config_path) / STATE_FILE


def load(config_path: Path) -> dict:
    try:
        return json.loads(_path(config_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(config_path: Path, st: dict) -> None:
    _path(config_path).write_text(json.dumps(st), encoding="utf-8")


def read_cvars(send, names) -> dict[str, str]:
    """Aktuelle Werte per RCON ("mp_roundtime = 1.92"); true/false -> 1/0."""
    out = {}
    for name, reply in zip(names, send(*names)):
        m = _VALUE.match((reply or "").strip().splitlines()[0] if (reply or "").strip() else "")
        if m and m.group(1) == name:
            v = m.group(2).strip('"')
            out[name] = {"true": "1", "false": "0"}.get(v.lower(), v)
    return out


def restore_cmds(saved: dict) -> list[str]:
    """RCON-Befehle zum Zuruecksetzen; sv_cheats als letztes (sonst greifen Cheat-Cvars nicht)."""
    saved = {k: v for k, v in (saved or {}).items() if k in SETUP}
    for k, v in SETUP.items():  # Wert nicht lesbar gewesen -> trotzdem abschalten
        if k not in saved and k in ("sv_showimpacts", "sv_infinite_ammo",
                                                  "sv_grenade_trajectory_prac_pipreview",
                                                  "mp_ignore_round_win_conditions", "mp_buy_anywhere"):
            saved[k] = "0"
    cheats = saved.pop("sv_cheats", "0")
    return [f"{k} {v}" for k, v in saved.items()] + [f"sv_cheats {cheats}"]


def handle(cfg: dict, config_path: Path, send, sid: str, name: str, cmd: str, arg: str,
           uid: int | None = None) -> list[str]:
    from .training_server import _admins, _humans, is_active
    st = load(config_path)
    word = arg.strip().lower()

    if cmd in ("tasten", "keys"):
        if uid is None:
            return []
        shown = set(st.get("keys", []))
        on = uid not in shown if word not in ("an", "on", "aus", "off") else word in ("an", "on")
        shown = (shown | {uid}) if on else (shown - {uid})
        st["keys"] = sorted(shown)
        save(config_path, st)
        send(f"coach_keys {uid} {1 if on else 0}")
        return [f"Tasten-Anzeige fuer {name} {'an' if on else 'aus'}."]

    if cmd in IMPACTS:
        if not st.get("on"):
            return ["Geht nur im Uebungsmodus (sv_cheats 1) - erst !uebung."]
        cur = read_cvars(send, ["sv_showimpacts"]).get("sv_showimpacts", "0")
        on = cur == "0" if word not in ("an", "on", "aus", "off") else word in ("an", "on")
        send(f"sv_showimpacts {1 if on else 0}")
        return [f"Einschlaege {'an' if on else 'aus'}."]

    if cmd in CLEAR:
        # Wie der Ping, aber per Chat - falls die Ping-Taste nicht ankommt (Plugin ab 1.31.0)
        if not st.get("on"):
            return ["Geht nur im Uebungsmodus - erst !uebung."]
        reply = (send("coach_clear")[0] or "").strip()
        if not reply.startswith("coach_clear ok"):
            return ["Wegraeumen braucht das Coach-Plugin ab 1.31.0."]
        return [f"Weggeraeumt: {reply.split()[-1]} Granaten/Smokes/Feuer."]

    if cmd in RETHROW | BACK:
        if not st.get("on"):
            return ["Geht nur im Uebungsmodus (sv_cheats 1) - erst !uebung."]
        if cmd in RETHROW:
            # Der Befehl braucht einen Spieler als Absender (Plugin ab 1.30.0);
            # ohne Plugin bleibt nur RCON, das auf dem Server meist nichts tut.
            reply = send(f"coach_rethrow {uid}")[0] if uid is not None else ""
            if not (reply or "").strip().startswith("coach_rethrow ok"):
                send("sv_rethrow_last_grenade")
            return []
        from .own_lineups import parse_last
        if uid is None:
            return []
        last = parse_last(send(f"coach_nade_last {uid}")[0].strip())
        if last is None:
            return ["Noch kein gelandeter Wurf von dir - erst werfen, dann !zurueck."]
        x, y, z = last["pos"]
        pitch, yaw = last["ang"]
        send(f"coach_tp {uid} {x} {y} {z} {pitch} {yaw}")
        return []

    if word in ("aus", "off", "stop", "ende"):
        if not st.get("on"):
            return ["Der Uebungsmodus ist nicht an."]
        send(*restore_cmds(st.get("saved")), "coach_practice 0", "mp_restartgame 1")
        st.update(on=False, saved={}, keys=[])
        save(config_path, st)
        return [f"Uebungsmodus aus ({name}) - vorherige Einstellungen wiederhergestellt, Neustart der Runde."]

    if st.get("on"):
        return ["Uebungsmodus laeuft schon. Beenden: !uebung aus · Tasten: !tasten"]
    if is_active(config_path):
        return ["Waehrend eines Trainings geht der Uebungsmodus nicht (!stop beendet das Training)."]
    if sid not in _admins(cfg):
        humans = _humans(send("status")[0])
        if len(humans) > 1:
            return ["Den Uebungsmodus darf mit anderen auf dem Server nur der Besitzer starten (sv_cheats 1)."]
    saved = read_cvars(send, list(SETUP))
    send(*[f"{k} {v}" for k, v in SETUP.items()], "mp_warmup_end", "mp_restartgame 1")
    # nach dem Rundenneustart: Spawn setzt Godmode erneut
    plugin = (send("coach_practice 1")[0] or "").strip().startswith("coach_practice ok")
    st.update(on=True, saved=saved, by=name)
    save(config_path, st)
    if not plugin:
        # 25.11.: CS2-Update, CounterStrikeSharp abgeschaltet - die Ansage versprach trotzdem
        # Godmode und Ping-Wegraeumen, und der Ping "ging nicht"
        return [f"Uebungsmodus an ({name}): sv_cheats 1, Zeit steht, 16000 $ ueberall kaufen. "
                "Coach-Plugin gerade nicht geladen (meist nach einem CS2-Update) - ohne Godmode, "
                "Tasten-Anzeige, Ping-Wegraeumen, !clear und !rethrow. Beenden: !uebung aus"]
    return [f"Uebungsmodus an ({name}): sv_cheats 1, Zeit steht, 16000 $ ueberall kaufen, Godmode, "
            "Respawn, Tasten-Anzeige, Einschlaege. Ping oder !clear raeumt Smokes/Feuer weg. Granate nochmal: !rethrow · zum Wurfpunkt: !zurueck · "
            "Beenden: !uebung aus · an/aus: !tasten · !impacts"]


def on_lines(config_path: Path, lines: list[str], send=None, owners=()) -> None:
    """Mapwechsel: Cvars bleiben stehen -> gesicherte Werte zuruecksetzen, Zustand vergessen.

    Sofort beim "Loading map" senden: das Map-/Modus-Skript (und map_settings
    2 s spaeter) setzt danach seine eigenen Werte darueber.
    """
    if not any("Loading map" in ln for ln in lines):
        return
    if send is not None and owners:
        try:  # Plugin-Zustand ueberlebt keinen Server-Neustart -> bei jeder Map neu setzen
            send("coach_vote_owner " + " ".join(sorted(owners)))
        except Exception as e:  # noqa: BLE001
            print(f"[practice] vote owner: {e}", flush=True)
    st = load(config_path)
    if st.get("on"):
        if send is not None:
            try:
                send(*restore_cmds(st.get("saved")))
            except Exception as e:  # noqa: BLE001
                print(f"[practice] restore: {e}", flush=True)
        st.update(on=False, saved={}, keys=[])
        save(config_path, st)
