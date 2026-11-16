"""Wie wahrscheinlich gewinnt CT die Runde - aus dem Spielzustand.

Die Hebel-Suche vergleicht Siege mit Niederlagen und findet auf Match-Ebene
fast nichts mehr: das Ergebnis faerbt jede Kennzahl, und was du in jedem
Match gleich machst, kann sich so nie abheben. Dieses Modul bewertet statt
des Matches den Zustand einer Runde:

    (CT am Leben, T am Leben, Bombe gelegt, Zeitabschnitt) -> P(CT gewinnt)

dazu der Ausruestungsvorteil bei Freeze-Ende als Verschiebung: ohne ihn
stimmt die Tabelle nur im Mittel (CT mit 2000 $ weniger pro Kopf: 38 %
vorhergesagt, 25 % beobachtet) und jeder Tod in einer Eco-Runde saehe
teurer aus, als er ist.

Damit laesst sich ein einzelnes Ereignis bewerten (dein Tod im 5v5 kostet
mehr als im 2v5), unabhaengig davon, wie die Runde am Ende ausging.

Gelernt wird aus allen zehn Spielern aller Demos im Demo-Ordner; je Demo
zwischengespeichert in winprob_cache/ neben der Config (wie death_cache/).

Das Modul rechnet nur die Tabelle und prueft, ob sie stimmt (``calibration``,
jedes Match wird mit einer Tabelle ohne dieses Match vorhergesagt). Es gibt
keine Ratschlaege - Vergleiche darauf laufen ueber evidence.py.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

TICKRATE = 64
CACHE_DIR = "winprob_cache"
CACHE_KEY = "rounds-v1"

#: Grenzen der Zeitabschnitte in Sekunden. Vor der Bombe ab Freeze-Ende
#: (Rundenzeit 115 s), nach der Bombe ab dem Legen (Zuender 40 s).
PRE_PLANT_EDGES = (35, 75, 100)
POST_PLANT_EDGES = (15, 30)

#: Gewicht der uebergeordneten Zelle in Runden: eine Zelle mit wenigen
#: Runden wird zu ihrer Zelle ohne Zeitabschnitt hingezogen.
PRIOR_ROUNDS = 10

#: Ausruestungsvorteil pro Kopf wird bei +-4000 $ gekappt.
EQUIP_CAP = 4.0

Cell = tuple[int, int, int, int]  # ct, t, planted, time bucket


def _event(p, name: str, columns: list[str]):
    import pandas as pd
    try:
        df = p.parse_event(name)
    except Exception:  # noqa: BLE001 - Event kommt in dieser Demo nicht vor
        return pd.DataFrame(columns=columns)
    return df if hasattr(df, "iterrows") else pd.DataFrame(columns=columns)


def _is_human(sid: str) -> bool:
    return sid.startswith("7656") and len(sid) == 17


def scan_demo(demo_path, parser_factory=None) -> list[dict]:
    """Alle Runden einer Demo mit Startaufstellung und Ereignissen.

    Je Runde: ``winner`` (CT/T), ``ct``/``t`` (Steam-IDs am Leben bei
    Freeze-Ende), ``ct_equip``/``t_equip`` (Summe Ausruestungswert) und
    ``events`` als ``[sekunde, art, steamid, taeter]`` mit art ``d`` (Tod),
    ``p`` (Bombe gelegt). Runden ohne 4-5 Menschen je Seite fallen weg
    (Aufwaermen, Bots, Wingman).
    """
    if parser_factory is None:
        from demoparser2 import DemoParser as parser_factory
    p = parser_factory(str(demo_path))
    freeze = sorted(int(t) for t in _event(p, "round_freeze_end", ["tick"])["tick"])
    ends_df = _event(p, "round_end", ["tick", "winner"])
    ends = sorted((int(r["tick"]), str(r["winner"]).upper()) for _, r in ends_df.iterrows())
    if not freeze or not ends:
        return []
    deaths = _event(p, "player_death", ["tick", "user_steamid", "attacker_steamid"])
    deaths = sorted((int(r["tick"]), str(r["user_steamid"]), str(r.get("attacker_steamid") or ""))
                    for _, r in deaths.iterrows())
    plants = sorted(int(t) for t in _event(p, "bomb_planted", ["tick"])["tick"])

    # Je Rundenende das letzte Freeze-Ende davor (und nach dem vorigen Ende)
    spans, prev_end = [], -1
    for end, winner in ends:
        start = max((f for f in freeze if prev_end < f < end), default=None)
        if start is not None and winner in ("CT", "T"):
            spans.append((start, end, winner))
        prev_end = end
    if not spans:
        return []

    rows = p.parse_ticks(["team_num", "is_alive", "current_equip_value"], ticks=[s for s, _, _ in spans])
    at_start: dict[int, list] = {}
    for tick, sid, team, alive, equip in zip(rows["tick"], rows["steamid"], rows["team_num"],
                                             rows["is_alive"], rows["current_equip_value"]):
        at_start.setdefault(int(tick), []).append((str(sid), team, alive, equip))

    out = []
    for n, (start, end, winner) in enumerate(spans, 1):
        sides: dict[str, list[str]] = {"CT": [], "T": []}
        equip = {"CT": 0, "T": 0}
        bots = False
        for sid, team, alive, value in at_start.get(start, []):
            side = {3: "CT", 2: "T"}.get(int(team) if team == team and team is not None else 0)
            if side is None or not bool(alive):
                continue
            bots = bots or not _is_human(sid)
            sides[side].append(sid)
            equip[side] += int(value) if value == value and value is not None else 0
        if bots or not all(4 <= len(s) <= 5 for s in sides.values()):
            continue
        events = [[round((t - start) / TICKRATE, 1), "d", v, k] for t, v, k in deaths if start < t <= end]
        events += [[round((t - start) / TICKRATE, 1), "p", "", ""] for t in plants if start < t <= end]
        events.sort(key=lambda e: (e[0], e[1] != "p"))
        out.append({"n": n, "winner": winner, "seconds": round((end - start) / TICKRATE, 1),
                    "ct": sides["CT"], "t": sides["T"], "ct_equip": equip["CT"], "t_equip": equip["T"],
                    "events": events})
    return out


def collect(cfg: dict, config_path: Path, parser_factory=None, log=print, max_new: int = 500) -> list[dict]:
    """Alle Demos im Demo-Ordner: ``[{"demo": stem, "rounds": [...]}]``, je Demo zwischengespeichert."""
    folder = (cfg.get("demo_folder") or "").strip()
    if not folder or not Path(folder).is_dir():
        return []
    cache_dir = Path(config_path).parent / CACHE_DIR
    out, scanned = [], 0
    for demo in sorted(Path(folder).glob("*.dem")):
        cache = cache_dir / f"{demo.stem}.json"
        try:
            known = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            known = {}
        if CACHE_KEY not in known:
            if scanned >= max_new:
                continue
            scanned += 1
            try:
                known[CACHE_KEY] = scan_demo(demo, parser_factory=parser_factory)
            except Exception as e:  # noqa: BLE001 - kaputte Demo: naechste
                log(f"[winprob] {demo.name}: {e}")
                continue
            cache_dir.mkdir(exist_ok=True)
            cache.write_text(json.dumps(known), encoding="utf-8")
        if known[CACHE_KEY]:
            out.append({"demo": demo.stem, "rounds": known[CACHE_KEY]})
    return out


# --- Zustaende einer Runde -------------------------------------------------

def _bucket(seconds: float, edges: tuple) -> int:
    return sum(1 for e in edges if seconds >= e)


def cell_at(ct: int, t: int, planted: bool, seconds: float) -> Cell:
    """Zelle fuer einen Zustand; ``seconds`` ab Freeze-Ende bzw. ab dem Legen der Bombe."""
    return (ct, t, int(planted), _bucket(seconds, POST_PLANT_EDGES if planted else PRE_PLANT_EDGES))


def is_live(ct: int, t: int, planted: bool) -> bool:
    """Ist die Runde in diesem Zustand noch offen?"""
    return ct > 0 and (t > 0 or planted)


def timeline(rnd: dict) -> list[dict]:
    """Zustandsfolge einer Runde: Start, dann der Zustand vor und nach jedem Ereignis.

    Jeder Eintrag: ``second``, ``kind`` (``start``/``d``/``p``), ``sid``/``by``,
    ``side`` des Opfers, ``before``/``after`` als (ct, t, planted, sekunde im
    jeweiligen Zeitmass). Tode nach dem Rundenende der Zaehlung (Seite schon
    bei 0) werden uebersprungen.
    """
    ct, t = set(rnd["ct"]), set(rnd["t"])
    planted, plant_second = False, 0.0
    out = []
    for second, kind, sid, by in rnd["events"]:
        clock = second - plant_second if planted else second
        before = (len(ct), len(t), planted, clock)
        if not is_live(*before[:3]):
            break
        side = None
        if kind == "p":
            if planted:
                continue
            planted, plant_second = True, second
        elif sid in ct:
            ct.discard(sid)
            side = "CT"
        elif sid in t:
            t.discard(sid)
            side = "T"
        else:
            continue  # nachgeruckter oder unbekannter Spieler
        after = (len(ct), len(t), planted, second - plant_second if planted else second)
        out.append({"second": second, "kind": kind, "sid": sid, "by": by, "side": side,
                    "before": before, "after": after})
    return out


def visited_cells(rnd: dict) -> set[Cell]:
    """Alle offenen Zellen, die die Runde durchlaufen hat (jede hoechstens einmal)."""
    cells: set[Cell] = set()

    def span(ct, t, planted, a, b):
        if not is_live(ct, t, planted) or b < a:
            return
        edges = POST_PLANT_EDGES if planted else PRE_PLANT_EDGES
        for k in range(_bucket(a, edges), _bucket(b, edges) + 1):
            cells.add((ct, t, int(planted), k))

    state = (len(rnd["ct"]), len(rnd["t"]), False)
    since, offset = 0.0, 0.0
    for ev in timeline(rnd):
        span(*state, since, ev["before"][3])
        if ev["kind"] == "p":
            offset = ev["second"]
        state, since = ev["after"][:3], ev["after"][3]
    span(*state, since, max(since, rnd.get("seconds", since) - offset))
    return cells


# --- Tabelle ----------------------------------------------------------------

def equip_edge(rnd: dict) -> float:
    """Ausruestungsvorteil der CT pro Kopf bei Freeze-Ende, in 1000 $, gekappt."""
    ct = rnd.get("ct_equip", 0) / max(len(rnd["ct"]), 1)
    t = rnd.get("t_equip", 0) / max(len(rnd["t"]), 1)
    return max(-EQUIP_CAP, min(EQUIP_CAP, (ct - t) / 1000))


def observe(matches: list[dict]) -> dict[str, list[tuple]]:
    """Je Demo die Runden als ``(ausruestungsvorteil, ct_gewinnt, zellen)``."""
    return {m["demo"]: [(equip_edge(r), int(r["winner"] == "CT"), sorted(visited_cells(r)))
                        for r in m["rounds"]] for m in matches}


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


class WinTable:
    """P(CT gewinnt) je Zelle, duenn besetzte Zellen zur Zelle ohne Zeit hingezogen.

    ``rounds`` wie aus ``observe`` (alle Demos aneinandergehaengt). Der
    Ausruestungsvorteil verschiebt die Tabelle auf der Logit-Skala um
    ``equip_slope * (vorteil - equip_neutral) * lebende/10``: je Spielerzahl
    einzeln angepasst faellt die Steigung fast linear mit den Lebenden (0,36
    bei zehn, 0,15 bei vier), also genuegt eine Zahl. ``equip_neutral`` ist
    der Vorteil, bei dem die Tabelle unveraendert gilt - sie enthaelt die
    mittlere Ausruestung schon, und die liegt nicht bei null (ohne den
    Nullpunkt sagte das Modell CT 1,4 Punkte zu hoch vorher).
    """

    def __init__(self, rounds: list[tuple], prior_rounds: float = PRIOR_ROUNDS,
                 use_time: bool = True, use_equip: bool = True):
        self.prior_rounds = prior_rounds
        self.use_time = use_time
        self.cells: dict[Cell, list[int]] = {}
        self.coarse: dict[tuple, list[int]] = {}
        self.by_diff: dict[tuple, list[int]] = {}
        wins = 0
        for _, won, cells in rounds:
            wins += won
            keys = [(self.cells, set(cells)), (self.coarse, {c[:3] for c in cells}),
                    (self.by_diff, {(c[0] - c[1], c[2]) for c in cells})]
            for table, ks in keys:
                for k in ks:
                    acc = table.setdefault(k, [0, 0])
                    acc[0] += 1
                    acc[1] += won
        self.rounds = len(rounds)
        self.base = wins / len(rounds) if rounds else 0.5
        self.equip_slope, self.equip_neutral = self._fit_equip(rounds) if use_equip else (0.0, 0.0)

    def _shrunk(self, nw: list[int] | None, parent: float) -> float:
        n, w = nw or (0, 0)
        if n + self.prior_rounds <= 0:
            return parent
        return (w + self.prior_rounds * parent) / (n + self.prior_rounds)

    def _cell_p(self, ct: int, t: int, planted: int, bucket: int) -> float:
        p = self._shrunk(self.by_diff.get((ct - t, planted)), self.base)
        p = self._shrunk(self.coarse.get((ct, t, planted)), p)
        if self.use_time:
            p = self._shrunk(self.cells.get((ct, t, planted, bucket)), p)
        return p

    def _fit_equip(self, rounds: list[tuple]) -> tuple[float, float]:
        """Steigung und Nullpunkt per Newton-Verfahren ueber alle Zellbesuche."""
        obs = [(_logit(self._cell_p(*c)), (c[0] + c[1]) / 10, x * (c[0] + c[1]) / 10, won)
               for x, won, cells in rounds for c in cells]
        a = b = 0.0  # shift = a * w + b * x * w
        for _ in range(25):
            ga = gb = haa = hab = hbb = 0.0
            for base, w, xw, won in obs:
                p = 1 / (1 + math.exp(-(base + a * w + b * xw)))
                r, v = won - p, p * (1 - p)
                ga += r * w
                gb += r * xw
                haa += v * w * w
                hab += v * w * xw
                hbb += v * xw * xw
            det = haa * hbb - hab * hab
            if det <= 1e-12:
                break
            da, db = (hbb * ga - hab * gb) / det, (haa * gb - hab * ga) / det
            a, b = a + da, b + db
            if abs(da) + abs(db) < 1e-6:
                break
        return (b, -a / b) if b else (0.0, 0.0)

    def p_cell(self, cell: Cell, equip: float | None = None) -> float:
        """``equip`` = Ausruestungsvorteil der CT (``equip_edge``); None = ohne Ausruestung."""
        p = self._cell_p(*cell)
        if equip is None or not self.equip_slope:
            return p
        shift = self.equip_slope * (equip - self.equip_neutral) * (cell[0] + cell[1]) / 10
        return 1 / (1 + math.exp(-(_logit(p) + shift)))

    def p_ct(self, ct: int, t: int, planted: bool, seconds: float = 0.0,
             equip: float | None = None) -> float:
        """Entschiedene Zustaende sind 0 oder 1, alle anderen kommen aus der Tabelle."""
        if ct <= 0:
            return 0.0
        if t <= 0 and not planted:
            return 1.0
        return self.p_cell(cell_at(ct, t, planted, seconds), equip)

    def p_side(self, side: str, ct: int, t: int, planted: bool, seconds: float = 0.0,
               equip: float | None = None) -> float:
        p = self.p_ct(ct, t, planted, seconds, equip)
        return p if side == "CT" else 1 - p


def fit(matches: list[dict], prior_rounds: float = PRIOR_ROUNDS) -> WinTable:
    return WinTable([r for rs in observe(matches).values() for r in rs], prior_rounds)


# --- Pruefung ---------------------------------------------------------------

#: Obergrenze (1000 $ pro Kopf) und Name je Ausruestungsklasse.
EQUIP_CLASSES = ((-2.0, "CT 2000 $ und mehr im Nachteil"), (-0.5, "CT 500-2000 $ im Nachteil"),
                 (0.5, "gleich (+-500 $)"), (2.0, "CT 500-2000 $ im Vorteil"),
                 (math.inf, "CT 2000 $ und mehr im Vorteil"))


def _equip_class(x: float) -> str:
    return next(label for upper, label in EQUIP_CLASSES if x <= upper)


def _score(pairs: list[tuple[float, int]]) -> dict:
    n = len(pairs)
    if not n:
        return {"n": 0}
    eps = 1e-6
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    logloss = -sum(math.log(min(max(p if y else 1 - p, eps), 1)) for p, y in pairs) / n
    return {"n": n, "brier": round(brier, 4), "logloss": round(logloss, 4)}


def _reliability(groups: list[tuple[str, list[tuple[float, int]]]]) -> tuple[list[dict], float | None]:
    rows = [{"label": label, "n": len(ps),
             "predicted": round(sum(p for p, _ in ps) / len(ps) * 100, 1),
             "observed": round(sum(y for _, y in ps) / len(ps) * 100, 1)} for label, ps in groups if ps]
    n = sum(r["n"] for r in rows)
    error = sum(abs(r["predicted"] - r["observed"]) * r["n"] for r in rows) / n if n else None
    return rows, round(error, 1) if error is not None else None


def calibration(matches: list[dict], bins: int = 10, prior_rounds: float = PRIOR_ROUNDS,
                folds: int = 20) -> dict:
    """Stimmt die Tabelle? Jedes Match wird mit einer Tabelle ohne dieses Match vorhergesagt.

    Die Demos werden dafuer auf ``folds`` Gruppen verteilt (ganze Matches,
    nie einzelne Runden - Runden eines Matches haengen zusammen). Einheit ist
    der Besuch einer Zelle durch eine Runde.

    ``models``: Brier und Log-Loss fuer ``base`` (immer die CT-Quote),
    ``alive`` (Spielerzahlen und Bombe), ``time`` (mit Zeitabschnitt) und
    ``equip`` (mit Ausruestung - das Modell, das ``fit`` liefert).
    ``bins``/``error``: Vorhersage gegen tatsaechliche CT-Quote, mittlere
    Abweichung in Prozentpunkten. ``by_equip``/``equip_error``: dasselbe je
    Ausruestungsklasse, einmal ohne (``*_without``) und einmal mit Ausruestung.
    """
    seen = observe(matches)
    demos = sorted(seen)
    folds = max(2, min(folds, len(demos))) if demos else 0
    pairs: dict[str, list[tuple[float, int]]] = {"base": [], "alive": [], "time": [], "equip": []}
    by_equip: dict[str, dict[str, list]] = {}
    slopes, neutrals = [], []
    for f in range(folds):
        train = [r for i, d in enumerate(demos) if i % folds != f for r in seen[d]]
        test = [r for i, d in enumerate(demos) if i % folds == f for r in seen[d]]
        full = WinTable(train, prior_rounds)
        alive = WinTable(train, prior_rounds, use_time=False, use_equip=False)
        slopes.append(full.equip_slope)
        neutrals.append(full.equip_neutral)
        for x, y, cells in test:
            label = _equip_class(x)
            for c in cells:
                with_equip, without = full.p_cell(c, x), full.p_cell(c)
                pairs["base"].append((full.base, y))
                pairs["alive"].append((alive.p_cell(c), y))
                pairs["time"].append((without, y))
                pairs["equip"].append((with_equip, y))
                slot = by_equip.setdefault(label, {"with": [], "without": []})
                slot["with"].append((with_equip, y))
                slot["without"].append((without, y))
    final = pairs["equip"]
    table, error = _reliability([
        (f"{b * 100 // bins}-{(b + 1) * 100 // bins} %",
         [(p, y) for p, y in final if b / bins <= p < (b + 1) / bins or (b == bins - 1 and p >= 1.0)])
        for b in range(bins)])
    order = [label for _, label in EQUIP_CLASSES]
    eq_with, eq_error = _reliability([(k, by_equip[k]["with"]) for k in order if k in by_equip])
    eq_without, eq_error_without = _reliability([(k, by_equip[k]["without"]) for k in order if k in by_equip])
    return {"matches": len(matches), "rounds": sum(len(v) for v in seen.values()),
            "models": {k: _score(v) for k, v in pairs.items()},
            "bins": table, "error": error,
            "by_equip": eq_with, "equip_error": eq_error,
            "by_equip_without": eq_without, "equip_error_without": eq_error_without,
            "equip_slope": round(sum(slopes) / len(slopes), 3) if slopes else None,
            "equip_neutral": round(sum(neutrals) / len(neutrals), 2) if neutrals else None}


# --- Bewertung einzelner Tode ----------------------------------------------

def swings(rnd: dict, table: WinTable) -> list[dict]:
    """Je Tod der Runde: wie viel Siegwahrscheinlichkeit er die Seite des Opfers kostet.

    ``cost`` in Prozentpunkten (positiv = hat gekostet), ``before`` die Lage
    der Seite davor in Prozent, dazu Spielerzahlen aus Sicht des Opfers.
    """
    equip = equip_edge(rnd)
    out = []
    for ev in timeline(rnd):
        if ev["kind"] != "d" or ev["side"] is None:
            continue
        side = ev["side"]
        pb = table.p_side(side, *ev["before"], equip=equip)
        pa = table.p_side(side, *ev["after"], equip=equip)
        ct, t, planted, _ = ev["before"]
        own, enemy = (ct, t) if side == "CT" else (t, ct)
        out.append({"sid": ev["sid"], "by": ev["by"], "side": side, "second": ev["second"],
                    "own": own, "enemy": enemy, "planted": bool(planted),
                    "before": round(pb * 100, 1), "cost": round((pb - pa) * 100, 1),
                    "won": rnd["winner"] == side})
    return out
