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
DUEL_FIELDS = ["X", "Y", "yaw", "approximate_spotted_by"]
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


def duel_notes(f: dict, killer_name: str, kill: bool = False) -> tuple[list[dict], list[dict]]:
    """Kommentare und Zeitmarken (fuer Einblendungen im Clip) aus duel_facts.

    ``kill``: das Duell endete mit deinem Kill statt deinem Tod.
    """
    notes, events = [], []
    ms = lambda ticks: round(ticks * 1000 / TICKRATE)  # noqa: E731
    mine, his = f["my_sight"], f["his_sight"]
    if his is not None:
        events.append({"tick": his, "text": f"{killer_name} sieht dich", "tone": "bad"})
    if mine is not None:
        events.append({"tick": mine, "text": f"Du siehst {killer_name}", "tone": "info"})
    if mine is None and kill:
        notes.append({"tone": "info", "text": f"Vor dem Kill gab es keinen Sichtkontakt zu {killer_name} - durch Smoke/Wand "
                      "oder so schnell, dass die Demo ihn noch nicht als gesehen fuehrte (unter ~0,1 s)."})
    elif mine is None:
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
        elif not kill:
            notes.append({"tone": "bad", "text": "Du hast zwischen Sichtkontakt und Tod nicht geschossen."})
    if f["his_shot"] is not None:
        events.append({"tick": f["his_shot"], "text": f"{killer_name} schießt", "tone": "bad"})
    return notes, events


def duel_ticks(death_tick: int) -> list[int]:
    return list(range(max(death_tick - DUEL_WINDOW_SECONDS * TICKRATE, 0), death_tick + 1))


def scan_duel(demo_path: str, me: str, killer: str, death_tick: int, parser=None) -> dict:
    """Liest das Duell-Fenster aus der Demo (jeder Tick, nur du und dein Killer).

    ``parser``: gemeinsamer (demo_cache.CachedParser) - sonst ein eigener."""
    from demoparser2 import DemoParser
    p = parser or DemoParser(demo_path)
    ticks = duel_ticks(death_tick)
    df = p.parse_ticks(DUEL_FIELDS, ticks=ticks)
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


# ── Gute Momente: deine wertvollsten Kills ────────────────────────────────
# Ein Kill bringt deinem Team genau das, was der Tod den Gegner kostet
# (zwei Seiten, Summe 100 %) - derselbe Preis wie bei costly_moments, nur
# aus der anderen Richtung gelesen.

_PRICED: dict = {}     # (Config, Demo-Liste) -> priced_deaths, je Prozess


def priced(cfg: dict, config_path, me: str, prefer: list[str] | None = None, scan_new: int = 0):
    """(eigene Tode, eigene Kills) mit Preis, Schluessel (Demo-Stamm, Runde).

    Tode: ein Eintrag je Runde (wie costly_moments.priced_own_deaths), Kills:
    Liste je Runde in Reihenfolge der Rundensekunde.
    """
    from .duel_value import priced_deaths
    from .winprob import collect
    if not me:
        return {}, {}
    matches = collect(cfg, config_path, log=lambda *_: None, max_new=scan_new, prefer=prefer)
    if len(matches) < 2:
        return {}, {}
    # Bepreisen passt 20 Siegchance-Tabellen an (Server ~3 s) - aendert sich nur mit neuen Demos
    sig = (str(config_path), tuple(sorted(m["demo"] for m in matches)))
    priced = _PRICED.get(sig)
    if priced is None:
        priced = priced_deaths(matches)
        _PRICED.clear()
        _PRICED[sig] = priced
    deaths, kills = {}, {}
    for d in priced:
        key = (d["demo"], d["round"])
        if d["sid"] == me:
            deaths[key] = d
        elif d["by"] == me:
            kills.setdefault(key, []).append(d)
    for v in kills.values():
        v.sort(key=lambda d: d["second"])
    return deaths, kills


def best_kills(data: dict, kills: dict, top: int = 5) -> list[dict]:
    """Deine Kills dieses Matches, die am meisten Siegchance gebracht haben.

    Zuordnung zum Export (Tick, Gegnername) ueber die Reihenfolge innerhalb der
    Runde: der n-te Kill im Export ist der n-te bepreiste Kill. Stimmt die
    Anzahl nicht ueberein, wird die Runde ausgelassen statt geraten.
    """
    from pathlib import Path
    stem = Path((data.get("match") or {}).get("demo_file") or "").stem
    per_round: dict[int, list[dict]] = {}
    for kp in data.get("kill_positions") or []:
        if kp.get("t") == "k":
            per_round.setdefault(int(kp.get("r") or 0), []).append(kp)
    out = []
    for r, kps in per_round.items():
        priced_round = kills.get((stem, r)) or []
        if len(priced_round) != len(kps):
            continue
        for kp, d in zip(sorted(kps, key=lambda k: int(k.get("tk") or 0)), priced_round):
            mine = "T" if d["side"] == "CT" else "CT"
            out.append({"kind": "kill", "round": r, "side": mine, "gain": d["cost"],
                        "before": round(100 - d["before"], 1), "second": d["second"],
                        "own": d["enemy"], "enemy": d["own"], "planted": d["planted"],
                        "tick": int(kp.get("tk") or 0), "place": kp.get("p") or "",
                        "weapon": kp.get("w") or "", "opponent": kp.get("e") or ""})
    out.sort(key=lambda m: -m["gain"])
    return out[:top]


def kill_headline(m: dict) -> str:
    from .live_plan import pretty_place
    where = f" in {pretty_place(m['place'])}" if m["place"] else ""
    return (f"Runde {m['round']} als {m['side']}{where}: Kill an {m['opponent']} ({m['weapon']}) "
            f"im {m['own']} gegen {m['enemy']} nach {m['second']:g} s (Siegchance vorher {m['before']:g} %)")


def annotate_kill(replay: dict, kill_tick: int, scale: float) -> dict:
    """Markierungen fuer einen eigenen Kill: du gruen, dein Opfer rot, Linie dazwischen."""
    players = replay.get("players") or []
    frames = replay.get("frames") or []
    if not frames:
        return {"focus": 0, "clip": [0, 0], "marks": [], "notes": []}
    me = next((i for i, p in enumerate(players) if p.get("is_target")), None)
    my_sid = players[me]["steamid"] if me is not None else ""
    kill = next((k for k in replay.get("kills") or []
                 if k["attacker"] == my_sid and abs(k["tick"] - kill_tick) <= TICKRATE), None)
    if kill:
        kill_tick = kill["tick"]
    focus = _frame_at(frames, kill_tick)
    pos = {e[0]: e for e in frames[max(focus - 1, 0)]["p"]}
    fps = replay.get("fps") or 8
    clip = [max(focus - LEAD_SECONDS * fps, 0), min(focus + TAIL_SECONDS * fps, len(frames) - 1)]
    victim_idx = next((i for i, p in enumerate(players) if kill and p["steamid"] == kill["victim"]), None)
    marks, notes = [], []
    mine, victim = pos.get(me), pos.get(victim_idx)
    if mine is not None:
        marks.append({"type": "circle", "x": mine[1], "y": mine[2], "r": 22, "tone": "good", "label": "du"})
    if mine is not None and victim is not None:
        d = math.hypot(mine[1] - victim[1], mine[2] - victim[2]) * scale
        marks.append({"type": "line", "x1": mine[1], "y1": mine[2], "x2": victim[1], "y2": victim[2], "tone": "good"})
        marks.append({"type": "circle", "x": victim[1], "y": victim[2], "r": 14, "tone": "enemy",
                      "label": players[victim_idx]["name"][:12]})
        weapon = (kill.get("weapon") or "").replace("weapon_", "")
        notes.append({"tone": "good", "text": f"Kill gegen {players[victim_idx]['name']}"
                      + (f" ({weapon})" if weapon else "") + f" aus {_meters(d)} m."})
    return {"focus": focus, "clip": clip, "marks": marks, "notes": notes,
            "killer": kill["victim"] if kill else "", "killer_idx": victim_idx, "me_idx": me,
            "death_tick": kill_tick, "kind": "kill"}


# ── 3D: Positionen mit Hoehe und Neigung fuer den Clip ────────────────────
EYE_HEIGHT = 64             # Augenhoehe stehend (Spieleinheiten ueber den Fuessen)
EYE_HEIGHT_DUCKED = 46
VIEW3D_STEP = 2             # jeder 2. Tick = 32 Hz fuer die 3D-Ansicht


VIEW3D_FIELDS = ["X", "Y", "Z", "yaw", "pitch", "health", "ducking", "team_num", "active_weapon_name",
                 "active_weapon_ammo", "armor_value", "flash_duration", "is_scoped", "balance", "inventory",
                 "has_helmet", "has_defuser", "total_ammo_left", "game_time", "round_start_time",
                 "team_rounds_total", "is_bomb_planted"]


ROUND_SECONDS = 115         # CS2-Rundenzeit nach der Freezetime
HUD_STEP = 8                # Uhr/Spielstand: jeder 8. Tick reicht (8 Hz)


def scan_3d(demo_path: str, players: list[dict], ticks: list[int], parser=None, hud: dict | None = None) -> dict:
    """Je Clip-Tick eine Zeile je Spieler, in Spieleinheiten:

    [0 Index, 1-3 x/y/z, 4 yaw, 5 pitch, 6 hp, 7 geduckt, 8 Seite (2=T, 3=CT), 9 Waffen-Schluessel
    (models3d.WEAPONS), 10 Waffenname, 11 Munition im Magazin, 12 Ruestung, 13 geblendet, 14 Zielfernrohr,
    15 Geld, 16 Inventar (Liste; None = unveraendert seit der letzten Zeile dieses Spielers),
    17 Helm, 18 Defuse-Kit, 19 Reservemagazine].

    ``hud`` (optional) wird gefuellt mit ``clock``: [[Tick, Rundenzeit uebrig in s, Siege CT, Siege T,
    Bombe gelegt], ...] im HUD_STEP-Raster.

    Spieler-Index wie im Replay (``players``), damit 2D und 3D dieselben
    Personen meinen. Nur fuer die Clip-Ticks - eine ganze Runde waere
    unnoetig gross. Seite aus team_num desselben Ticks (Roster != Seite, s. CLAUDE.md).
    """
    from demoparser2 import DemoParser

    from .models3d import weapon_key
    if not ticks:
        return {}
    ticks = sorted(ticks)
    idx = {p["steamid"]: i for i, p in enumerate(players)}
    fields = VIEW3D_FIELDS
    df = (parser or DemoParser(demo_path)).parse_ticks(fields, ticks=ticks)
    out: dict[int, list] = {}
    clock: dict[int, list] = {}
    last_inv: dict[int, tuple] = {}
    names = ["tick", "steamid"] + fields
    cols = [df[c] if c in df.columns else [None] * len(df) for c in names]
    for row in zip(*cols):
        r = dict(zip(names, row))
        t = int(r["tick"])
        side = _int(r["team_num"])
        # Raster ab dem ersten Clip-Tick: der kann ungerade sein, "t % 8" traf dann nie
        if hud is not None and (t - ticks[0]) % HUD_STEP == 0 and side in (2, 3):
            c = clock.setdefault(t, [t, None, None, None, False])
            if c[1] is None and _float(r["game_time"]) > 0:
                c[1] = round(max(0.0, ROUND_SECONDS - (_float(r["game_time"]) - _float(r["round_start_time"]))), 1)
            c[2 if side == 3 else 3] = _int(r["team_rounds_total"])
            c[4] = c[4] or bool(r["is_bomb_planted"])
        i = idx.get(str(r["steamid"]))
        x, y, z = r["X"], r["Y"], r["Z"]
        if i is None or any(v is None or not math.isfinite(float(v)) for v in (x, y, z)):
            continue
        wname = r["active_weapon_name"] if isinstance(r["active_weapon_name"], str) else ""
        inv = r["inventory"]
        # Inventar als Modell-/Symbolschluessel (models3d), unbekannte Namen klein geschrieben
        inv = tuple(weapon_key(w) or str(w).lower() for w in inv) if inv is not None and not isinstance(inv, float) else ()
        inv_out = list(inv) if last_inv.get(i) != inv else None
        last_inv[i] = inv
        out.setdefault(t, []).append([i, round(float(x), 1), round(float(y), 1), round(float(z), 1),
                                      round(_float(r["yaw"]), 1), round(_float(r["pitch"]), 1), int(r["health"] or 0),
                                      bool(r["ducking"]), side, weapon_key(wname) or "", wname,
                                      _int(r["active_weapon_ammo"]), _int(r["armor_value"]),
                                      bool(_float(r["flash_duration"]) > 0), bool(r["is_scoped"]),
                                      _int(r["balance"]), inv_out, bool(r["has_helmet"]), bool(r["has_defuser"]),
                                      _int(r["total_ammo_left"])])
    if hud is not None:
        hud["clock"] = [clock[t] for t in sorted(clock)]
    return out


def _float(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0


def _int(v) -> int:
    return int(_float(v))
