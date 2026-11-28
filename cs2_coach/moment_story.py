"""Ursachen-Storyline eines Schluesselmoments: Ausloeser -> Ablauf -> Auswirkung.

Zwei Teile, bewusst getrennt:
- ``build(scene, kind)``: alles, was aus der Demo folgt (Ausloeser, Ablauf, Hot-Ticks).
  Liegt im Szenen-Cache (.moments_cache, ``STORY_VERSION`` im Payload).
- ``context(...)``: Siegchance (winprob, out-of-fold) und Lobby-Vergleich (duel_value).
  Beides aendert sich mit jeder neuen Demo - daher NIE im Cache, sondern je Abruf frisch.

Die Bewertung (gut/schlecht) kommt aus moment_cause/key_moments/aim_trace; hier
entstehen keine neuen Schwellwerte. Der Lobby-Vergleich erscheint nur mit einem
belastbaren evidence.py-Verdict (SOLID_*), sonst gar nicht.
"""

from __future__ import annotations

from pathlib import Path

from . import moment_cause
from .key_moments import TICKRATE

STORY_VERSION = 1
MAX_FLOW = 4

# Hot-Tick-Typen fuer Zeitstrahl/3D; Rang 3 = gross, 1 = klein
_HOT = {"sight_his": ("contact", 2), "sight_me": ("contact", 2), "shot": ("shot", 1), "shot_his": ("shot", 1),
        "hit": ("hit", 1), "hit_taken": ("hit", 2), "blind": ("blind", 2), "nade": ("nade_throw", 1),
        "trade": ("trade", 2), "death": ("death", 3), "kill": ("kill", 3)}
_NADE = {"smoke": "Smoke", "flash": "Flash", "he": "HE", "molotov": "Molotov", "incgrenade": "Molotov",
         "inferno": "Feuer", "decoy": "Decoy"}


def _names(scene: dict) -> tuple[str, str, str]:
    mo = scene.get("moment") or {}
    players = scene.get("players") or []
    i, j = mo.get("me_idx"), mo.get("killer_idx")
    me = players[i]["steamid"] if isinstance(i, int) and i < len(players) else ""
    foe = players[j]["name"] if isinstance(j, int) and j < len(players) else "Gegner"
    return me, mo.get("killer") or "", foe


def _seen_by(facts: dict) -> int | None:
    """Wie viele Gegner dich im Todestick sahen (approximate_spotted_by); alte Szenen: None."""
    v = facts.get("seen_by")
    return v if isinstance(v, int) else None


def build(scene: dict, kind: str = "death", cause: dict | None = None) -> dict:
    """Storyline + Hot-Ticks aus einer fertigen Szene (fehlende Felder: leere Teile)."""
    cause = cause or scene.get("cause") or moment_cause.summarize(scene, kind)
    mo = scene.get("moment") or {}
    facts = mo.get("facts") or {}
    is_kill = kind == "kill" or mo.get("kind") == "kill"
    want = "good" if is_kill else "bad"
    me_sid, foe_sid, foe = _names(scene)
    end = mo.get("death_tick")

    # Ausloeser: fruehester Faktor in der Richtung des Ausgangs (beim Tod: was dagegen lief)
    timed = sorted((c for c in cause.get("causes") or [] if c.get("tick") is not None), key=lambda c: c["tick"])
    lead = next((c for c in timed if c["tone"] == want), None) \
        or next((c for c in cause.get("causes") or [] if c["tone"] == want), None)
    if lead:
        trigger = {"text": lead["text"], "tick": lead.get("tick"), "tone": lead["tone"], "label": lead["label"]}
    else:
        first = next((t for t in cause.get("turns") or [] if t["kind"] in ("sight_his", "sight_me")), None)
        trigger = {"text": (first["label"] + ".") if first else "Kein klarer Auslöser in der Szene.",
                   "tick": first["tick"] if first else None, "tone": "info", "label": "Erster Kontakt" if first else ""}

    # Ablauf: die uebrigen Ursachen + Granaten/Exponiertheit, zeitlich sortiert
    flow = [{"text": c["text"], "tick": c.get("tick"), "tone": c["tone"]}
            for c in cause.get("causes") or [] if c is not lead and c.get("tick") is not None]
    for t in cause.get("turns") or []:
        if t["kind"] == "nade":
            flow.append({"text": t["label"] + " fliegt.", "tick": t["tick"], "tone": "util"})
    seen = _seen_by(facts)
    if seen is not None and seen >= 2 and not is_kill and end is not None:
        flow.append({"text": f"Im Moment des Todes sahen dich {seen} Gegner.", "tick": end, "tone": "bad"})
    flow.sort(key=lambda f: f["tick"])
    flow = flow[-MAX_FLOW:]                       # die letzten Sekunden vor dem Moment zaehlen

    # Auswirkung ohne Siegchance (die kommt frisch aus context): Trade/Schaden aus den Ursachen
    after = [c["text"] for c in cause.get("causes") or [] if c["key"] in ("trade", "damage") and c is not lead]
    impact = {"text": " ".join(after[:2]), "tick": end}

    # Hot-Ticks: Wendepunkte + Detonationen der beiden Duellanten, nur im Clip
    frames = scene.get("frames") or []
    clip = mo.get("clip") or [0, max(len(frames) - 1, 0)]
    t0 = frames[clip[0]]["t"] if frames else None
    t1 = frames[min(clip[1], len(frames) - 1)]["t"] if frames else None
    hot = []
    for t in cause.get("turns") or []:
        typ, rank = _HOT.get(t["kind"], ("info", 1))
        hot.append({"tick": t["tick"], "type": typ, "rank": rank, "label": t["label"], "tone": t["tone"]})
    for e in ((scene.get("nades") or {}).get("effects") or []):
        if e.get("sid") and e.get("sid") in (me_sid, foe_sid):
            who = "Deine" if e["sid"] == me_sid else f"{foe}s"
            hot.append({"tick": int(e["t0"]), "type": "nade_pop", "rank": 1, "tone": "util",
                        "label": f"{who} {_NADE.get(e.get('type'), 'Granate')} wirkt",
                        "pos": [e.get("x"), e.get("y"), e.get("z")]})
    if t0 is not None:
        hot = [h for h in hot if t0 <= h["tick"] <= t1]
    hot.sort(key=lambda h: h["tick"])
    contact = [h["tick"] for h in hot if h["type"] == "contact"]
    duel = [min(contact), end] if contact and end is not None and min(contact) <= end else None
    return {"version": STORY_VERSION, "verdict": cause.get("verdict", ""), "tone": cause.get("tone", "info"),
            "trigger": trigger, "flow": flow, "impact": impact, "hot": hot, "duel": duel,
            "causes": cause.get("causes") or []}


# ── frischer Kontext (nicht gecacht) ────────────────────────────────────────

def _find(rows: list, rnd: int, tick: int) -> dict | None:
    best = [r for r in rows if r.get("round") == rnd]
    return min(best, key=lambda r: abs(int(r.get("tick") or 0) - tick)) if best else None


def impact_text(m: dict, kind: str) -> dict:
    """Siegchance vorher/nachher aus einem costly_moments-/best_kills-Eintrag."""
    before = float(m.get("before") or 0)
    delta = float(m.get("gain") if kind == "kill" else -float(m.get("cost") or 0)) if kind == "kill" \
        else -float(m.get("cost") or 0)
    after = before + delta
    own, enemy = int(m.get("own") or 0), int(m.get("enemy") or 0)
    left = f"{own} gegen {enemy - 1}" if kind == "kill" else f"{own - 1} gegen {enemy}"
    sign = "+" if delta > 0 else "−"
    return {"before": round(before), "after": round(after), "delta": round(delta),
            "text": f"Siegchance {before:.0f} % → {after:.0f} % ({sign}{abs(delta):.0f} Punkte), danach {left}."}


def lobby_line(priced: list[dict], me: str, d: dict | None, kind: str) -> dict | None:
    """Belastbarer Lobby-Vergleich am Callout dieses Duells - sonst None."""
    from .duel_value import by_zone
    from .evidence import SOLID_NEGATIVE, SOLID_POSITIVE
    if not d or not priced:
        return None
    side = d["side"] if kind != "kill" else ("T" if d["side"] == "CT" else "CT")
    place = d.get("place") if kind != "kill" else d.get("by_place")
    if not place:
        return None
    g = next((g for g in by_zone(priced, me) if g.key == (d.get("map"), side, place)), None)
    if g is None or g.value is None or g.value.verdict not in (SOLID_POSITIVE, SOLID_NEGATIVE):
        return None
    from .live_plan import pretty_place
    v = g.value
    better = v.verdict == SOLID_POSITIVE
    return {"tone": "good" if better else "bad", "effect": round(v.effect, 2),
            "ci": [round(v.ci_low, 2), round(v.ci_high, 2)], "n": g.n,
            "text": f"{pretty_place(place)} als {side}: deine Duelle dort sind belastbar "
                    f"{'besser' if better else 'schlechter'} als die der Lobby "
                    f"({g.mean:+.1f} vs {g.lobby_mean:+.1f} Punkte je Duell, n = {g.n})."}


def context(cfg: dict, config_path: Path, data: dict, me: str, rnd: int, tick: int, kind: str) -> dict:
    """Siegchance-Knick + Lobby-Vergleich fuer EINEN Moment (frisch, wie die Momente-Liste)."""
    from . import costly_moments, key_moments
    stem = Path((data.get("match") or {}).get("demo_file") or "").stem
    own, kills = key_moments.priced(cfg, config_path, me, prefer=[stem] if stem else None)
    out: dict = {"impact": None, "lobby": None}
    if kind == "kill":
        m = _find(key_moments.best_kills(data, kills, top=999), rnd, tick)
        raw = None
        if m:
            idx = sorted(x["tick"] for x in key_moments.best_kills(data, kills, top=999) if x["round"] == rnd).index(m["tick"])
            row = kills.get((stem, rnd)) or []
            raw = row[idx] if idx < len(row) else None
    else:
        m = _find(costly_moments.for_match(data, own, top=999), rnd, tick)
        raw = own.get((stem, rnd))
    if m and abs(int(m.get("tick") or 0) - tick) <= TICKRATE:
        out["impact"] = impact_text(m, kind)
        try:
            out["lobby"] = lobby_line(key_moments.priced_list(), me, raw, kind)
        except Exception:  # noqa: BLE001 - ohne Lobby-Zeile
            out["lobby"] = None
    return out
