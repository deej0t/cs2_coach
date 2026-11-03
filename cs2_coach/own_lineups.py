"""Eigene Granaten-Lineups: im Spiel aufnehmen, speichern, ueben.

Wurf werfen, landet er gut: `!speichern <Name>`. Das Plugin (1.20.0) merkt sich
beim Loslassen Stand, Blick und Wurfart, bei der Detonation die Landung; der
Coach holt das mit `coach_nade_last <uid>` ab. Gespeichert wird nur ein Wurf,
der gelandet ist. `!lineups` listet sie, `!loeschen <Name>` entfernt einen,
`!granaten <map> meine` uebt sie mit dem Granaten-Trainer. Die Seite /lineups
zeigt sie auf der Radarkarte.

Gleiches Format wie grenade_lineups.library(), damit der Trainer beides kann.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

FILE = "lineups_own.json"
COMMANDS = {"speichern", "save", "lineups", "loeschen", "delete"}
MAX_PER_MAP = 40


def load(config_path: Path) -> dict[str, list[dict]]:
    try:
        return json.loads((Path(config_path).parent / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(config_path: Path, data: dict) -> None:
    (Path(config_path).parent / FILE).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def parse_last(reply: str) -> dict | None:
    """'coach_nade_last smoke x y z pitch yaw lx ly lz strength jump duck speed team' -> Lineup."""
    p = reply.split()
    if len(p) < 15 or p[0] != "coach_nade_last":
        return None
    try:
        v = [float(x) for x in p[2:15]]
    except ValueError:
        return None
    strength, jump, duck, speed = v[8], v[9] > 0, v[10] > 0, v[11]
    click = "left" if strength >= 0.75 else "both" if strength >= 0.25 else "right"
    return {"type": p[1], "side": "CT" if int(v[12]) == 3 else "T", "pos": [round(c, 1) for c in v[0:3]],
            "ang": [round(v[3], 2), round(v[4], 2)], "land": [round(c, 1) for c in v[5:8]],
            "style": {"click": click, "jump": jump, "duck": duck, "air": jump, "run": speed > 30 and not jump},
            "path": [], "count": 1, "mine": 1, "won": None}


def _name(arg: str) -> str:
    return re.sub(r"[^\w .\-]", "", arg, flags=re.UNICODE).strip()[:30]


def handle(config_path: Path, send, name: str, cmd: str, arg: str, uid: str | None) -> list[str]:
    from .training_server import _current_map_from
    from .grenade_lineups import LABELS
    bsp = _current_map_from(send("status")[0])
    if not bsp:
        return ["Map unbekannt."]
    data = load(config_path)
    mine = data.get(bsp, [])
    if cmd == "lineups":
        if not mine:
            return [f"Noch keine eigenen Lineups auf {bsp}. Wurf werfen, dann !speichern <Name>."]
        return [f"{bsp}: " + ", ".join(f"{g['name']} ({LABELS[g['type']]})" for g in mine)
                + ". Ueben: !granaten meine"]
    label = _name(arg)
    if cmd in ("loeschen", "delete"):
        keep = [g for g in mine if g["name"].lower() != label.lower()]
        if len(keep) == len(mine):
            return [f"Kein Lineup '{label}' auf {bsp}."]
        data[bsp] = keep
        _save(config_path, data)
        return [f"Lineup '{label}' geloescht."]
    if uid is None:
        return ["Spieler nicht gefunden."]
    lineup = parse_last(send(f"coach_nade_last {uid}")[0].strip())
    if lineup is None:
        return ["Kein gelandeter Wurf gefunden - erst werfen, landen lassen, dann !speichern <Name>."]
    label = label or f"{LABELS[lineup['type']]} {len(mine) + 1}"
    mine = [g for g in mine if g["name"].lower() != label.lower()]
    if len(mine) >= MAX_PER_MAP:
        return [f"Schon {MAX_PER_MAP} Lineups auf {bsp} - erst eins mit !loeschen <Name> entfernen."]
    mine.append({**lineup, "name": label, "by": name, "map": bsp})
    data[bsp] = mine
    _save(config_path, data)
    return [f"Gespeichert: {label} ({LABELS[lineup['type']]}). Ueben: !granaten meine"]
