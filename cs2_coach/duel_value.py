"""Wo verlierst du mehr Rundengewinn als deine Lobby an derselben Stelle?

zones.py vergleicht deine Duelle in einer Zone mit deinen *uebrigen* Duellen
auf derselben Map und Seite - eine Zone, in der du immer schwach bist, kann
dort nie auffallen, wenn du ueberall gleich spielst. Hier ist die Referenz
die Lobby: alle anderen Spieler deiner Demos, die an genau diesem Callout
auf derselben Seite ein Duell hatten.

Ein Duell wird in Prozentpunkten Rundengewinn bewertet (winprob.swings):
gewonnen = +der Preis des Gegners, verloren = -dein Preis. Damit zaehlt ein
verlorenes 5v5-Opening mehr als ein verlorenes 1v4, und das Rundenergebnis
spielt keine Rolle. Der Preis kommt fuer jedes Match aus einer Tabelle, die
dieses Match nicht kennt (dieselben Gruppen wie winprob.calibration).

Statistik ausschliesslich ueber evidence.py; die auffaelligste von k Zonen
ist eine Auswahl aus k Kandidaten, also comparisons=k. Duelle eines Matches
haengen zusammen (derselbe Gegner, dieselbe Laune) - die Bonferroni-
Korrektur ueber alle Zonen ist deshalb keine Uebertreibung.

Vorsicht bei der Deutung: ein Entry stirbt vorne teurer als ein Anchor -
der Vergleich an derselben Stelle gleicht das nur teilweise aus.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import winprob as wp
from dataclasses import replace

from .evidence import SOLID_NEGATIVE, SOLID_POSITIVE, Comparison, compare_groups, compare_rates

FOLDS = 20
MIN_DUELS = 15     # eigene Duelle, darunter wird eine Zone nicht verglichen

#: Lage aus Sicht des Spielers: wie viele seines Teams gegen wie viele Gegner.
LEADS = ("Ueberzahl", "Gleichstand", "Unterzahl")


def _lead(own: int, enemy: int) -> str:
    return LEADS[0] if own > enemy else LEADS[1] if own == enemy else LEADS[2]


def priced_deaths(matches: list[dict], folds: int = FOLDS) -> list[dict]:
    """Alle Tode aller Demos mit Preis aus einer Tabelle ohne das eigene Match.

    Je Tod die Felder aus ``winprob.swings`` plus ``map``, ``demo``, ``round``.
    """
    seen = wp.observe(matches)
    demos = sorted(seen)
    folds = max(2, min(folds, len(demos))) if demos else 0
    fold_of = {d: i % folds for i, d in enumerate(demos)} if folds else {}
    tables = {f: wp.WinTable([r for d in demos if fold_of[d] != f for r in seen[d]]) for f in range(folds)}
    out = []
    for m in matches:
        if m["demo"] not in fold_of:
            continue
        table = tables[fold_of[m["demo"]]]
        for rnd in m["rounds"]:
            for s in wp.swings(rnd, table):
                out.append({**s, "map": rnd.get("map") or "", "demo": m["demo"], "round": rnd.get("n")})
    return out


def duels(deaths: list[dict]):
    """Jedes Duell zweimal, aus Sicht beider Spieler: (sid, map, seite, callout, wert, gewonnen, tod).

    Nur Tode durch einen Gegner - Selbstmord, Welt und Teamkill sind kein Duell.
    """
    for d in deaths:
        if not d["by"]:
            continue
        enemy_side = "T" if d["side"] == "CT" else "CT"
        yield d["sid"], d["map"], d["side"], d["place"], -d["cost"], False, d
        yield d["by"], d["map"], enemy_side, d["by_place"], d["cost"], True, d


@dataclass
class Group:
    """Deine Duelle in einer Gruppe gegen die der Lobby in derselben Gruppe."""

    key: tuple
    mine: list[float]
    lobby: list[float]
    won: int
    lobby_won: int
    value: Comparison | None      # Wert pro Duell, positiv = du besser
    rate: Comparison | None       # Anteil gewonnener Duelle

    @property
    def n(self) -> int:
        return len(self.mine)

    @property
    def mean(self) -> float:
        return round(sum(self.mine) / len(self.mine), 1) if self.mine else 0.0

    @property
    def lobby_mean(self) -> float:
        return round(sum(self.lobby) / len(self.lobby), 1) if self.lobby else 0.0

    @property
    def solid(self) -> bool:
        return self.value is not None and self.value.verdict in (SOLID_POSITIVE, SOLID_NEGATIVE)

    @property
    def weaker(self) -> bool:
        return self.value is not None and self.value.verdict == SOLID_NEGATIVE


def _compare(groups: dict[tuple, dict], min_duels: int) -> list[Group]:
    tested = [k for k, g in groups.items() if len(g["mine"]) >= min_duels and len(g["lobby"]) >= min_duels]
    out = []
    for k in tested:
        g = groups[k]
        value = compare_groups(g["mine"], g["lobby"], comparisons=len(tested))
        rate = compare_rates(g["won"], len(g["mine"]), g["lobby_won"], len(g["lobby"]), comparisons=len(tested))
        out.append(Group(k, g["mine"], g["lobby"], g["won"], g["lobby_won"], value, rate))
    out.sort(key=lambda g: (not g.solid, g.value.effect if g.value else 0.0))
    return out


def _add(groups: dict, key: tuple, mine: bool, value: float, won: bool) -> None:
    g = groups.setdefault(key, {"mine": [], "lobby": [], "won": 0, "lobby_won": 0})
    g["mine" if mine else "lobby"].append(value)
    g["won" if mine else "lobby_won"] += won


def by_zone(deaths: list[dict], me: str, min_duels: int = MIN_DUELS) -> list[Group]:
    """Je (Map, Seite, Callout an dem du standest): deine Duelle gegen die der Lobby dort."""
    groups: dict[tuple, dict] = {}
    for sid, m, side, place, value, won, _ in duels(deaths):
        if m and place:
            _add(groups, (m, side, place), sid == me, value, won)
    return _compare(groups, min_duels)


def by_situation(deaths: list[dict], me: str, min_duels: int = MIN_DUELS) -> list[Group]:
    """Je (Seite, Lage vor dem Duell, Bombe liegt): deine Duelle gegen die der Lobby.

    Die Lage ist aus Sicht des jeweiligen Spielers - wer im 4v5 einen Kill
    holt, war in Unterzahl.
    """
    groups: dict[tuple, dict] = {}
    for sid, _, side, _, value, won, d in duels(deaths):
        own, enemy = (d["own"], d["enemy"]) if not won else (d["enemy"], d["own"])
        _add(groups, (side, _lead(own, enemy), d["planted"]), sid == me, value, won)
    return _compare(groups, min_duels)


# --- Gewohnheiten: wie du stirbst, gegen die Lobby ------------------------------

#: Weiter weg als das vom naechsten lebenden Mitspieler = allein (death_review: Median 619).
ALONE_UNITS = 1000
EARLY_SECONDS = 25

#: Schluessel -> (Name, Bedingung je Tod). Als letzter Mann ist jeder "allein" - das
#: zaehlt nicht, sonst saehe aus wie eine Schwaeche, wer oft als Letzter uebrig bleibt.
HABITS = {
    "alone": ("allein gestorben (Mitspieler lebt, aber > 1000 Units weg)",
              lambda d: d["own"] >= 2 and d.get("mate_dist") is not None and d["mate_dist"] > ALONE_UNITS),
    "early": ("früh gestorben (vor Sekunde 25)", lambda d: d["second"] < EARLY_SECONDS and not d["planted"]),
    "opening": ("als Erster der Runde gestorben", lambda d: d["opening"]),
    "unused": ("mit ungenutzter Granate gestorben (keine geworfen, aber eine dabei)",
               lambda d: d.get("thrown") == 0 and (d.get("held") or 0) >= 1),
}


def _flip(c: Comparison | None) -> Comparison | None:
    """Bei Gewohnheiten ist "oefter" schlechter: Effekt so drehen, dass positiv = du besser."""
    if c is None:
        return None
    verdict = {SOLID_POSITIVE: SOLID_NEGATIVE, SOLID_NEGATIVE: SOLID_POSITIVE}.get(c.verdict, c.verdict)
    return replace(c, effect=-c.effect, ci_low=-c.ci_high, ci_high=-c.ci_low, verdict=verdict)


@dataclass
class Habit:
    """Wie oft dir eine Todesart pro Runde passiert, gegen die Lobby auf derselben Seite."""

    key: str
    label: str
    side: str
    k: int                      # deine Tode dieser Art
    n: int                      # deine Runden auf dieser Seite
    lobby_k: int
    lobby_n: int
    cost: float                 # Punkte Siegchance pro Runde durch diese Tode, du
    lobby_cost: float
    rate: Comparison | None     # positiv = dir passiert es seltener = du besser

    @property
    def solid(self) -> bool:
        return self.rate is not None and self.rate.verdict in (SOLID_POSITIVE, SOLID_NEGATIVE)


def _side_rounds(matches: list[dict]) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    for m in matches:
        for rnd in m["rounds"]:
            for side, ids in (("CT", rnd["ct"]), ("T", rnd["t"])):
                for sid in ids:
                    out[(sid, side)] = out.get((sid, side), 0) + 1
    return out


def by_habit(deaths: list[dict], matches: list[dict], me: str) -> list[Habit]:
    """Je Seite und Gewohnheit: Anteil deiner Runden mit so einem Tod gegen den der Lobby.

    Die Lobby sind alle anderen Spieler derselben Demos - Mitspieler und Gegner,
    also im Mittel gleich oft auf der Sieger- wie auf der Verliererseite.
    Rolle bleibt eine Stoerquelle: ein Entry stirbt oefter als Erster.
    Bonferroni ueber alle Seite-Gewohnheit-Paare.
    """
    rounds = _side_rounds(matches)
    pairs = [(side, key) for side in ("CT", "T") for key in HABITS]
    counts = {(side, key): [0, 0.0, 0, 0.0] for side, key in pairs}   # k, cost du; k, cost Lobby
    for d in deaths:
        for key, (_, test) in HABITS.items():
            c = counts.get((d["side"], key))
            if c is None or not test(d):
                continue
            i = 0 if d["sid"] == me else 2
            c[i] += 1
            c[i + 1] += d["cost"]
    out = []
    for side, key in pairs:
        n = rounds.get((me, side), 0)
        lobby_n = sum(v for (sid, s), v in rounds.items() if s == side and sid != me)
        k, cost, lk, lcost = counts[(side, key)]
        rate = _flip(compare_rates(k, n, lk, lobby_n, comparisons=len(pairs))) if n and lobby_n else None
        out.append(Habit(f"{side}_{key}", HABITS[key][0], side, k, n, lk, lobby_n,
                         round(cost / n, 2) if n else 0.0, round(lcost / lobby_n, 2) if lobby_n else 0.0, rate))
    out.sort(key=lambda h: (not h.solid, h.rate.effect if h.rate else 0.0))
    return out


def per_match(deaths: list[dict], matches: list[dict], me: str) -> dict[str, list[float]]:
    """Je Match deine Werte und der Mittelwert der anderen neun, pro gespielter Runde.

    ``gain``/``cost``: Punkte durch Kills bzw. eigene Tode, ``net`` = gain - cost.
    Schluessel ``<wert>_me`` und ``<wert>_lobby``, je eine Zahl pro Match mit dir.
    """
    rounds: dict[tuple, int] = {}
    for m in matches:
        for rnd in m["rounds"]:
            for sid in rnd["ct"] + rnd["t"]:
                rounds[(m["demo"], sid)] = rounds.get((m["demo"], sid), 0) + 1
    totals: dict[tuple, list[float]] = {}
    for d in deaths:
        totals.setdefault((d["demo"], d["sid"]), [0.0, 0.0])[1] += d["cost"]
        if d["by"]:
            totals.setdefault((d["demo"], d["by"]), [0.0, 0.0])[0] += d["cost"]
    out: dict[str, list[float]] = {f"{k}_{w}": [] for k in ("gain", "cost", "net") for w in ("me", "lobby")}
    for m in matches:
        players = {sid: n for (demo, sid), n in rounds.items() if demo == m["demo"] and n >= 8}
        if me not in players or len(players) < 2:
            continue
        vals = {sid: [x / n for x in totals.get((m["demo"], sid), [0.0, 0.0])] for sid, n in players.items()}
        others = [v for sid, v in vals.items() if sid != me]
        for i, k in enumerate(("gain", "cost")):
            out[f"{k}_me"].append(vals[me][i])
            out[f"{k}_lobby"].append(sum(v[i] for v in others) / len(others))
        out["net_me"].append(vals[me][0] - vals[me][1])
        out["net_lobby"].append(sum(v[0] - v[1] for v in others) / len(others))
    return out
