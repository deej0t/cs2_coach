"""Schluesselmomente als kommentierte Szene im 2D-Replay.

Welche Momente: die teuersten eigenen Tode (costly_moments.for_match). Was
dazu auf die Karte kommt, sind nur Tatsachen aus genau dieser Szene - wo du
standest, wer dich von wo erwischt hat, wie weit der naechste lebende
Mitspieler weg war, ob dein Killer innerhalb von 5 s getradet wurde. Das
sind Beschreibungen, keine Empfehlungen: ob z. B. Trades Runden entscheiden,
misst findings/death_review (Ergebnis: nicht trennscharf), nicht diese Seite.

Gearbeitet wird auf dem fertigen Replay (build_round_replay), also in
Radar-Pixeln; Abstaende werden ueber den Map-Massstab in Spieleinheiten
zurueckgerechnet.
"""

from __future__ import annotations

import math

TICKRATE = 64
TRADE_SECONDS = 5           # wie death_review.TRADE_SECONDS
FAR_MATE_UNITS = 1000       # wie death_review/findings ("naechster Mitspieler > 1000")
UNITS_PER_METER = 52.5      # 1 Einheit ~ 1,905 cm
LEAD_SECONDS = 6            # Clip beginnt so weit vor dem Tod
TAIL_SECONDS = 4            # ... und laeuft so weit danach (Trade sichtbar)


def _meters(units: float) -> int:
    return round(units / UNITS_PER_METER)


def _frame_at(frames: list[dict], tick: int) -> int:
    """Index des letzten Frames bis ``tick`` (0, wenn der Tod vor dem ersten Frame liegt)."""
    best = 0
    for i, f in enumerate(frames):
        if f["t"] <= tick:
            best = i
        else:
            break
    return best


def annotate(replay: dict, death_tick: int, scale: float) -> dict:
    """Markierungen und Kommentare fuer deinen Tod in diesem Replay.

    ``scale``: Spieleinheiten je Radar-Pixel (maps.MAP_RADAR_DATA[map]["scale"]).
    Rueckgabe: ``focus`` (Frame-Index des Todes), ``clip`` [von, bis] als
    Frame-Indizes, ``marks`` (Kreise/Linien in Radar-Pixeln) und ``notes``
    (Saetze mit ``tone`` good/bad/info).
    """
    players = replay.get("players") or []
    frames = replay.get("frames") or []
    if not frames:
        return {"focus": 0, "clip": [0, 0], "marks": [], "notes": []}
    me = next((i for i, p in enumerate(players) if p.get("is_target")), None)
    my_sid = players[me]["steamid"] if me is not None else ""
    kill = next((k for k in replay.get("kills") or []
                 if k["victim"] == my_sid and abs(k["tick"] - death_tick) <= TICKRATE), None)
    if kill:
        death_tick = kill["tick"]
    focus = _frame_at(frames, death_tick)
    # Positionen einen Frame vorher: im Todesframe kann deine HP schon 0 sein
    before = frames[max(focus - 1, 0)]["p"]
    pos = {e[0]: e for e in before}
    fps = replay.get("fps") or 8
    clip = [max(focus - LEAD_SECONDS * fps, 0), min(focus + TAIL_SECONDS * fps, len(frames) - 1)]

    marks, notes = [], []
    mine = pos.get(me)
    if mine is None:
        return {"focus": focus, "clip": clip, "marks": marks, "notes": notes}
    marks.append({"type": "circle", "x": mine[1], "y": mine[2], "r": 22, "tone": "bad",
                  "label": "dein Tod"})

    def dist(a, b) -> float:
        return math.hypot(a[1] - b[1], a[2] - b[2]) * scale

    killer_idx = next((i for i, p in enumerate(players) if kill and p["steamid"] == kill["attacker"]), None)
    killer = pos.get(killer_idx)
    if killer is not None:
        name = players[killer_idx]["name"]
        marks.append({"type": "line", "x1": killer[1], "y1": killer[2], "x2": mine[1], "y2": mine[2],
                      "tone": "bad"})
        marks.append({"type": "circle", "x": killer[1], "y": killer[2], "r": 14, "tone": "enemy",
                      "label": name[:12]})
        weapon = (kill.get("weapon") or "").replace("weapon_", "")
        notes.append({"tone": "info", "text": f"Erwischt von {name}"
                      + (f" ({weapon})" if weapon else "") + f" aus {_meters(dist(killer, mine))} m."})

    enemies_alive = [e for i, e in pos.items() if e[3] > 0 and players[i].get("team") == "enemy"]
    near = [e for e in enemies_alive if dist(e, mine) <= FAR_MATE_UNITS]
    if len(near) >= 2:
        notes.append({"tone": "bad", "text": f"{len(near)} Gegner waren im Umkreis von "
                      f"{_meters(FAR_MATE_UNITS)} m - mehr als einer konnte dich treffen."})

    mates = [(dist(e, mine), i, e) for i, e in pos.items()
             if e[3] > 0 and players[i].get("team") == "mate"]
    if mates:
        d, i, e = min(mates)
        tone = "bad" if d > FAR_MATE_UNITS else "good"
        marks.append({"type": "circle", "x": e[1], "y": e[2], "r": 14, "tone": tone,
                      "label": f"{_meters(d)} m"})
        marks.append({"type": "dash", "x1": e[1], "y1": e[2], "x2": mine[1], "y2": mine[2], "tone": tone})
        if tone == "bad":
            notes.append({"tone": "bad", "text": f"Naechster Mitspieler ({players[i]['name']}) war "
                          f"{_meters(d)} m weg - zu weit fuer einen schnellen Trade."})
        else:
            notes.append({"tone": "good", "text": f"{players[i]['name']} stand {_meters(d)} m neben dir "
                          "und konnte nachziehen."})
    else:
        notes.append({"tone": "info", "text": "Kein Mitspieler lebte mehr."})

    if kill and killer_idx is not None:
        mate_sids = {p["steamid"] for p in players if p.get("team") == "mate"}
        trade = next((k for k in replay.get("kills") or []
                      if k["victim"] == kill["attacker"] and k["attacker"] in mate_sids
                      and 0 <= k["tick"] - kill["tick"] <= TRADE_SECONDS * TICKRATE), None)
        if trade:
            secs = (trade["tick"] - kill["tick"]) / TICKRATE
            notes.append({"tone": "good", "text": f"Getradet nach {secs:.1f} s."})
        elif mates:
            notes.append({"tone": "bad", "text": f"Kein Trade durch dein Team innerhalb von {TRADE_SECONDS} s."})
    return {"focus": focus, "clip": clip, "marks": marks, "notes": notes}


def headline(m: dict) -> str:
    """Kopfzeile eines Moments, ohne Nachkommastellen-Rauschen."""
    from .costly_moments import describe
    return describe(m)
