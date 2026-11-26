"""Aim-Verlauf eines Duells, tickgenau: wohin zeigte dein Fadenkreuz relativ zum Kopf des Gegners.

Fuer die 3D-Ansicht der Schluesselmomente (/momente). Je Tick (64 Hz) der
Winkelfehler zwischen deiner Blickrichtung und der Linie Auge -> Kopf des
Gegners, getrennt nach horizontal (links/rechts) und vertikal (hoch/tief),
dazu deine Geschwindigkeit und jeder Schuss mit Treffer/Fehlschuss.

Das sind Beschreibungen einer einzelnen Szene, keine Hebel: ob z. B. Flick-
Ueberschiessen Runden kostet, misst diese Datei nicht. Vorsicht beim Spray:
die Demo-Blickwinkel enthalten den Rueckstoss (Aim-Punch) nicht - wer richtig
gegenzieht, dessen Fadenkreuz liegt *unter* dem Kopf. Deshalb wird nur der
erste Schuss einer Salve als "daneben" beurteilt.
"""

from __future__ import annotations

import math

TICKRATE = 64
HEAD_STAND = 64             # Kopfmitte ~ Augenhoehe ueber den Fuessen
HEAD_DUCK = 46
HEAD_RADIUS = 5.0           # Spieleinheiten, grob
MOVE_THRESHOLD = 80.0       # wie parser.COUNTER_STRAFE_SPEED_THRESHOLD
ON_TARGET_DEG = 3.0         # "auf dem Ziel" fuer die Zeit bis zum Ziel
SPRAY_GAP_TICKS = 16        # Schuesse mit < 0,25 s Abstand = eine Salve
OVERSHOOT_DEG = 2.0         # so weit muss man ueber das Ziel hinaus, damit es zaehlt


def _f(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _wrap(a: float) -> float:
    return (a + 180) % 360 - 180


def errors(me: dict, enemy: dict) -> tuple[float, float, float, float] | None:
    """(horizontal, vertikal, gesamt in Grad, Distanz) von deinem Blick zum Kopf des Gegners.

    horizontal positiv = Fadenkreuz rechts vom Kopf; vertikal positiv = Fadenkreuz
    ueber dem Kopf. Source: yaw gegen den Uhrzeigersinn ab +X, pitch positiv = nach unten.
    """
    vals = [_f(me.get(k)) for k in ("X", "Y", "Z", "yaw", "pitch")] + [_f(enemy.get(k)) for k in ("X", "Y", "Z")]
    if any(v is None for v in vals):
        return None
    mx, my, mz, yaw, pitch, ex, ey, ez = vals
    eye = mz + (HEAD_DUCK if me.get("ducking") else HEAD_STAND)
    head = ez + (HEAD_DUCK if enemy.get("ducking") else HEAD_STAND)
    dx, dy, dz = ex - mx, ey - my, head - eye
    flat = math.hypot(dx, dy)
    dist = math.hypot(flat, dz)
    if dist < 1:
        return None
    ideal_yaw = math.degrees(math.atan2(dy, dx))
    ideal_pitch = -math.degrees(math.atan2(dz, flat))
    h = _wrap(ideal_yaw - yaw)          # yaw kleiner als ideal = Blick rechts vom Ziel
    v = ideal_pitch - pitch              # pitch kleiner = Blick hoeher
    # echter Winkel zwischen den Richtungen
    def vec(yw, pt):
        y, p = math.radians(yw), math.radians(pt)
        return (math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), -math.sin(p))
    a, b = vec(yaw, pitch), vec(ideal_yaw, ideal_pitch)
    dot = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))
    return h, v, math.degrees(math.acos(dot)), dist


def trace(rows: list[dict], shots: list[dict], hurts: list[dict], me: str, enemy: str,
          sight_tick: int | None = None) -> dict:
    """Reine Auswertung. ``rows``: Tick-Zeilen (tick, steamid, X, Y, Z, yaw, pitch, ducking).

    ``shots``: weapon_fire (tick, steamid, weapon). ``hurts``: player_hurt (tick,
    attacker, victim, hitgroup, dmg). Rueckgabe: ``series`` (je Tick [tick, h, v,
    gesamt, Tempo]), ``shots`` (bewertet) und ``mistakes`` (Saetze mit Tick).
    """
    by: dict[int, dict] = {}
    for r in rows:
        by.setdefault(int(r["tick"]), {})[str(r["steamid"])] = r
    series, prev = [], None
    for t in sorted(by):
        a, b = by[t].get(me), by[t].get(enemy)
        speed = None
        if a and prev and _f(a.get("X")) is not None and _f(prev[1].get("X")) is not None and t > prev[0]:
            speed = math.hypot(a["X"] - prev[1]["X"], a["Y"] - prev[1]["Y"]) * TICKRATE / (t - prev[0])
        if a:
            prev = (t, a)
        e = errors(a, b) if a and b else None
        if e is None:
            continue
        h, v, tot, dist = e
        series.append([t, round(h, 2), round(v, 2), round(tot, 2), round(speed or 0), round(dist)])
    at = {s[0]: s for s in series}

    def nearest(t):
        if not series:
            return None
        return at.get(t) or min(series, key=lambda s: abs(s[0] - t))

    my_shots = sorted((s for s in shots if s["steamid"] == me), key=lambda s: s["tick"])
    my_hits = [h for h in hurts if h["attacker"] == me and h["victim"] == enemy]
    out_shots, last = [], None
    for s in my_shots:
        row = nearest(s["tick"])
        if row is None or abs(row[0] - s["tick"]) > 4:
            continue
        hit = next((h for h in my_hits if 0 <= h["tick"] - s["tick"] <= 2), None)
        first = last is None or s["tick"] - last > SPRAY_GAP_TICKS
        last = s["tick"]
        head_deg = math.degrees(math.atan2(HEAD_RADIUS, max(row[5], 1)))
        out_shots.append({"tick": s["tick"], "weapon": s.get("weapon", ""), "h": row[1], "v": row[2],
                          "err": row[3], "speed": row[4], "moving": row[4] > MOVE_THRESHOLD,
                          "first": first, "head_deg": round(head_deg, 2),
                          "hit": bool(hit), "hitgroup": hit.get("hitgroup") if hit else None,
                          "dmg": hit.get("dmg") if hit else 0})
    return {"series": series, "shots": out_shots, "mistakes": mistakes(series, out_shots, sight_tick)}


def mistakes(series: list[list], shots: list[dict], sight_tick: int | None) -> list[dict]:
    """Auffaelligkeiten der Szene als Saetze mit Tick (fuer Marker in Zeitleiste und 3D)."""
    out: list[dict] = []
    ms = lambda ticks: round(ticks * 1000 / TICKRATE)  # noqa: E731
    after = [s for s in series if sight_tick is None or s[0] >= sight_tick]
    if sight_tick is not None and after:
        start = after[0]
        on = next((s for s in after if s[3] <= ON_TARGET_DEG), None)
        if on is not None and start[3] > ON_TARGET_DEG:
            out.append({"tick": on[0], "tone": "info",
                        "text": f"Fadenkreuz nach {ms(on[0] - start[0])} ms auf dem Kopf (Start: {start[3]:.0f}° daneben)."})
        elif on is None:
            best = min(after, key=lambda s: s[3])
            out.append({"tick": best[0], "tone": "bad",
                        "text": f"Fadenkreuz kam nie näher als {best[3]:.1f}° an den Kopf."})
        if start[3] > ON_TARGET_DEG:
            vert = abs(start[2]) > abs(start[1])
            where = (("zu hoch" if start[2] > 0 else "zu tief") if vert
                     else ("rechts daneben" if start[1] > 0 else "links daneben"))
            out.append({"tick": start[0], "tone": "bad" if start[3] > 15 else "info",
                        "text": f"Beim Sichtkontakt lag das Fadenkreuz {start[3]:.0f}° {where}."})
        # Ueberschiessen: horizontaler Fehler wechselt das Vorzeichen und geht deutlich ueber das Ziel
        for i in range(1, len(after)):
            p, c = after[i - 1][1], after[i][1]
            if p * c < 0 and abs(after[0][1]) > 5:
                peak = max((s for s in after[i:i + 20] if s[1] * c > 0), key=lambda s: abs(s[1]), default=None)
                if peak and abs(peak[1]) >= OVERSHOOT_DEG:
                    out.append({"tick": peak[0], "tone": "bad",
                                "text": f"Flick überschossen: {abs(peak[1]):.1f}° über den Kopf hinaus."})
                break
    for s in shots:
        if s["moving"]:
            out.append({"tick": s["tick"], "tone": "bad",
                        "text": f"Schuss in der Bewegung ({s['speed']} u/s) - nicht gegengestrafet."})
        if s["first"] and not s["hit"] and s["err"] > max(s["head_deg"] * 2, 1.0):
            where = ("links" if s["h"] < 0 else "rechts") if abs(s["h"]) >= abs(s["v"]) else ("über" if s["v"] > 0 else "unter")
            out.append({"tick": s["tick"], "tone": "bad",
                        "text": f"Erster Schuss {s['err']:.1f}° {where} " + ("dem Kopf" if where in ("über", "unter") else "vom Kopf") + " - verfehlt."})
    if shots:
        hits = sum(1 for s in shots if s["hit"])
        heads = sum(1 for s in shots if s["hitgroup"] in ("head", 1, "1"))
        out.append({"tick": shots[0]["tick"], "tone": "info",
                    "text": f"{len(shots)} " + ("Schuss" if len(shots) == 1 else "Schüsse") + f", {hits} Treffer" + (f", davon {heads} Kopf." if heads else ".")})
    return sorted(out, key=lambda m: m["tick"])


def scan(demo_path: str, me: str, enemy: str, start_tick: int, end_tick: int,
         sight_tick: int | None = None) -> dict:
    """Liest das Fenster aus der Demo (jeder Tick, nur du und der Gegner)."""
    from demoparser2 import DemoParser
    p = DemoParser(demo_path)
    ticks = list(range(max(start_tick, 0), end_tick + 1))
    df = p.parse_ticks(["X", "Y", "Z", "yaw", "pitch", "ducking"], ticks=ticks)
    rows = [{"tick": t, "steamid": str(s), "X": x, "Y": y, "Z": z, "yaw": yw, "pitch": pt, "ducking": bool(d)}
            for t, s, x, y, z, yw, pt, d in zip(df["tick"], df["steamid"], df["X"], df["Y"], df["Z"],
                                                df["yaw"], df["pitch"], df["ducking"]) if str(s) in (me, enemy)]
    shots, hurts = [], []
    try:
        fdf = p.parse_event("weapon_fire")
        wcol = fdf["weapon"] if "weapon" in fdf.columns else [""] * len(fdf)
        shots = [{"tick": int(t), "steamid": str(s), "weapon": str(w).replace("weapon_", "")}
                 for t, s, w in zip(fdf["tick"], fdf["user_steamid"], wcol) if start_tick <= int(t) <= end_tick]
    except Exception:  # noqa: BLE001
        pass
    try:
        hdf = p.parse_event("player_hurt")
        hurts = [{"tick": int(t), "attacker": str(a), "victim": str(v), "hitgroup": hg, "dmg": int(dm or 0)}
                 for t, a, v, hg, dm in zip(hdf["tick"], hdf["attacker_steamid"], hdf["user_steamid"],
                                            hdf["hitgroup"], hdf["dmg_health"]) if start_tick <= int(t) <= end_tick + 2]
    except Exception:  # noqa: BLE001
        pass
    return trace(rows, shots, hurts, me, enemy, sight_tick)
