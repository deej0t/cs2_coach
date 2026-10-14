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

from .evidence import compare_groups
from .findings import Relevance

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
        c = compare_groups(wins.get(key, []), losses.get(key, []))
        if c is None:
            continue
        out.append(Relevance(
            key=f"util_{key}",
            label=GRENADE_TYPES.get(key, "Gesamt"),
            unit="/Runde",
            mean_win=c.mean_a,
            mean_loss=c.mean_b,
            effect=c.effect,
            # Nicht ergebnisgetrieben im Sinne von findings.py, aber siehe
            # den Vorbehalt im Modul-Docstring.
            outcome_driven=False,
            ci_low=c.ci_low, ci_high=c.ci_high, verdict=c.verdict,
            matches_needed=c.n_needed,
        ))

    out.sort(key=lambda r: -abs(r.effect))
    return {
        "rows": out,
        "matches_used": used,
        "matches_skipped": skipped,
        "wins": len(wins.get("total", [])),
        "losses": len(losses.get("total", [])),
    }


# ── Wirkung statt Menge (Schema 4) ──────────────────────────────────────

def _he_values(export: dict) -> dict[str, float] | None:
    """HE-Kennzahlen eines Matches; None, wenn der Export sie nicht kennt.

    Erst ab Schema 4 tragen Exporte Granatenschaden und Wurfzeitpunkt.
    Fehlt das, wird das Match uebersprungen statt als "null Schaden"
    gezaehlt - sonst saehe jedes alte Match wie lauter Fehlwuerfe aus.
    """
    ups = export.get("utility_positions") or []
    util = (export.get("player") or {}).get("utility") or {}
    rounds = (export.get("match") or {}).get("total_rounds") or 0
    if rounds <= 0 or "he_damage" not in util or not any("s" in u for u in ups):
        return None

    he = [u for u in ups if u.get("t") == "h"]
    out = {
        "he_dmg_round": util["he_damage"] / rounds,
        "fire_dmg_round": util.get("fire_damage", 0) / rounds,
    }
    if he:
        out["dmg_per_he"] = util["he_damage"] / len(he)
        times = sorted(u["s"] for u in he if u.get("s") is not None)
        if times:
            out["he_second"] = times[len(times) // 2]
    return out


#: Schluessel -> (Anzeigename, Einheit)
HE_METRICS = {
    "dmg_per_he": ("Schaden pro HE", " pro HE"),
    "he_dmg_round": ("HE-Schaden", "/Runde"),
    "fire_dmg_round": ("Feuerschaden", "/Runde"),
    "he_second": ("HE-Zeitpunkt", " s"),
}


def he_effectiveness(exports: list[dict]) -> dict:
    """Trifft die HE, und wann wird sie geworfen - Siege gegen Niederlagen?

    Die Anzahl der HE trennt Siege belastbar von Niederlagen (d=0.80). Das
    sagt aber nicht, ob der Rat "wirf mehr HE" oder "triff mit deinen HE"
    lauten muss. "Schaden pro HE" beantwortet das: ist er in Siegen
    hoeher, kommt es auf das Treffen an, nicht auf die Menge.

    Beim Zeitpunkt gibt es kein besser oder schlechter; ein positiver Wert
    heisst nur, dass in Siegen spaeter in der Runde geworfen wird.
    """
    wins: dict[str, list[float]] = {}
    losses: dict[str, list[float]] = {}
    used = skipped = 0
    for export in exports:
        result = (export.get("match") or {}).get("result", "")
        if result not in ("Sieg", "Niederlage"):
            continue
        values = _he_values(export)
        if values is None:
            skipped += 1
            continue
        used += 1
        target = wins if result == "Sieg" else losses
        for key, value in values.items():
            target.setdefault(key, []).append(value)

    rows: list[Relevance] = []
    for key, (label, unit) in HE_METRICS.items():
        c = compare_groups(wins.get(key, []), losses.get(key, []))
        if c is None:
            continue
        rows.append(Relevance(
            key=f"he_{key}", label=label, unit=unit,
            mean_win=c.mean_a, mean_loss=c.mean_b, effect=c.effect,
            outcome_driven=False, ci_low=c.ci_low, ci_high=c.ci_high,
            verdict=c.verdict, matches_needed=c.n_needed,
        ))
    return {"rows": rows, "matches_used": used, "matches_skipped": skipped}
