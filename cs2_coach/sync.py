"""Neue Demos von Valve holen, ohne Weboberflaeche.

Der Sync existierte nur als Route der WebUI (/api/auto-sync) und lief nur,
wenn jemand ihn anklickte. Fuer das taegliche Briefing muss er ohne Zutun
laufen - sonst fasst es jeden Tag dieselben Daten zusammen.

Dazu kommt: Valve haelt Download-Links nur kurz vor. Im September hatten
von 140 gelisteten Premier-Matches nur noch 3 einen Link. Wer nicht
regelmaessig synchronisiert, verliert Demos endgueltig.

Uebernommen wird nur der GCPD-Teil der Route, also der Download ueber die
gespeicherte Steam-Sitzung. Die Share-Code-Abfrage dort ist nur ein
Signal ("es gibt neue Matches") und ordnet keine Demo zu; die Auswertung
uebernimmt danach "batch --only-outdated".
"""

from __future__ import annotations

from pathlib import Path


def download_new_demos(cfg: dict, log=print) -> list[Path]:
    """Neue Demos per GCPD in den Demo-Ordner laden. Liefert die neuen Dateien."""
    from .sharecode import download_demo_bz2, fetch_gcpd_demo_urls, load_steam_session

    raw = (cfg.get("demo_folder") or "").strip()
    folder = Path(raw)
    # Rohwert pruefen: Path("") ist ".", also das Arbeitsverzeichnis.
    if not raw or not folder.is_dir():
        log(f"Demo-Ordner nicht gefunden: {folder}")
        return []
    session = load_steam_session()
    if session is None:
        log("Keine gueltige Steam-Anmeldung - kein Download moeglich.")
        return []

    demos = fetch_gcpd_demo_urls(session, str(cfg.get("steam_id") or ""), on_status=log)
    present = [p.name for p in folder.glob("*.dem")]
    new: list[Path] = []
    for d in demos:
        # Dieselbe Demo kann auch mit Endung "_274" aus dem Replays-Ordner
        # vorliegen - so entstanden die doppelten Dateien auf dem Array.
        prefix = f"match730_{str(d['match_id']).zfill(21)}"
        if any(name.startswith(prefix) for name in present):
            continue
        path = download_demo_bz2(d["url"], folder, match_date=d.get("date", ""), on_status=log)
        if path:
            new.append(path)
            present.append(path.name)
    log(f"{len(new)} neue Demos geladen, {len(demos)} bei Valve gelistet.")
    return new
