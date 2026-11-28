"""Woran lag es? Verdichtet eine fertige Moment-Szene zu Ursachen und Wendepunkten.

Reine Funktion ueber das Szenen-dict aus ``moments_page.build_clip`` (Duell-Fakten,
``moment.ctx`` aus key_moments.annotate, Aim-Spur beider Seiten, Blendungen,
Granaten, Kills). Keine neuen Schwellwerte: die Toene (good/bad/info) stammen aus
key_moments/aim_trace, die dieselben Saetze schon in den Notizen zeigen. Das ist
eine Beschreibung EINER Szene, keine Statistik - Muster ueber viele Duelle misst
duel_value/aim_profile ueber evidence.py.

Ausgabe ``{"verdict": str, "tone": str, "causes": [...], "turns": [...]}``:
- causes: ``{"key", "tone", "label", "text", "tick"|None}``, wichtigste zuerst
- turns: Wendepunkte fuer Zeitstrahl/3D ``{"tick", "kind", "label", "tone"}``
"""

from __future__ import annotations

from .key_moments import TICKRATE, TRADE_SECONDS

MAX_CAUSES = 4

# Reihenfolge = Prioritaet beim Tod: was haette man selbst anders machen koennen, zuerst
_ORDER = ["info", "blind", "placement", "aim", "move", "reaction", "crowd", "trade", "damage"]


def _ms(ticks: int) -> int:
    return round(ticks * 1000 / TICKRATE)


def _hits(shots: list[dict] | None) -> list[dict]:
    return [s for s in shots or [] if s.get("hit")]


def summarize(scene: dict, kind: str = "death") -> dict:
    """Ursachen + Wendepunkte einer Szene (alte Szenen ohne Felder: leere Teile)."""
    mo = scene.get("moment") or {}
    facts = mo.get("facts") or {}
    ctx = mo.get("ctx") or {}
    is_kill = kind == "kill" or mo.get("kind") == "kill"
    players = scene.get("players") or []
    me_idx, foe_idx = mo.get("me_idx"), mo.get("killer_idx")
    foe = players[foe_idx]["name"] if foe_idx is not None and foe_idx < len(players) else "Gegner"
    me_sid = players[me_idx]["steamid"] if me_idx is not None and me_idx < len(players) else ""
    foe_sid = mo.get("killer") or ""
    t_end = mo.get("death_tick")
    causes: list[dict] = []
    turns: list[dict] = []

    def cause(key, tone, label, text, tick=None):
        causes.append({"key": key, "tone": tone, "label": label, "text": text, "tick": tick})

    # Info: wer sah wen zuerst (duel_notes: > 8 Ticks = frueher)
    mine, his = facts.get("my_sight"), facts.get("his_sight")
    if his is not None:
        turns.append({"tick": his, "kind": "sight_his", "label": f"{foe} sieht dich", "tone": "bad"})
    if mine is not None:
        turns.append({"tick": mine, "kind": "sight_me", "label": f"Du siehst {foe}", "tone": "info"})
    if mine is None and his is not None and not is_kill:
        cause("info", "bad", "Nicht gesehen", f"{foe} hatte dich im Blick, du ihn bis zum Tod nie.", his)
    elif mine is not None and his is not None:
        d = mine - his
        if d > 8:
            cause("info", "bad", f"{_ms(d)} ms zu spät gesehen", f"{foe} sah dich {_ms(d)} ms früher als du ihn.", his)
        elif d < -8:
            cause("info", "good", f"{_ms(-d)} ms Infovorsprung", f"Du sahst {foe} {_ms(-d)} ms früher.", mine)

    # Geblendet im Moment selbst (player_blind, hud.blinds = [Spieler, von, bis])
    for b in ((scene.get("hud") or {}).get("blinds") or []):
        if t_end is None or len(b) < 3:
            continue
        if b[0] == me_idx and b[1] <= t_end <= b[2] + 8 and not is_kill:
            cause("blind", "bad", "Geblendet", "Du warst im Duell geblendet.", b[1])
            turns.append({"tick": b[1], "kind": "blind", "label": "Du bist geblendet", "tone": "bad"})
        elif b[0] == foe_idx and b[1] <= t_end <= b[2] + 8:
            cause("blind", "good", "Gegner geblendet", f"{foe} war im Duell geblendet.", b[1])
            turns.append({"tick": b[1], "kind": "blind", "label": f"{foe} geblendet", "tone": "good"})

    # Fadenkreuz beim Sichtkontakt (duel_notes: <= 5 gut, > 20 schlecht)
    off = facts.get("offset")
    if off is not None and abs(off) > 20:
        cause("placement", "bad", f"Fadenkreuz {abs(off):.0f}° daneben",
              f"Beim ersten Sichtkontakt lag dein Fadenkreuz {abs(off):.0f}° {'rechts' if off > 0 else 'links'} neben {foe}.", mine)
    elif off is not None and abs(off) <= 5:
        cause("placement", "good", "Fadenkreuz vorgelegt", f"Dein Fadenkreuz lag beim Sichtkontakt schon auf {foe}.", mine)

    # Aim-Fehler aus aim_trace.mistakes (nur die als schlecht markierten)
    aim = scene.get("aim") or {}
    for m in aim.get("mistakes") or []:
        if m.get("tone") != "bad":
            continue
        txt = m.get("text", "")
        key = "move" if "Bewegung" in txt else "aim"
        if any(c["key"] == key for c in causes):
            continue
        label = "Schuss in Bewegung" if key == "move" else ("Flick überschossen" if "überschossen" in txt
                                                            else "Erster Schuss daneben" if "verfehlt" in txt else "Aim nicht auf dem Kopf")
        cause(key, "bad", label, txt, m.get("tick"))

    # Reaktion: erster Schuss nach Sichtkontakt (dein Wert gegen seinen in DIESER Szene)
    my_shot, his_shot = facts.get("my_shot"), facts.get("his_shot")
    if my_shot is not None:
        turns.append({"tick": my_shot, "kind": "shot", "label": "Dein erster Schuss", "tone": "info"})
    if his_shot is not None:
        turns.append({"tick": his_shot, "kind": "shot_his", "label": f"{foe} schießt", "tone": "bad"})
    if mine is not None and my_shot is None and not is_kill:
        cause("reaction", "bad", "Nicht geschossen", "Zwischen Sichtkontakt und Tod kam kein Schuss von dir.", mine)
    elif None not in (mine, his, my_shot, his_shot):
        a, b = my_shot - mine, his_shot - his
        if a - b > 8 and not is_kill:
            cause("reaction", "bad", f"{_ms(a - b)} ms langsamer", f"Dein erster Schuss kam {_ms(a)} ms nach Sichtkontakt, seiner nach {_ms(b)} ms.", my_shot)
        elif b - a > 8:
            cause("reaction", "good", f"{_ms(b - a)} ms schneller", f"Dein erster Schuss kam {_ms(a)} ms nach Sichtkontakt, seiner nach {_ms(b)} ms.", my_shot)

    # Position/Team (annotate: >= 2 Gegner im Umkreis, Mitspieler > FAR_MATE_UNITS, Trade-Fenster)
    if ctx.get("near_enemies", 0) >= 2 and not is_kill:
        cause("crowd", "bad", f"{ctx['near_enemies']} Gegner in Reichweite",
              f"{ctx['near_enemies']} Gegner konnten dich gleichzeitig treffen.", t_end)
    if ctx.get("untraded") and ctx.get("mate_far"):
        cause("trade", "bad", f"Allein ({ctx.get('mate_m')} m)", f"{ctx.get('mate_name', 'Mitspieler')} stand {ctx.get('mate_m')} m weg – kein Trade möglich.", t_end)
    elif ctx.get("untraded"):
        cause("trade", "bad", "Nicht getradet", f"Dein Team hat den Tod nicht innerhalb von {TRADE_SECONDS} s gerächt.", t_end)
    elif ctx.get("trade_tick") is not None:
        cause("trade", "good", f"Getradet ({ctx['trade_s']:.1f} s)", f"Dein Team hat nach {ctx['trade_s']:.1f} s nachgezogen.", ctx["trade_tick"])
        turns.append({"tick": ctx["trade_tick"], "kind": "trade", "label": "Trade", "tone": "good"})

    # Schaden im Duell: deine Treffer gegen seine (aim / aim_enemy)
    mine_h, his_h = _hits(aim.get("shots")), _hits((scene.get("aim_enemy") or {}).get("shots"))
    dealt, taken = sum(int(s.get("dmg") or 0) for s in mine_h), sum(int(s.get("dmg") or 0) for s in his_h)
    if mine_h:
        top = max(mine_h, key=lambda s: s.get("dmg") or 0)
        turns.append({"tick": top["tick"], "kind": "hit", "label": f"Dein Treffer −{top.get('dmg') or 0} HP", "tone": "good"})
    if his_h:
        top = max(his_h, key=lambda s: s.get("dmg") or 0)
        turns.append({"tick": top["tick"], "kind": "hit_taken", "label": f"Treffer −{top.get('dmg') or 0} HP", "tone": "bad"})
    if mine_h or his_h:
        tone = "good" if dealt >= taken else "bad"
        if is_kill or tone == "bad" or dealt:
            cause("damage", tone, f"Schaden {dealt} : {taken}", f"Du hast {foe} {dealt} Schaden gemacht, er dir {taken}.", None)

    # Granaten der beiden Duellanten in der Szene (Abwurf = erster Bahnpunkt)
    names = {"smoke": "Smoke", "flash": "Flash", "he": "HE", "molotov": "Molotov", "incgrenade": "Molotov", "decoy": "Decoy"}
    for p in ((scene.get("nades") or {}).get("paths") or []):
        pts = p.get("pts") or []
        if not pts or p.get("sid") not in (me_sid, foe_sid) or not p.get("sid"):
            continue
        who = "Deine" if p["sid"] == me_sid else f"{foe}s"
        turns.append({"tick": int(pts[0][0]), "kind": "nade", "tone": "info",
                      "label": f"{who} {names.get(p.get('type'), p.get('type', 'Granate'))}"})

    if t_end is not None:
        turns.append({"tick": t_end, "kind": "kill" if is_kill else "death",
                      "label": f"Du tötest {foe}" if is_kill else f"Tod durch {foe}", "tone": "good" if is_kill else "bad"})

    want = "good" if is_kill else "bad"
    causes.sort(key=lambda c: (c["tone"] != want, _ORDER.index(c["key"]) if c["key"] in _ORDER else 99))
    causes = causes[:MAX_CAUSES]
    lead = next((c for c in causes if c["tone"] == want), None)
    if lead:
        verdict = ("Gewonnen durch: " if is_kill else "Hauptgrund: ") + lead["label"]
    else:
        verdict = "Sauberes Duell – kein klarer Fehler erkennbar" if not is_kill else "Kill ohne klaren Vorteil"
    turns.sort(key=lambda t: t["tick"])
    return {"verdict": verdict, "tone": want if lead else "info", "causes": causes, "turns": turns}
