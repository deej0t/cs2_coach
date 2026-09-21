"""Trennt der Utility-Einsatz Siege von Niederlagen - und welcher?

Die Relevanzanalyse in findings.py bewertet "Utility pro Runde" als eine
Zahl. Seit der Export die Detonationsorte mitfuehrt, laesst sich die Frage
schaerfer stellen: nicht *wieviel*, sondern *was*. Eine Empfehlung
"wirf mehr Utility" ist kaum umsetzbar; "dir fehlen die Flashes" schon.

Gerechnet wird **pro Runde**, nicht pro Match. Ein 13:5 hat 18 Runden, ein
13:11 deren 24 - eine Auswertung je Match wuerde zu einem guten Teil die
Rundenzahl messen und nicht das Verhalten.

Zur Vorsicht bei der Deutung: ganz verhaltensrein ist auch die
Rundenquote nicht. Wer frueh stirbt, wirft weniger, und frueh zu sterben
haengt mit dem Rundenausgang zusammen. Der Zusammenhang ist also
schwaecher zirkulaer als bei Survival oder K/D, aber nicht frei davon.
Deshalb gilt hier dasselbe wie ueberall in diesem Projekt: die
Effektstaerke wird mit ihrem Vertrauensbereich berichtet und nie als
Tatsache.
"""

from __future__ import annotations

from .findings import (
    MIN_MATCHES_PER_OUTCOME,
    Relevance,
    UNDECIDED,
    _effect_ci,
    _matches_needed,
    _stdev,
    _verdict_for,
)

#: Kuerzel im Export -> Anzeigename. Siehe _compact_utility_positions().
GRENADE_TYPES = {
    "s": "Smokes",
    "f": "Flashes",
    "h": "HE",
    "m": "Molotovs",
}


def _rates(export: dict) -> dict[str, float] | None:
    """Wuerfe je Runde und Granatentyp fuer einen Export.

    None, wenn der Export die Wurfpositionen nicht kennt - aeltere Exporte
    haben das Feld nicht, und eine fehlende Angabe als "null Wuerfe" zu
    zaehlen wuerde die Auswertung verfaelschen.
    """
    if "utility_positions" not in export:
        return None
    rounds = (export.get("match") or {}).get("total_rounds") or 0
    if rounds <= 0:
        return None

    counts = dict.fromkeys(GRENADE_TYPES, 0)
    total = 0
    for pos in export["utility_positions"]:
        t = pos.get("t")
        if t in counts:
            counts[t] += 1
            total += 1

    rates = {k: v / rounds for k, v in counts.items()}
    rates["total"] = total / rounds
    return rates


def throw_relevance(exports: list[dict]) -> dict:
    """Effektstaerke je Granatentyp zwischen Siegen und Niederlagen.

    Erwartet die *rohen* Export-Dicts (mit match und utility_positions),
    nicht die abgeflachten Eintraege aus _get_exports().
    """
    wins: dict[str, list[float]] = {}
    losses: dict[str, list[float]] = {}
    used = skipped = 0

    for export in exports:
        result = (export.get("match") or {}).get("result", "")
        if result not in ("Sieg", "Niederlage"):
            continue
        rates = _rates(export)
        if rates is None:
            skipped += 1
            continue
        used += 1
        target = wins if result == "Sieg" else losses
        for key, value in rates.items():
            target.setdefault(key, []).append(value)

    out: list[Relevance] = []
    for key in list(GRENADE_TYPES) + ["total"]:
        w, l = wins.get(key, []), losses.get(key, [])
        if len(w) < MIN_MATCHES_PER_OUTCOME or len(l) < MIN_MATCHES_PER_OUTCOME:
            continue
        mw, ml = sum(w) / len(w), sum(l) / len(l)
        pooled = _stdev(w + l)
        effect = round((mw - ml) / pooled, 2) if pooled else 0.0
        lo, hi = _effect_ci(effect, len(w), len(l))
        verdict = _verdict_for(lo, hi)
        out.append(Relevance(
            key=f"util_{key}",
            label=GRENADE_TYPES.get(key, "Gesamt"),
            unit="/Runde",
            mean_win=round(mw, 2),
            mean_loss=round(ml, 2),
            effect=effect,
            # Nicht ergebnisgetrieben im Sinne von findings.py, aber siehe
            # den Vorbehalt im Modul-Docstring.
            outcome_driven=False,
            ci_low=lo, ci_high=hi, verdict=verdict,
            matches_needed=_matches_needed(effect) if verdict == UNDECIDED else 0,
        ))

    out.sort(key=lambda r: -abs(r.effect))
    return {
        "rows": out,
        "matches_used": used,
        "matches_skipped": skipped,
        "wins": len(wins.get("total", [])),
        "losses": len(losses.get("total", [])),
    }
