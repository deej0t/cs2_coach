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
    return {"focus": focus, "clip": clip, "marks": marks, "notes": notes,
            "killer": kill["attacker"] if kill else "", "killer_idx": killer_idx, "me_idx": me,
            "death_tick": death_tick}


def headline(m: dict) -> str:
    """Kopfzeile eines Moments, ohne Nachkommastellen-Rauschen."""
    from .costly_moments import describe
    return describe(m)


def safe_angle(v) -> int:
    """Blickwinkel als ganze Grad, 0 bei fehlendem Wert (NaN/None im Demo-Tick)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    return round(f) if math.isfinite(f) else 0


# ── Duell-Detail: wer sah wen zuerst, wohin zeigte dein Fadenkreuz ─────────
# Sichtbarkeit = approximate_spotted_by (wie parser._process_first_sight). Das
# Feld schlaegt fuer beide Seiten gleich verzoegert an (~124 ms) - die
# Differenz "er sah dich X ms frueher" ist deshalb fairer als absolute Zeiten.
DUEL_WINDOW_SECONDS = 4     # so weit vor dem Tod wird jeder Tick gelesen
SIGHT_QUIET_TICKS = 32      # 0,5 s nicht gesehen -> neues Sehen (wie First Sight)


def _signed_offset(ox, oy, oyaw, tx, ty) -> float:
    """Grad, um die das Ziel neben deiner Blickrichtung liegt; positiv = rechts von dir."""
    ideal = math.degrees(math.atan2(ty - oy, tx - ox))
    return (oyaw - ideal + 180) % 360 - 180


def _first_contact(ticks: list[int], seen: dict[int, bool], death_tick: int) -> int | None:
    """Beginn des letzten durchgehenden Sichtkontakts vor dem Tod (Luecken < 0,5 s zaehlen mit)."""
    start, last = None, None
    for t in ticks:
        if t > death_tick:
            break
        if seen.get(t):
            if last is None or t - last > SIGHT_QUIET_TICKS:
                start = t
            last = t
    return start


def duel_facts(rows: list[dict], shots: list[dict], me: str, killer: str, death_tick: int) -> dict:
    """Reine Auswertung ueber Tick-Zeilen (steamid, tick, X, Y, yaw, spotted_by).

    ``shots``: weapon_fire-Ereignisse (tick, steamid). Rueckgabe: Ticks des
    ersten Sichtkontakts beider Seiten, dein Fadenkreuz-Versatz in dem Moment,
    Zeit bis zum ersten Schuss beider Seiten.
    """
    by = {}
    for r in rows:
        by.setdefault(int(r["tick"]), {})[str(r["steamid"])] = r
    ticks = sorted(by)
    i_see = {t: me in {str(s) for s in (by[t].get(killer, {}).get("spotted_by") or [])} for t in ticks}
    he_sees = {t: killer in {str(s) for s in (by[t].get(me, {}).get("spotted_by") or [])} for t in ticks}
    my_sight = _first_contact(ticks, i_see, death_tick)
    his_sight = _first_contact(ticks, he_sees, death_tick)
    out = {"my_sight": my_sight, "his_sight": his_sight, "offset": None, "my_shot": None, "his_shot": None}
    if my_sight is not None:
        a, b = by[my_sight].get(me), by[my_sight].get(killer)
        if a and b and all(math.isfinite(float(v)) for v in (a["X"], a["Y"], a["yaw"], b["X"], b["Y"])):
            out["offset"] = round(_signed_offset(a["X"], a["Y"], a["yaw"], b["X"], b["Y"]), 1)
        out["my_shot"] = next((s["tick"] for s in shots if s["steamid"] == me and my_sight <= s["tick"] <= death_tick), None)
    if his_sight is not None:
        out["his_shot"] = next((s["tick"] for s in shots if s["steamid"] == killer and his_sight <= s["tick"] <= death_tick), None)
    return out


def duel_notes(f: dict, killer_name: str) -> tuple[list[dict], list[dict]]:
    """Kommentare und Zeitmarken (fuer Einblendungen im Clip) aus duel_facts."""
    notes, events = [], []
    ms = lambda ticks: round(ticks * 1000 / TICKRATE)  # noqa: E731
    mine, his = f["my_sight"], f["his_sight"]
    if his is not None:
        events.append({"tick": his, "text": f"{killer_name} sieht dich", "tone": "bad"})
    if mine is not None:
        events.append({"tick": mine, "text": f"Du siehst {killer_name}", "tone": "info"})
    if mine is None:
        notes.append({"tone": "bad", "text": f"Du hattest {killer_name} vor deinem Tod nicht im Blick "
                      "(von hinten, durch Smoke oder sehr schnell)."})
    elif his is not None:
        d = mine - his
        if d > 8:
            notes.append({"tone": "bad", "text": f"{killer_name} sah dich {ms(d)} ms früher als du ihn."})
        elif d < -8:
            notes.append({"tone": "good", "text": f"Du sahst {killer_name} {ms(-d)} ms früher."})
        else:
            notes.append({"tone": "info", "text": "Ihr saht euch praktisch gleichzeitig."})
    if f["offset"] is not None:
        o = f["offset"]
        side = "rechts" if o > 0 else "links"
        tone = "good" if abs(o) <= 5 else ("info" if abs(o) <= 20 else "bad")
        notes.append({"tone": tone, "text": f"Beim ersten Sichtkontakt lag dein Fadenkreuz {abs(o):.0f}° "
                      + (f"{side} neben ihm." if abs(o) > 1 else "direkt auf ihm.")})
    if mine is not None:
        if f["my_shot"] is not None:
            txt = f"Dein erster Schuss: {ms(f['my_shot'] - mine)} ms nach Sichtkontakt"
            if his is not None and f["his_shot"] is not None:
                txt += f", seiner: {ms(f['his_shot'] - his)} ms"
            notes.append({"tone": "info", "text": txt + "."})
            events.append({"tick": f["my_shot"], "text": "Dein erster Schuss", "tone": "info"})
        else:
            notes.append({"tone": "bad", "text": "Du hast zwischen Sichtkontakt und Tod nicht geschossen."})
    if f["his_shot"] is not None:
        events.append({"tick": f["his_shot"], "text": f"{killer_name} schießt", "tone": "bad"})
    return notes, events


def scan_duel(demo_path: str, me: str, killer: str, death_tick: int) -> dict:
    """Liest das Duell-Fenster aus der Demo (jeder Tick, nur du und dein Killer)."""
    from demoparser2 import DemoParser
    p = DemoParser(demo_path)
    ticks = list(range(max(death_tick - DUEL_WINDOW_SECONDS * TICKRATE, 0), death_tick + 1))
    df = p.parse_ticks(["X", "Y", "yaw", "approximate_spotted_by"], ticks=ticks)
    rows = [{"tick": t, "steamid": str(s), "X": x, "Y": y, "yaw": yw, "spotted_by": sp}
            for t, s, x, y, yw, sp in zip(df["tick"], df["steamid"], df["X"], df["Y"], df["yaw"],
                                          df["approximate_spotted_by"]) if str(s) in (me, killer)]
    shots = []
    try:
        fdf = p.parse_event("weapon_fire")
        shots = sorted(({"tick": int(t), "steamid": str(s)} for t, s in zip(fdf["tick"], fdf["user_steamid"])
                        if ticks[0] <= int(t) <= death_tick and str(s) in (me, killer)),
                       key=lambda s: s["tick"])
    except Exception:  # noqa: BLE001 - ohne Schuesse bleibt der Rest
        pass
    return duel_facts(rows, shots, me, killer, death_tick)
