"""Hebel-Suche: was trennt deine Siege von deinen Niederlagen - neu gemessen bei jedem Sync.

Bis 13.11. wurden neue Hebel von Hand gesucht (Ad-hoc-Skripte unter /tmp:
Team-Utility, Zonen, Leetify-Kennzahlen, Trades), und die Notiz "mit mehr
Matches wiederholen" las niemand wieder. Der Coach stand dadurch auf genau
einem Hebel (fruehe Granate im Full Buy) - ist der erreicht, hat er nichts
mehr beizubringen.

Hier laufen alle Kandidaten bei jedem Sync neu, mit den vorhandenen
Auswertungen (keine eigene Statistik, alles ueber evidence.py):

- Rundenebene: fruehe Granate im Full Buy (gewonnene Runden mit gegen ohne)
- Matchebene: alle Regeln aus findings (inkl. beobachteter wie first_sight)
  und die Granatentypen je Runde - ergebnisabhaengige bleiben markiert
- Todesebene: getradet, Opening-Tod, Mitspieler weit weg (death_review-Cache)
- Zonen: Duellquote je Map/Seite/Callout gegen den Rest der Map (Bonferroni)

Die Urteile werden gemerkt (.levers_state.json); wird ein Kandidat neu
belastbar (und ist nicht ergebnisabhaengig), geht eine Nachricht an
Discord/Telegram. Der erste Lauf legt nur die Ausgangslage fest.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .evidence import SOLID_NEGATIVE, SOLID_POSITIVE

STATE_FILE = ".levers_state.json"
RESULT_FILE = ".levers.json"
SOLID = (SOLID_POSITIVE, SOLID_NEGATIVE)
HISTORY = 100
# Granatenzahlen je Match steigen zum grossen Teil, weil man gewinnt: am 13.11. warf der
# *Gegner* in deinen Siegen 1,05 statt 1,44 pro Runde (d = -0,93). Sie bleiben sichtbar,
# gelten aber nicht als neuer Hebel - die saubere Groesse ist die fruehe Granate je Runde.
COUNT_KEYS = {"utility"}
COUNT_NOTE = "Matchebene - zum Teil Folge gewonnener Runden"


@dataclass
class Lever:
    key: str
    label: str
    level: str                 # Runde, Match, Tod, Zone
    unit: str
    mean_win: float            # Gruppe A (Siege bzw. "mit") - Name passt zu relevance_table.html
    mean_loss: float
    effect: float
    ci_low: float
    ci_high: float
    verdict: str
    matches_needed: int = 0
    outcome_driven: bool = False
    note: str = ""

    @property
    def solid(self) -> bool:
        return self.verdict in SOLID


def _from_cmp(key, label, level, unit, c, note="", outcome=False) -> Lever | None:
    if c is None:
        return None
    return Lever(key, label, level, unit, round(c.mean_a, 2), round(c.mean_b, 2), c.effect,
                 c.ci_low, c.ci_high, c.verdict, c.n_needed, outcome, note)


def _from_rel(r, level="Match", prefix="") -> Lever:
    return Lever(prefix + r.key, r.label, level, r.unit, r.mean_win, r.mean_loss, r.effect,
                 r.ci_low, r.ci_high, r.verdict, r.matches_needed, r.outcome_driven)


def candidates(exports: list[dict], deaths: list[dict] | None = None) -> tuple[list[Lever], dict]:
    """Alle Kandidaten neu messen. exports in Spielreihenfolge, deaths aus death_review."""
    from .findings import build_relevance
    from .utility_analysis import early_utility_rounds, throw_relevance
    from .zones import analyse

    out: list[Lever] = []
    lv = _from_cmp("early_util_round", "Frühe Granate im Full Buy (gewonnene Runden mit gegen ohne)",
                   "Runde", " %", early_utility_rounds(exports),
                   note="Full-Buy-Runden, die du 35 s überlebt hast")
    if lv:
        out.append(lv)
    matches = [{"result": (e.get("match") or {}).get("result", ""), "player": e.get("player") or {}}
               for e in exports]
    for r in build_relevance(matches):
        lv = _from_rel(r)
        if r.key in COUNT_KEYS:
            lv.outcome_driven, lv.note = True, COUNT_NOTE
        out.append(lv)
    for r in throw_relevance(exports).get("rows", []):
        if r.key == "util_total":      # dasselbe wie "Utility pro Runde" aus findings
            continue
        lv = _from_rel(r)
        lv.label, lv.outcome_driven, lv.note = f"Granaten pro Runde: {r.label}", True, COUNT_NOTE
        out.append(lv)
    if deaths:
        from .death_review import summary
        s = summary(deaths)
        for key, label, cmp_key in (("traded", "Tod wurde getradet (Siege gegen Niederlagen)", "traded_cmp"),
                                    ("opening", "Opening-Tod (erster Tod der Runde)", "opening_cmp"),
                                    ("far", "Nächster Mitspieler > 1000 Units beim Tod", "far_cmp")):
            lv = _from_cmp("death_" + key, label, "Tod", " %", s.get(cmp_key))
            if lv:
                out.append(lv)
    zones = analyse(exports)
    from .live_plan import pretty_place
    zone_levers = [_from_cmp(f"zone_{z.map}_{z.side}_{z.place}",
                             f"{z.map} {z.side}: Duelle in {pretty_place(z.place)} gegen Rest der Map",
                             "Zone", " %", z.cmp, note=f"{z.won}/{z.duels} Duelle")
                   for z in zones if z.cmp is not None]
    zone_levers = [z for z in zone_levers if z]
    out += [z for z in zone_levers if z.solid]
    # Die fuenf Zonen mit dem groessten Unterschied - Kandidaten, noch keine Befunde
    zinfo = {"tested": len(zone_levers), "solid": sum(z.solid for z in zone_levers),
             "closest": [asdict(z) for z in sorted(zone_levers, key=lambda z: abs(z.effect), reverse=True)[:5]]}
    return out, zinfo


def _load_exports(cfg: dict) -> list[dict]:
    from .report_ai import _exports
    data = []
    for f in _exports(cfg):
        try:
            data.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return data


def run(cfg: dict, config_path: Path) -> dict:
    """Messung ueber alle Exporte (Todes-Daten nur aus dem Cache, nie neu gescannt)."""
    exports = _load_exports(cfg)
    try:
        from .death_review import collect
        deaths = collect(cfg, config_path, log=lambda *_: None, max_new=0)
    except Exception:  # noqa: BLE001 - ohne Todes-Daten fehlen nur diese Kandidaten
        deaths = []
    levers, zinfo = candidates(exports, deaths)
    return {"at": datetime.now().isoformat(timespec="minutes"), "matches": len(exports),
            "deaths": len(deaths), "levers": [asdict(x) for x in levers], "zones": zinfo}


def load(config_path: Path) -> dict:
    try:
        return json.loads((Path(config_path).parent / RESULT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def check(cfg: dict, config_path: Path, notify: bool = True, log=print) -> list[dict]:
    """Neu messen, Ergebnis ablegen, neu belastbare Hebel melden. Gibt die neuen zurueck."""
    base = Path(config_path).parent
    result = run(cfg, config_path)
    (base / RESULT_FILE).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    try:
        state = json.loads((base / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    first = "verdicts" not in state
    before = state.get("verdicts", {})
    new = [x for x in result["levers"] if x["verdict"] in SOLID and not x["outcome_driven"]
           and before.get(x["key"]) not in SOLID]
    lost = [k for k, v in before.items() if v in SOLID
            and next((x["verdict"] for x in result["levers"] if x["key"] == k), None) not in SOLID]
    state["verdicts"] = {x["key"]: x["verdict"] for x in result["levers"]}
    state.setdefault("history", []).append({"at": result["at"], "matches": result["matches"],
                                            "solid": [x["key"] for x in result["levers"] if x["verdict"] in SOLID]})
    state["history"] = state["history"][-HISTORY:]
    if new and not first and notify:
        from .report_ai import _send_text
        lines = ["**Hebel-Suche: neuer belastbarer Unterschied**"]
        for x in new:
            word = "in Siegen höher" if x["verdict"] == SOLID_POSITIVE else "in Siegen niedriger"
            lines.append(f"• {x['label']}: {x['mean_win']}{x['unit']} gegen {x['mean_loss']}{x['unit']} "
                         f"({word}, Effekt {x['effect']} [{x['ci_low']} … {x['ci_high']}], {result['matches']} Matches)")
        lines.append("Details: /hebel")
        _send_text(cfg, config_path, "\n".join(lines), log)
    (base / STATE_FILE).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    if lost:
        log(f"Hebel-Suche: nicht mehr belastbar: {', '.join(lost)}")
    return [] if first else new
