"""Aim-Profil: wie zielst du in Duellen - und was davon kostet dich Duelle?

Die Aim-Spur (aim_trace.py) gibt es je Schluesselmoment. Hier wird sie fuer
JEDEN Kill jeder Demo gerechnet, und zwar fuer beide Seiten: der Schuetze hat
sein Duell gewonnen, das Opfer verloren. Weil das fuer alle Spieler im selben
Durchgang passiert, gibt es die Lobby als Referenz gratis dazu.

Je Duell und Seite (nur ab dem eigenen Sichtkontakt - davor ist der Gegner
nicht zu sehen und Zielen auf ihn kein Zielen):

- ``placement``: Grad zwischen Fadenkreuz und Kopf beim Sichtkontakt
- ``ttt``: Millisekunden bis das Fadenkreuz <= 3 Grad am Kopf liegt (time to target)
- ``never``: das Fadenkreuz kam bis zum Duell-Ende nie so nah
- ``shot_ms``: Millisekunden bis zum ersten Schuss
- ``first_err``: Grad daneben beim ersten Schuss
- ``moving``: erster Schuss in der Bewegung (> 80 u/s, wie Counter-Strafe)
- ``overshoot``: Flick ueber den Kopf hinaus (aim_trace.mistakes)

Sichtkontakt aus ``approximate_spotted_by`` wie key_moments (verzoegert ~0,1 s,
fuer beide Seiten gleich). Statistik ausschliesslich ueber evidence.py.

Vorsicht bei der Deutung "gewonnen gegen verloren": ein verlorenes Duell
endet oft, bevor das Fadenkreuz am Ziel ist - kuerzere Fenster, weniger
Schuesse. Der Vergleich mit der Lobby (gleiche Messung, gleiche Demos) ist
deshalb der fairere; beide werden gezeigt.
"""

from __future__ import annotations

import json
import math
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from . import aim_trace
from .evidence import compare_groups, compare_rates

TICKRATE = 64
WINDOW_TICKS = 4 * TICKRATE         # so weit vor dem Kill wird jeder Tick gelesen
QUIET_TICKS = 32                    # 0,5 s nicht gesehen -> neuer Sichtkontakt (wie key_moments)
ON_TARGET = aim_trace.ON_TARGET_DEG
CAP_DEG, CAP_MS = 90.0, 2000.0      # Ausreisser kappen (Flicks von hinten, sehr lange Duelle)

CACHE_DIR = "aim_cache"
CACHE_KEY = "aim-v2"                # v2: Lage je Duell (wer zuerst, Gegner mit Sicht, allein, reingelaufen)
RESULT_FILE = ".aim_profile.json"
SERIES_MAX = 30                     # so viele Faelle je Wert fuer die Serie im Player
FIELDS = aim_trace.FIELDS + ["approximate_spotted_by", "team_num", "health", "active_weapon_name"]
FIRST_TICKS = 8                     # mehr als 1/8 s frueher gesehen = "zuerst"
ALONE_UNITS = 1000                  # naechster lebender Mitspieler weiter weg = allein (wie death_review)
PEEK_SPEED = 100.0                  # schneller beim ersten Kontakt = in den Kontakt gelaufen
MULTI_WINDOW = 32                   # Gegner mit Sicht auf dich in der letzten halben Sekunde

SNIPERS = {"awp", "ssg08", "g3sg1", "scar20"}
PISTOLS = {"glock", "hkp2000", "usp_silencer", "p250", "deagle", "elite", "fiveseven", "tec9", "cz75a", "revolver"}


def weapon_group(w: str) -> str:
    w = (w or "").lower().removeprefix("weapon_")
    if w in SNIPERS:
        return "sniper"
    if w in PISTOLS:
        return "pistol"
    if not w or "knife" in w or w in ("taser", "hegrenade", "inferno", "molotov", "incgrenade"):
        return "other"
    return "rifle"          # Gewehre, SMGs, Schrot, MG


def _is_human(sid: str) -> bool:
    return sid.startswith("7656") and len(sid) == 17


def _first_contact(ticks: list[int], seen: set[int], end: int) -> int | None:
    start, last = None, None
    for t in ticks:
        if t > end:
            break
        if t in seen:
            if last is None or t - last > QUIET_TICKS:
                start = t
            last = t
    return start


def measure(rows: list[dict], shots: list[dict], hurts: list[dict], me: str, opp: str, end: int) -> dict | None:
    """Messwerte einer Seite eines Duells (None ohne Sichtkontakt).

    ``rows``: Tick-Zeilen beider Spieler (tick, steamid, X, Y, Z, yaw, pitch,
    ducking, spotted_by als Liste von SteamIDs).
    """
    ticks = sorted({int(r["tick"]) for r in rows})
    seen = {int(r["tick"]) for r in rows if str(r["steamid"]) == opp and me in r.get("spotted_by", ())}
    sight = _first_contact(ticks, seen, end)
    if sight is None:
        return None
    tr = aim_trace.trace(rows, shots, hurts, me, opp, sight)
    after = [s for s in tr["series"] if s[0] >= sight]
    if not after:
        return None
    out = {"sight": sight, "placement": min(after[0][3], CAP_DEG), "ttt": None, "never": True,
           "shot_ms": None, "first_err": None, "moving": None, "weapon": "",
           "overshoot": any("überschossen" in m["text"] for m in tr["mistakes"])}
    on = next((s for s in after if s[3] <= ON_TARGET), None)
    if on is not None:
        out["never"] = False
        out["ttt"] = min((on[0] - sight) * 1000 / TICKRATE, CAP_MS)
    first = next((s for s in tr["shots"] if not s["before_sight"] and s["tick"] >= sight - 8), None)
    if first is not None:
        out["shot_ms"] = min(max(first["tick"] - sight, 0) * 1000 / TICKRATE, CAP_MS)
        out["first_err"] = min(first["err"], CAP_DEG)
        out["moving"] = bool(first["moving"])
        out["weapon"] = first.get("weapon", "")
    return out


def scan_demo(demo_path, parser=None) -> list[dict]:
    """Alle Duelle (je Kill zwei Seiten) einer Demo mit Messwerten."""
    from .demo_cache import cached_parser
    p = parser or cached_parser(demo_path)
    deaths = p.parse_event("player_death")
    if deaths is None or not len(deaths):
        return []
    rounds = sorted(int(t) for t in p.parse_event("round_end")["tick"]) if len(p.parse_event("round_end")) else []
    duels = []
    for r in deaths.to_dict("records"):
        a, v = str(r.get("attacker_steamid") or ""), str(r.get("user_steamid") or "")
        if a == v or not (_is_human(a) and _is_human(v)):
            continue
        duels.append((int(r["tick"]), a, v, str(r.get("weapon") or "")))
    if not duels:
        return []
    want = sorted({t for tick, *_ in duels for t in range(max(tick - WINDOW_TICKS, 0), tick + 1)})
    df = p.parse_ticks(FIELDS, ticks=want)
    by_tick: dict[int, dict[str, dict]] = {}
    for row in df.to_dict("records"):
        sp = row.get("approximate_spotted_by")
        row["spotted_by"] = {str(x) for x in sp} if sp is not None and not isinstance(sp, float) else set()
        by_tick.setdefault(int(row["tick"]), {})[str(row["steamid"])] = row
    fire = p.parse_event("weapon_fire")
    hurt = p.parse_event("player_hurt")
    shots_all = sorted(({"tick": int(t), "steamid": str(s), "weapon": str(w).replace("weapon_", "")}
                        for t, s, w in zip(fire["tick"], fire["user_steamid"], fire["weapon"])), key=lambda x: x["tick"]) \
        if len(fire) else []
    hurts_all = [{"tick": int(t), "attacker": str(a), "victim": str(v), "hitgroup": hg, "dmg": int(dm or 0)}
                 for t, a, v, hg, dm in zip(hurt["tick"], hurt["attacker_steamid"], hurt["user_steamid"],
                                            hurt["hitgroup"], hurt["dmg_health"])] if len(hurt) else []
    import bisect
    shot_ticks = [s["tick"] for s in shots_all]
    out = []
    for tick, a, v, kill_weapon in duels:
        lo_t = max(tick - WINDOW_TICKS, 0)
        win = [by_tick[t] for t in range(lo_t, tick + 1) if t in by_tick]
        if not win:
            continue
        side_a = next((x[a].get("team_num") for x in win if a in x), None)
        side_v = next((x[v].get("team_num") for x in win if v in x), None)
        if side_a is not None and side_a == side_v:
            continue            # Teamkill
        i0, i1 = bisect.bisect_left(shot_ticks, lo_t), bisect.bisect_right(shot_ticks, tick)
        shots = [s for s in shots_all[i0:i1] if s["steamid"] in (a, v)]
        hurts = [h for h in hurts_all if lo_t <= h["tick"] <= tick + 2 and {h["attacker"], h["victim"]} == {a, v}]
        rnd = 1 + sum(1 for e in rounds if e < tick)
        for me, opp, won in ((a, v, True), (v, a, False)):
            rows = [x[s] | {"steamid": s} for x in win for s in (me, opp) if s in x]
            m = measure(rows, shots, hurts, me, opp, tick) or dict(EMPTY_AIM)
            m.update(situation(win, me, opp, tick))
            active = next((x[me].get("active_weapon_name") for x in reversed(win) if me in x
                           and isinstance(x[me].get("active_weapon_name"), str)), "")
            from .models3d import weapon_key
            m.update(sid=me, opp=opp, won=won, tick=tick, round=rnd,
                     group=weapon_group(m["weapon"] or weapon_key(active) or (kill_weapon if won else "")))
            out.append(m)
    return out


EMPTY_AIM = {"sight": None, "placement": None, "ttt": None, "never": None, "shot_ms": None,
             "first_err": None, "moving": None, "weapon": "", "overshoot": None}


def situation(win: list[dict], me: str, opp: str, end: int) -> dict:
    """Lage eines Duells aus Sicht von ``me`` (Tick-Zeilen aller Spieler im Fenster).

    ``he_first``: er sah dich > 1/8 s frueher (oder du sahst ihn gar nicht),
    ``i_first``: umgekehrt, ``blind``: du sahst ihn vor dem Kill nie,
    ``multi``: mindestens zwei Gegner hatten dich in der letzten halben Sekunde im Blick,
    ``alone``: naechster lebender Mitspieler > 1000 Einheiten (beim Kill),
    ``peek``: beim ersten Kontakt schneller als 100 u/s.
    """
    ticks = [int(next(iter(x.values()))["tick"]) for x in win]
    i_see = {t for t, x in zip(ticks, win) if opp in x and me in x[opp].get("spotted_by", ())}
    he_sees = {t for t, x in zip(ticks, win) if me in x and opp in x[me].get("spotted_by", ())}
    mine, his = _first_contact(ticks, i_see, end), _first_contact(ticks, he_sees, end)
    out = {"blind": mine is None, "i_first": False, "he_first": False, "multi": False, "alone": None, "peek": None}
    if mine is None:
        out["he_first"] = his is not None
    elif his is None or mine < his - FIRST_TICKS:
        out["i_first"] = True
    elif his < mine - FIRST_TICKS:
        out["he_first"] = True
    my_team = next((x[me].get("team_num") for x in win if me in x), None)
    enemies = set()
    for t, x in zip(ticks, win):
        if t >= end - MULTI_WINDOW and me in x:
            enemies |= {s for s in x[me].get("spotted_by", ()) if s in x and x[s].get("team_num") != my_team}
    out["multi"] = len(enemies) >= 2
    last = next((x for x in reversed(win) if me in x), None)
    if last is not None and my_team is not None:
        mx, my = last[me].get("X"), last[me].get("Y")
        dists = [math.hypot(r["X"] - mx, r["Y"] - my) for s, r in last.items()
                 if s != me and r.get("team_num") == my_team and (r.get("health") or 0) > 0
                 and r.get("X") is not None and math.isfinite(r["X"])]
        out["alone"] = not dists or min(dists) > ALONE_UNITS
    contact = min([t for t in (mine, his) if t is not None], default=None)
    if contact is not None:
        prev = next((x for t, x in zip(ticks, win) if contact - 8 <= t < contact and me in x), None)
        cur = next((x for t, x in zip(ticks, win) if t >= contact and me in x), None)
        if prev and cur:
            dt = (int(cur[me]["tick"]) - int(prev[me]["tick"])) or 1
            out["peek"] = math.hypot(cur[me]["X"] - prev[me]["X"], cur[me]["Y"] - prev[me]["Y"]) * TICKRATE / dt > PEEK_SPEED
    return out


# ── Sammeln ueber alle Demos (je Demo zwischengespeichert, wie winprob) ──────
def collect(cfg: dict, config_path: Path, max_new: int = 500, log=lambda *_: None, stats: dict | None = None) -> list[dict]:
    """Alle Duell-Seiten aller Demos: ``[{..., "demo": stem}]``."""
    folder = (cfg.get("demo_folder") or "").strip()
    if not folder or not Path(folder).is_dir():
        return []
    from .users import shared_dir
    cache_dir = shared_dir(config_path) / CACHE_DIR   # Duelle aller Spieler: einmal fuer alle
    out, new, waiting = [], 0, 0
    for demo in sorted(Path(folder).glob("*.dem")):
        cache = cache_dir / f"{demo.stem}.json"
        try:
            known = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            known = {}
        if CACHE_KEY not in known:
            if new >= max_new:
                waiting += 1
                continue
            try:
                known = {CACHE_KEY: scan_demo(demo)}
            except Exception as e:  # noqa: BLE001 - kaputte Demo: naechste
                log(f"[aim] {demo.name}: {e}")
                continue
            new += 1
            cache_dir.mkdir(exist_ok=True)
            cache.write_text(json.dumps(known), encoding="utf-8")
        out += [d | {"demo": demo.stem} for d in known[CACHE_KEY]]
    if stats is not None:
        stats.update(new=new, waiting=waiting)
    return out


# ── Auswertung ───────────────────────────────────────────────────────────────
#: (Schluessel, Titel, Einheit) - Werte, bei denen weniger besser ist
VALUES = (("placement", "Fadenkreuz beim Sichtkontakt", "°"),
          ("ttt", "Zeit bis aufs Ziel (wenn nötig)", "ms"),
          ("shot_ms", "Zeit bis zum ersten Schuss", "ms"),
          ("first_err", "Daneben beim ersten Schuss", "°"))
#: (Schluessel, Titel) - Anteile, bei denen weniger besser ist
RATES = (("never", "Nie aufs Ziel gekommen"),
         ("moving", "Erster Schuss in Bewegung"),
         ("overshoot", "Flick überschossen"))
GROUPS = {"rifle": "Gewehre & SMGs", "pistol": "Pistolen", "sniper": "Scharfschützen", "all": "Alle Waffen"}


@dataclass
class Row:
    key: str
    title: str
    unit: str
    me: float | None            # Mittel (Werte) bzw. Anteil in % (Quoten)
    lobby: float | None
    won: float | None
    lost: float | None
    vs_lobby: dict | None       # evidence.Comparison als dict, A = du, B = Lobby (positiv = du besser)
    vs_lost: dict | None        # A = deine gewonnenen, B = deine verlorenen Duelle
    n_me: int = 0


def _value(d: dict, key: str):
    """Messwert oder None. Zeit bis aufs Ziel nur, wenn nachgezogen werden musste: lag das
    Fadenkreuz schon beim Sichtkontakt am Kopf (die meisten Duelle), waere sie 0 ms und
    wuerde den Wert zur Placement-Messung machen."""
    if key == "ttt" and (d.get("placement") or 0) <= ON_TARGET:
        return None
    return d.get(key)


def _cmp(c) -> dict | None:
    return None if c is None else asdict(c) | {"note": c.note("Duelle")}


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    k = len(s) // 2
    return round(s[k] if len(s) % 2 else (s[k - 1] + s[k]) / 2, 1)


def profile(duels: list[dict], me: str, group: str = "rifle") -> list[Row]:
    """Dein Aim gegen die Lobby und gewonnene gegen verlorene Duelle.

    Jede Zeile ist ein Kandidat aus mehreren - comparisons = Anzahl Zeilen
    (Bonferroni), wie duel_value bei den Zonen.
    """
    pool = [d for d in duels if group == "all" or d.get("group") == group]
    mine = [d for d in pool if d["sid"] == me]
    lobby = [d for d in pool if d["sid"] != me]
    k = len(VALUES) + len(RATES)
    rows = []
    for key, title, unit in VALUES:
        a = [d[key] for d in mine if _value(d, key) is not None]
        b = [d[key] for d in lobby if _value(d, key) is not None]
        w = [d[key] for d in mine if _value(d, key) is not None and d["won"]]
        lo = [d[key] for d in mine if _value(d, key) is not None and not d["won"]]
        rows.append(Row(key, title, unit, _median(a), _median(b), _median(w), _median(lo),
                        _cmp(compare_groups(a, b, higher_is_better=False, comparisons=k)),
                        _cmp(compare_groups(w, lo, higher_is_better=False, comparisons=k)), len(a)))
    for key, title in RATES:
        def rate(ds):
            n = [d for d in ds if d.get(key) is not None]
            return sum(1 for d in n if d[key]), len(n)
        ka, na = rate(mine)
        kb, nb = rate(lobby)
        kw, nw = rate([d for d in mine if d["won"]])
        kl, nl = rate([d for d in mine if not d["won"]])
        pct = lambda x, n: round(100 * x / n, 1) if n else None  # noqa: E731
        # weniger ist besser: Quoten tauschen, damit positiv = A besser
        vl = compare_rates(kb, nb, ka, na, comparisons=k)
        vw = compare_rates(kl, nl, kw, nw, comparisons=k)
        rows.append(Row(key, title, "%", pct(ka, na), pct(kb, nb), pct(kw, nw), pct(kl, nl),
                        _flip(vl), _flip(vw), na))
    return rows


def _flip(c) -> dict | None:
    """compare_rates(B, A) -> Sicht A gegen B (Mittel und Gruppen zurueckgetauscht)."""
    if c is None:
        return None
    d = _cmp(c)
    d["n_a"], d["n_b"] = d["n_b"], d["n_a"]
    d["mean_a"], d["mean_b"] = d["mean_b"], d["mean_a"]
    return d


#: Lagen: (Schluessel, Titel, Richtung) - "less": seltener ist besser, "more": haeufiger ist besser,
#: "neutral": weder noch (in den Kontakt laufen kann richtig sein)
SITUATIONS = (("he_first", "Er sah dich zuerst", "less"),
              ("blind", "Gegner vor dem Tod/Kill nie gesehen", "less"),
              ("i_first", "Du sahst ihn zuerst", "more"),
              ("multi", "Zwei oder mehr Gegner mit Sicht auf dich", "less"),
              ("alone", "Allein (nächster Mitspieler > 1000 u)", "less"),
              ("peek", "In den Kontakt gelaufen (> 100 u/s)", "neutral"))


@dataclass
class Situation:
    key: str
    title: str
    direction: str
    share_me: float | None       # Anteil deiner Duelle in dieser Lage (%)
    share_lobby: float | None
    share_cmp: dict | None       # A = du, B = Lobby; positiv = fuer dich guenstiger (bei neutral: haeufiger)
    win_me: float | None         # Duelle in dieser Lage gewonnen (%)
    win_lobby: float | None
    win_cmp: dict | None         # A = du, B = Lobby; positiv = du gewinnst sie oefter
    n_me: int = 0


def situations(duels: list[dict], me: str, group: str = "rifle") -> tuple[list[Situation], dict | None]:
    """Wie oft bist du in welcher Lage - und wie oft gewinnst du sie - gegen die Lobby.

    Bonferroni ueber alle 2 x len(SITUATIONS) Vergleiche. Zusaetzlich deine
    Duell-Siegquote insgesamt gegen die Lobby (Zusammenhang, als Rahmen).
    """
    pool = [d for d in duels if group == "all" or d.get("group") == group]
    mine = [d for d in pool if d["sid"] == me]
    lobby = [d for d in pool if d["sid"] != me]
    k = 2 * len(SITUATIONS)
    pct = lambda x, n: round(100 * x / n, 1) if n else None  # noqa: E731
    out = []
    for key, title, direction in SITUATIONS:
        mi = [d for d in mine if d.get(key) is not None]
        lo = [d for d in lobby if d.get(key) is not None]
        km, kl = sum(1 for d in mi if d[key]), sum(1 for d in lo if d[key])
        if direction == "less":
            share = _flip(compare_rates(kl, len(lo), km, len(mi), comparisons=k))
        else:
            share = _cmp(compare_rates(km, len(mi), kl, len(lo), comparisons=k))
        wm = [d for d in mi if d[key]]
        wl = [d for d in lo if d[key]]
        win = _cmp(compare_rates(sum(1 for d in wm if d["won"]), len(wm),
                                 sum(1 for d in wl if d["won"]), len(wl), comparisons=k))
        out.append(Situation(key, title, direction, pct(km, len(mi)), pct(kl, len(lo)), share,
                             pct(sum(1 for d in wm if d["won"]), len(wm)), pct(sum(1 for d in wl if d["won"]), len(wl)),
                             win, len(mi)))
    overall = _cmp(compare_rates(sum(1 for d in mine if d["won"]), len(mine),
                                 sum(1 for d in lobby if d["won"]), len(lobby)))
    return out, overall


def examples(duels: list[dict], me: str, key: str, group: str = "rifle", top: int = 6) -> list[dict]:
    """Deine deutlichsten Faelle zu einem Wert (zum Ansehen im Player), neueste Demos zuerst."""
    pool = [d for d in duels if d["sid"] == me and (group == "all" or d.get("group") == group)]
    if key in {k for k, _, _ in SITUATIONS}:
        # Lagen: deine verlorenen Duelle in dieser Lage (dort lohnt das Hinsehen)
        hits = [d for d in pool if d.get(key) and not d["won"]]
        return sorted(hits, key=lambda d: (d["demo"], d["tick"]), reverse=True)[:top]
    if key in dict((k, 1) for k, _ in RATES):
        hits = [d for d in pool if d.get(key)]
        return sorted(hits, key=lambda d: (d["demo"], d["tick"]), reverse=True)[:top]
    vals = [d for d in pool if _value(d, key) is not None]
    return sorted(vals, key=lambda d: d[key], reverse=True)[:top]


# ── Ergebnis fuer die Seite: im Hintergrund rechnen, als Datei merken ────────
_job = {"running": False}
_job_lock = threading.Lock()


def run(cfg: dict, config_path: Path, max_new: int = 500) -> dict:
    me = str(cfg.get("steam_id") or "")
    stats: dict = {}
    duels = collect(cfg, config_path, max_new=max_new, stats=stats)
    demos = sorted({d["demo"] for d in duels if d["sid"] == me})
    res = {"at": datetime.now().isoformat(timespec="minutes"), "me": me, "demos": len(demos),
           "duels": sum(1 for d in duels if d["sid"] == me), "lobby": sum(1 for d in duels if d["sid"] != me),
           "waiting": stats.get("waiting", 0), "groups": {}}
    for g in GROUPS:
        rows = profile(duels, me, g)
        sits, overall = situations(duels, me, g)
        ex = {r.key: examples(duels, me, r.key, g, top=SERIES_MAX) for r in rows}
        ex.update({x.key: examples(duels, me, x.key, g, top=SERIES_MAX) for x in sits})
        res["groups"][g] = {"rows": [asdict(r) for r in rows], "situations": [asdict(x) for x in sits],
                            "overall": overall, "examples": ex}
    res["exports"] = _export_files(cfg, {d["demo"] for g in res["groups"].values()
                                         for ex in g["examples"].values() for d in ex})
    (Path(config_path).parent / RESULT_FILE).write_text(json.dumps(res), encoding="utf-8")
    return res


def _export_files(cfg: dict, stems: set[str]) -> dict[str, str]:
    """Demo-Stamm -> Export-Datei (fuer Links in den Player); nur die gebrauchten."""
    from .report_ai import _exports
    out = {}
    for f in _exports(cfg):
        if len(out) == len(stems):
            break
        try:
            demo = (json.loads(f.read_text(encoding="utf-8")).get("match") or {}).get("demo_file") or ""
        except (OSError, ValueError):
            continue
        stem = Path(demo).stem
        if stem in stems:
            out[stem] = f.name
    return out


def load(config_path: Path) -> dict | None:
    try:
        return json.loads((Path(config_path).parent / RESULT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def start(cfg: dict, config_path: Path) -> bool:
    """Neue Demos im Hintergrund einlesen und das Profil neu rechnen (einmal gleichzeitig)."""
    with _job_lock:
        if _job["running"]:
            return False
        _job["running"] = True

    def work():
        try:
            run(cfg, config_path)
        except Exception:  # noqa: BLE001 - Seite zeigt weiter das letzte Ergebnis
            pass
        finally:
            _job["running"] = False
    threading.Thread(target=work, name="aim-profile", daemon=True).start()
    return True


def running() -> bool:
    return _job["running"]
