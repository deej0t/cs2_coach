"""Deine teuersten Tode: was hat ein einzelner Tod an Siegchance gekostet?

Die Hebel-Suche findet (noch) keine Gewohnheit, die dich belastbar Runden
kostet. Was das Siegwahrscheinlichkeits-Modell trotzdem kann: jeden einzelnen
Tod bepreisen. Das ist kein Ratschlag aus einem Mittelwert, sondern eine
Tatsache ueber genau diesen Moment ("Runde 7, 5 gegen 5 in Connector: 18
Punkte") - und damit die Szene, die sich in der Demo anzuschauen lohnt.

Gezaehlt werden nur Tode, bei denen noch ein Mitspieler lebte. Als letzter
Mann kostet jeder Tod fast die ganze restliche Chance (lokal: Median 6, aber
p90 50 Punkte), die Liste bestand sonst nur aus verlorenen 1v1-Clutches.

Der Preis kommt aus der Tabelle ohne das eigene Match (duel_value.priced_deaths).
Runde und Tick kommen aus dem Export (kill_positions ``t == "d"``): je Runde
stirbst du hoechstens einmal, die Runden beider Quellen stimmten lokal in
845 von 848 Faellen ueberein.
"""

from __future__ import annotations

import json
from pathlib import Path

TOP = 3
TICKRATE = 64
LEAD_SECONDS = 5      # demo_gototick so weit vor dem Tod - man soll sehen, wie es dazu kam


def priced_own_deaths(cfg: dict, config_path: Path, me: str, prefer: list[str] | None = None,
                      scan_new: int = 0) -> dict[tuple[str, int], dict]:
    """(Demo ohne .dem, Runde) -> dein Tod mit Preis. ``prefer``: diese Demos zuerst einlesen."""
    from .duel_value import priced_deaths
    from .winprob import collect
    if not me:
        return {}
    matches = collect(cfg, config_path, log=lambda *_: None, max_new=scan_new, prefer=prefer)
    if len(matches) < 2:
        return {}
    return {(d["demo"], d["round"]): d for d in priced_deaths(matches) if d["sid"] == me}


def _demo_stem(data: dict) -> str:
    return Path((data.get("match") or {}).get("demo_file") or "").stem


def for_match(data: dict, own: dict[tuple[str, int], dict], top: int = TOP) -> list[dict]:
    """Die teuersten Tode eines Matches, bei denen noch ein Mitspieler lebte - teuerste zuerst.

    Je Tod: ``round``, ``side``, ``own``/``enemy`` (Lebende davor, deine Sicht),
    ``planted``, ``second``, ``cost`` und ``before`` (Siegchance deines Teams in
    Prozent), dazu aus dem Export ``tick``, ``place``, ``weapon``, ``opponent``.
    """
    stem = _demo_stem(data)
    if not stem:
        return []
    out = []
    for kp in data.get("kill_positions") or []:
        if kp.get("t") != "d":
            continue
        r = int(kp.get("r") or 0)
        d = own.get((stem, r))
        if d is None or d["own"] < 2:
            continue
        out.append({"round": r, "side": d["side"], "own": d["own"], "enemy": d["enemy"],
                    "planted": d["planted"], "second": d["second"], "cost": d["cost"], "before": d["before"],
                    "tick": int(kp.get("tk") or 0), "place": kp.get("p") or d.get("place") or "",
                    "weapon": kp.get("w") or "", "opponent": kp.get("e") or ""})
    out.sort(key=lambda m: -m["cost"])
    return out[:top]


def _where(m: dict) -> str:
    from .live_plan import pretty_place
    return f" in {pretty_place(m['place'])}" if m["place"] else ""


def describe(m: dict) -> str:
    """Ein Tod in Worten, alle Zahlen als Ziffern (die Nachbesprechung prueft sie)."""
    bomb = " (Bombe lag)" if m["planted"] else ""
    return (f"Runde {m['round']} als {m['side']}{_where(m)} im {m['own']} gegen {m['enemy']}{bomb} "
            f"nach {m['second']:g} s: {m['cost']:g} Punkte Siegchance (vorher {m['before']:g} %)")


def facts_line(moments: list[dict]) -> str:
    if not moments:
        return ""
    return ("TEUERSTE TODE (so viel Siegchance hat der Tod deinem Team gekostet; nur Tode, bei denen "
            "noch ein Mitspieler lebte): " + "; ".join(describe(m) for m in moments) + ".")


def goto_tick(m: dict) -> int | None:
    """Tick fuer ``demo_gototick``, ein paar Sekunden vor dem Tod."""
    if not m.get("tick"):
        return None
    return max(0, m["tick"] - LEAD_SECONDS * TICKRATE)


def notify_line(moments: list[dict]) -> str:
    """Eine Zeile fuers Handy: der teuerste Tod und wo er in der Demo liegt."""
    if not moments:
        return ""
    m = moments[0]
    tick = goto_tick(m)
    jump = f" - in der Demo `demo_gototick {tick}`" if tick is not None else ""
    return f"Teuerster Tod: {describe(m)}{jump}."


def scenario_costs(cfg: dict, config_path: Path, exports_dir: Path, me: str) -> dict[tuple[str, int], float]:
    """(Export-Datei, Runde) -> Preis deines Todes, fuer die Reihenfolge im Server-Training.

    Tode als letzter Mann zaehlen 0 - sie kommen nach den uebrigen, nicht weg.
    """
    own = priced_own_deaths(cfg, config_path, me)
    if not own:
        return {}
    out = {}
    for f in Path(exports_dir).glob("*_coach.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stem = _demo_stem(data)
        for (demo, r), d in own.items():
            if demo == stem:
                out[(f.name, r)] = d["cost"] if d["own"] >= 2 else 0.0
    return out
