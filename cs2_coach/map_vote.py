"""Mapwechsel per Abstimmung im Chat: !map <name>, Zustimmung mit !ja.

Seit dem 2.11. wartet der Server auf de_mirage statt auf der Workshop-Map
aim_multigame - der Wechsel weg von ihr liess den Server zweimal haengen,
waehrend ein Spieler verbunden war (30.10. und 2.11., WatchDog). Damit man
trotzdem einfach die Map wechseln kann, stimmen die Spieler ab: die Mehrheit
der verbundenen Menschen entscheidet, allein reicht der eigene Wunsch.

Maps aus dem Trainings-Pool plus die Workshop-Maps (aim_multigame). Weg von
einer Workshop-Map werden vorher alle getrennt (map_change_commands). Nie
waehrend eines Trainings.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

VOTE_FILE = ".mapvote.json"
VOTE_SECONDS = 30
COMMANDS = {"map", "karte", "rtv", "ja", "yes"}


def _load(config_path: Path) -> dict:
    try:
        return json.loads((Path(config_path).parent / VOTE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(config_path: Path, vote: dict) -> None:
    (Path(config_path).parent / VOTE_FILE).write_text(json.dumps(vote), encoding="utf-8")


def handle(cfg: dict, config_path: Path, send, sid: str, name: str, cmd: str, arg: str,
           now: float | None = None) -> list[str]:
    """Antwortzeilen (werden als say geschickt); der Mapwechsel selbst geht direkt per RCON."""
    from .training_server import DEFAULT_MAPS, _current_map_from, _humans, is_active, map_change_commands, workshop_maps
    now = now or time.time()
    pool = list(cfg.get("training_maps") or DEFAULT_MAPS) + list(workshop_maps(cfg, config_path))
    short = ", ".join(m.removeprefix("de_") for m in pool)
    if is_active(config_path):
        return ["Waehrend eines Trainings wird die Map nicht gewechselt."]
    status = send("status")[0]
    humans = _humans(status)
    needed = len(humans) // 2 + 1
    vote = _load(config_path)
    if vote and now > vote.get("until", 0):
        vote = {}

    if cmd in ("map", "karte", "rtv"):
        target = arg.strip().lower().split()[0] if arg.strip() else ""
        if not target:
            return [f"Maps: {short}. Abstimmen mit !map <name>."]
        # "aim" -> aim_multigame, "nuke" -> de_nuke
        target = next((m for m in pool if m == target or m.removeprefix("de_") == target
                       or (not m.startswith("de_") and m.startswith(target))), f"de_{target}")
        if target not in pool:
            return [f"{target} gibt es hier nicht. Maps: {short}."]
        current = _current_map_from(status)
        if current == target:
            return [f"Wir sind schon auf {target}."]
        vote = {"map": target, "votes": [sid], "until": now + VOTE_SECONDS, "by": name}
    elif cmd in ("ja", "yes"):
        if not vote:
            return ["Gerade laeuft keine Abstimmung. Start: !map <name>."]
        if sid not in vote["votes"]:
            vote["votes"].append(sid)
    yes = len(vote["votes"])
    if yes >= needed:
        _save(config_path, {})
        # Kein Warten: der Log-Empfang laeuft synchron, CS2 schickt erst nach der Antwort weiter.
        send(f"say [Coach] Mehrheit ({yes}/{len(humans)}): Wechsel auf {vote['map']}.",
             *map_change_commands(status, vote["map"], cfg, config_path))
        return []
    _save(config_path, vote)
    return [f"{vote['by']} will auf {vote['map']} wechseln - !ja zum Zustimmen ({yes}/{needed}, "
            f"noch {int(vote['until'] - now)} s)."]
