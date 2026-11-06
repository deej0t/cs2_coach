"""Workshop-Maps per Chat auf den Server holen: !workshop <ID oder Link> [Name].

Der CS2-Server laedt eine Workshop-Map selbst herunter, sobald er sie mit
host_workshop_map <ID> starten soll. Dieser Befehl (nur Besitzer/Admins)
merkt sich die Map unter einem kurzen Namen in .workshop_maps.json neben der
Config - danach ist sie wie aim_multigame per !map <name> abstimmbar - und
wechselt sofort hin. Der Titel kommt von der Steam-API (ohne Schluessel).

Nicht in config.yaml: settings_save() schreibt die Config aus dem Speicher der
WebUI zurueck und wuerde neue Eintraege verlieren (wie bei .telegram_chat.json).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

FILE = ".workshop_maps.json"
COMMANDS = {"workshop", "ws"}
API = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"


def load(config_path: Path | None) -> dict[str, str]:
    if config_path is None:
        return {}
    try:
        return json.loads((Path(config_path).parent / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(config_path: Path, maps: dict[str, str]) -> None:
    (Path(config_path).parent / FILE).write_text(json.dumps(maps, indent=1), encoding="utf-8")


def parse_id(text: str) -> str | None:
    """ID aus '3070244462' oder einem Workshop-Link (...?id=3070244462)."""
    m = re.search(r"(?:id=)?(\d{6,12})", text)
    return m.group(1) if m else None


def slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return s[:24].strip("_") or "workshop"


def title_of(wid: str, post=None) -> tuple[str | None, int | None]:
    """(Titel, App-ID) von Steam - (None, None), wenn die API nichts liefert."""
    if post is None:
        import requests
        post = requests.post
    try:
        r = post(API, data={"itemcount": 1, "publishedfileids[0]": wid}, timeout=10)
        d = (r.json().get("response") or {}).get("publishedfiledetails") or [{}]
        return d[0].get("title"), d[0].get("consumer_app_id")
    except Exception:  # noqa: BLE001 - ohne Titel geht es mit eigenem Namen trotzdem
        return None, None


def handle(cfg: dict, config_path: Path, send, sid: str, name: str, arg: str, post=None) -> list[str]:
    from .training_server import _admins, is_active, map_change_commands, workshop_maps
    maps = workshop_maps(cfg, config_path)
    words = arg.split()
    if not words:
        return [f"Workshop-Maps: {', '.join(maps) or 'keine'}. Neu: !workshop <ID oder Link> [Name]. "
                "Wechseln: !map <name>."]
    if sid not in _admins(cfg):
        return ["Workshop-Maps hinzufuegen kann nur der Server-Besitzer. Abstimmen: !map <name>."]
    if words[0].lower() in ("weg", "loeschen", "entfernen", "remove") and len(words) > 1:
        own = load(config_path)
        if own.pop(words[1].lower(), None) is None:
            return [f"'{words[1]}' ist keine selbst hinzugefuegte Workshop-Map."]
        _save(config_path, own)
        return [f"Workshop-Map {words[1].lower()} entfernt."]
    wid = parse_id(words[0])
    if not wid:
        return ["Keine Workshop-ID erkannt. Beispiel: !workshop 3070244462 oder der Link aus dem Workshop."]
    title, app = title_of(wid, post)
    if app not in (None, 730):
        return [f"{wid} ist kein CS2-Workshop-Eintrag."]
    short = slug(words[1]) if len(words) > 1 else slug(title or f"ws_{wid}")
    own = load(config_path)
    own[short] = wid
    _save(config_path, own)
    if is_active(config_path):
        return [f"{title or wid} gespeichert als '{short}'. Waehrend des Trainings kein Mapwechsel - spaeter !map {short}."]
    status = send("status")[0]
    send(f"say [Coach] Lade Workshop-Map {title or wid} ('{short}') - der erste Download kann eine Weile dauern.",
         *map_change_commands(status, short, cfg, config_path))
    return []
