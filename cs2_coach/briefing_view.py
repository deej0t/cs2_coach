"""Darstellung des taeglichen Briefings.

Feste Struktur statt KI-Freitext: das erste KI-Briefing (2026-10-16)
wiederholte Uebungen je Ziel, nannte Raten wie "0.44/Runde", mit denen im
Spiel niemand etwas anfangen kann, und sah jeden Tag anders aus. Jetzt:

- Ziele in Einheiten, die man im Match zaehlen kann ("~9 HE pro Match").
- Ein Balken zeigt den Abstand zum Zielwert. Das ist eine Messung, kein
  Urteil - ob sich etwas belastbar veraendert hat, sagt allein der
  Fortschrittsteil, mit dem Urteil aus evidence.py.
- Uebungen nach Zeitpunkt gruppiert (vor dem ersten Match / im Match)
  und ohne Wiederholung.

Die KI steuert nur noch ein, zwei einleitende Saetze bei (briefing.py).
Ausgabe ist Discord-Markdown; telegram.to_html() setzt es fuer Telegram um.
"""

from __future__ import annotations

from statistics import median

from .training import MIN_AFTER

_DAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
_SHORT_DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

#: Anzeige je Ziel: Symbol und verstaendlicher Name.
_GOALS = {
    "util_h": ("💣", "HE-Granaten"),
    "util_m": ("🔥", "Molotovs"),
    "util_f": ("⚡", "Flashbangs"),
    "util_s": ("💨", "Smokes"),
    "utility": ("🧨", "Granaten"),
    "accuracy": ("🎯", "Treffsicherheit"),
    "crosshair_placement": ("➕", "Fadenkreuz-Position"),
    "counter_strafe": ("🛑", "Counter-Strafing"),
}

#: Kurzname fuer Saetze wie "eine HE oder Molotov werfen".
_THROW = {"util_h": "HE", "util_m": "Molotov", "util_f": "Flash", "util_s": "Smoke", "utility": "Granate"}

_VERDICT = {
    "solid_positive": "✅ belastbar besser",
    "solid_negative": "⚠️ belastbar schlechter",
    "solid_negligible": "➖ unverändert",
}


def de(x: float, digits: int = 1) -> str:
    """Zahl im deutschen Format, ohne ueberfluessige Nachkommastellen."""
    s = f"{x:.{digits}f}".rstrip("0").rstrip(".") if digits else f"{x:.0f}"
    return s.replace(".", ",")


def bar(fraction: float, width: int = 10) -> str:
    filled = max(0, min(width, round(fraction * width)))
    return "▰" * filled + "▱" * (width - filled)


def _rounds_per_match(exports: list[dict]) -> int:
    rounds = [(e.get("match") or {}).get("total_rounds") or 0 for e in exports[-10:]]
    rounds = [r for r in rounds if r > 0]
    return round(median(rounds)) if rounds else 21


def _when(e: dict) -> str:
    m = e.get("match") or {}
    return m.get("datetime") or m.get("date") or ""


# ── Ziele ────────────────────────────────────────────────────────────

def _goal_block(g, rounds: int) -> list[str]:
    icon, name = _GOALS.get(g.key, ("📌", g.label))
    per_round = g.unit == "/Runde"
    if per_round:
        target, recent = g.target * rounds, (g.recent or 0) * rounds
        every = f" (etwa jede {round(1 / g.target)}. Runde)" if 0 < g.target <= 0.75 else ""
        head = f"{icon} **{name}** · Ziel ~{de(target, 0)} pro Match{every}"
        now = f"zuletzt ~{de(recent, 0)}"
    else:
        unit = f" {g.unit}" if g.unit == "%" else g.unit
        target, recent = g.target, g.recent or 0
        head = f"{icon} **{name}** · Ziel {de(target)}{unit}"
        now = f"zuletzt {de(recent)}{unit}"

    if g.recent is None or not target:
        lines = [head]
    else:
        frac = recent / target if g.higher_is_better else (target / recent if recent else 1.0)
        lines = [head, f"{bar(frac)} {now} ({de(min(frac, 9.99) * 100, 0)} %)"]

    if per_round and g.loss is not None:
        lines.append(f"_Warum: in deinen Siegen ~{de(g.target * rounds, 0)}, in Niederlagen ~{de(g.loss * rounds, 0)}._")
    elif not per_round and g.loss is not None:
        u = f" {g.unit}" if g.unit == "%" else g.unit
        lines.append(f"_Warum: in deinen Siegen {de(g.target)}{u}, in Niederlagen {de(g.loss)}{u}._")
    return lines


def _exercises(goals, main_map: str) -> tuple[list[str], list[str]]:
    """(vor dem ersten Match, im Match) - ohne Doppelungen."""
    before: list[str] = []
    during: list[str] = []
    throws = [_THROW[g.key] for g in goals if g.key in _THROW]
    if throws:
        names = " und ".join(dict.fromkeys(throws))
        before.append(f"Granaten: `exec coach/utility` - Wurfpositionen für {names} üben")
        during.append(f"In jeder Kaufrunde vor dem ersten Kontakt eine {' oder '.join(dict.fromkeys(throws))} "
                      f"auf eine typische Gegnerposition werfen")
    keys = {g.key for g in goals}
    if "accuracy" in keys:
        before.append(f"Aim: `exec coach/warmup` (bewegliche Bots), dann `exec coach/spray_{main_map}` "
                      f"(Spray-Transfer)")
        during.append("Kurze Salven statt Dauerfeuer auf Distanz")
    if "crosshair_placement" in keys:
        before.append(f"Prefire: `exec coach/practice_{main_map}` - Fadenkreuz auf Kopfhöhe")
        during.append("Beim Laufen das Fadenkreuz auf Kopfhöhe an der nächsten Ecke halten")
    if "counter_strafe" in keys:
        if not any("warmup" in b for b in before):
            before.append("Bewegung: `exec coach/warmup` - vor jedem Schuss kurz Gegenrichtung tippen")
        during.append("Erst stoppen, dann schießen")
    if not before:
        before.append("Aufwärmen: `exec coach/warmup`")
    return before, during


# ── Fortschritt ──────────────────────────────────────────────────────

def _progress(goals, win_change: dict | None, rounds: int) -> list[str]:
    lines = []
    waiting: dict[int, list[str]] = {}
    for g in goals:
        icon, name = _GOALS.get(g.key, ("📌", g.label))
        if not g.change:
            waiting.setdefault(g.matches_since, []).append(name)
            continue
        c = g.change
        scale = rounds if g.unit == "/Runde" else 1
        unit = " pro Match" if g.unit == "/Runde" else (f" {g.unit}" if g.unit == "%" else g.unit)
        digits = 0 if g.unit == "/Runde" else 1
        lines.append(f"{icon} {name}: {de(c['mean_b'] * scale, digits)} → {de(c['mean_a'] * scale, digits)}{unit} · "
                     f"{_VERDICT.get(c['verdict'], '⏳ noch offen')}")
    for n, names in sorted(waiting.items()):
        lines.append(f"⏳ {', '.join(names)}: {bar(n / MIN_AFTER, MIN_AFTER)} {n}/{MIN_AFTER} Matches "
                     f"bis zur ersten Auswertung")
    w = win_change or {}
    if "verdict" in w:
        lines.append(f"🏆 Siegquote: {de(w['mean_b'], 0)} % → {de(w['mean_a'], 0)} % · "
                     f"{_VERDICT.get(w['verdict'], '⏳ noch offen')}")
    return lines


# ── Matches ──────────────────────────────────────────────────────────

_RESULT = {"Sieg": "✅", "Niederlage": "❌"}


def _match_line(e: dict) -> str:
    m, p = e.get("match") or {}, e.get("player") or {}
    when = _when(e)
    try:
        from datetime import datetime
        d = datetime.strptime(when[:10], "%Y-%m-%d")
        day = f"{_SHORT_DAYS[d.weekday()]} {d.strftime('%d.%m.')}"
    except ValueError:
        day = when[:10]
    util = e.get("utility_positions") or []
    he = sum(1 for u in util if u.get("t") == "h")
    mo = sum(1 for u in util if u.get("t") == "m")
    parts = [f"{_RESULT.get(m.get('result'), '➖')} {day} **{m.get('map')} {m.get('score_own')}:{m.get('score_enemy')}**"]
    if p.get("rating") is not None:
        parts.append(f"Rating {float(p['rating']):.2f}".replace(".", ","))
    if p.get("adr") is not None:
        parts.append(f"ADR {de(float(p['adr']), 0)}")
    if "utility_positions" in e:
        parts.append(f"{he} HE, {mo} Molotovs")
    return " · ".join(parts)


# ── Gesamt ───────────────────────────────────────────────────────────

def render(plan: dict, exports: list[dict], new: list[dict], first: bool, now,
           main_map: str, intro: str | None = None, link: str = "") -> str:
    """Briefing als Discord-Markdown."""
    exports = sorted(exports, key=_when)
    rounds = _rounds_per_match(exports)
    goals = plan["goals"]

    out = [f"📋 **CS2 Coach · {_DAYS[now.weekday()]}, {now.strftime('%d.%m.')}**"]
    if intro:
        out.append(intro.strip())

    if goals:
        block = ["**🎯 Deine Ziele**"]
        for g in goals:
            block += _goal_block(g, rounds) + [""]
        out.append("\n".join(block).rstrip())

        before, during = _exercises(goals, main_map)
        how = [f"**🕔 Vor dem ersten Match** · offline `map de_{main_map}`, 10-15 Min"]
        how += [f"• {b}" for b in before]
        if during:
            how += ["", "**🎮 Im Match**"] + [f"• {d}" for d in during]
        out.append("\n".join(how))

        out.append("\n".join(["**📈 Fortschritt**"] + _progress(goals, plan.get("win_change"), rounds)))
    else:
        out.append("🎯 Keine offenen Trainingsziele - in den Daten trennt gerade nichts belastbar "
                   "deine Siege von deinen Niederlagen.")

    title = "**🗓️ Letzte Matches**" if first else "**🗓️ Neue Matches**"
    if new:
        out.append("\n".join([title] + [_match_line(e) for e in new[-8:]]))
    else:
        out.append(f"{title}\nKeine neuen. Valve stellt Demos mit 2-3 Tagen Verzögerung bereit.")

    if link:
        out.append(f"🔗 Details: {link.rstrip('/')}/coaching")
    return "\n\n".join(out)
