"""Duelle je Callout ueber alle Matches (Idee: Leetifys "Map Zones").

Leetify zeigt Zonen je Match - ein Match hat aber nur 3-10 Kills und Tode
je Zone, das ist Rauschen. Hier wird ueber alle Exporte zusammengezaehlt:
je Map, Seite und Callout, an dem *du* standest, die Quote gewonnener
Duelle (Kill = gewonnen, Tod = verloren) gegen deine uebrigen Duelle auf
derselben Map und Seite.

Statistik ausschliesslich ueber evidence.compare_rates. Die auffaelligste
von k Zonen ist eine Auswahl aus k Kandidaten - deshalb comparisons=k
(Bonferroni), sonst findet sich auf jeder Map eine "Schwachstelle".

Callouts kommen aus dem Nav-Mesh der Map (`last_place_name`, Export-Felder
`p`/`ep`, Schema 5). Aeltere Exporte haben sie nicht und fallen heraus.
"""

from __future__ import annotations

from dataclasses import dataclass

from .evidence import SOLID_NEGATIVE, SOLID_POSITIVE, Comparison, compare_rates

MIN_DUELS = 15      # darunter wird eine Zone nicht verglichen


@dataclass
class Zone:
    map: str
    side: str
    place: str
    won: int
    duels: int
    rest_won: int
    rest_duels: int
    cmp: Comparison | None

    @property
    def rate(self) -> float:
        return round(100 * self.won / self.duels, 1) if self.duels else 0.0

    @property
    def rest_rate(self) -> float:
        return round(100 * self.rest_won / self.rest_duels, 1) if self.rest_duels else 0.0

    @property
    def solid(self) -> bool:
        return self.cmp is not None and self.cmp.verdict in (SOLID_POSITIVE, SOLID_NEGATIVE)


def _sides(data: dict) -> dict[int, str]:
    return {int(r.get("round", 0)): r.get("side", "") for r in data.get("round_timeline") or []}


def count(exports: list[dict]) -> dict[tuple[str, str, str], list[int]]:
    """(map, Seite, Callout) -> [gewonnen, Duelle]."""
    out: dict[tuple[str, str, str], list[int]] = {}
    for data in exports:
        m = ((data.get("match") or {}).get("map") or "").strip()
        side_of = _sides(data)
        for kp in data.get("kill_positions") or []:
            place = kp.get("p")
            side = side_of.get(int(kp.get("r", 0)), "")
            if not m or not place or side not in ("T", "CT") or kp.get("t") not in ("k", "d"):
                continue
            c = out.setdefault((m, side, place), [0, 0])
            c[0] += kp["t"] == "k"
            c[1] += 1
    return out


def analyse(exports: list[dict], min_duels: int = MIN_DUELS) -> list[Zone]:
    """Alle Zonen mit genug Duellen, je Map und Seite gegen den Rest verglichen.

    Sortiert: belastbare Abweichungen zuerst, darin die schwaechsten Zonen oben.
    """
    counts = count(exports)
    totals: dict[tuple[str, str], list[int]] = {}
    for (m, side, _), (w, n) in counts.items():
        t = totals.setdefault((m, side), [0, 0])
        t[0] += w
        t[1] += n
    tested: dict[tuple[str, str], int] = {}
    for (m, side, _), (_, n) in counts.items():
        if n >= min_duels:
            tested[(m, side)] = tested.get((m, side), 0) + 1

    zones = []
    for (m, side, place), (w, n) in counts.items():
        if n < min_duels:
            continue
        tw, tn = totals[(m, side)]
        rw, rn = tw - w, tn - n
        cmp = compare_rates(w, n, rw, rn, comparisons=tested[(m, side)]) if rn else None
        zones.append(Zone(m, side, place, w, n, rw, rn, cmp))
    zones.sort(key=lambda z: (not z.solid, z.rate - z.rest_rate))
    return zones
