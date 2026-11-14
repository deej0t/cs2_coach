"""Fokus-Dashboard (Startseite): was heute zaehlt, nicht alles auf einmal.

Oben steht das Premier-Rating (Wunsch 13.11.: "es spricht mich nicht an" -
die Zahl, die einen Spieler interessiert, fehlte). Es ist reine Anzeige und
Motivation: es steigt durch Siege, ist also Ergebnis und kein Hebel - kein
Ratschlag wird daraus abgeleitet, das Coaching bleibt beim Ziel.

Ersetzt am 13.11. das alte Dashboard (12 Bloecke, ~4 Bildschirmhoehen).
Dessen Warnungen ("KAST sinkt: 67,9 % vs 72,7 %"), Skill-Benchmarks gegen
"Gold Nova" und Map-"BAN" aus 13 Matches waren rohe Mittelwertvergleiche
mit festen Schwellen - genau das, was CLAUDE.md verbietet. Hier steht nur:

- das letzte Match mit dem Anfang der KI-Nachbesprechung,
- das aktuelle Trainingsziel mit Fortschritt (training.current_plan, die
  gleiche Quelle wie das Tages-Briefing - inklusive evidence-Urteil),
- Form, letzte Matches und Maps als reine Messung ohne Urteilsfarben.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from . import report_ai
from .training import EARLY_KEY, MIN_AFTER, current_plan
from .utility_analysis import early_utility

RECENT = 5
TILES = 6
WEEK_DAYS = 7
CURVE = 30
INTRO_CHARS = 280
RADARS = Path(__file__).parent / "web" / "static" / "radars"

#: Premier-Stufen wie im Spiel (Untergrenze, Farbe, Name). Die Farbwerte sind
#: den bekannten CS2-Stufenfarben nachempfunden, nicht aus dem Spiel ausgelesen.
TIERS = [(0, "#b0c3d9", "Grau"), (5000, "#5e98d9", "Hellblau"), (10000, "#4b69ff", "Blau"),
         (15000, "#8847ff", "Lila"), (20000, "#d32ce6", "Pink"), (25000, "#eb4b4b", "Rot"),
         (30000, "#e4ae39", "Gold")]

_cache: dict = {"key": None, "exports": []}


def _load(cfg: dict) -> tuple[list[Path], list[dict]]:
    """Alle Exporte in Spielreihenfolge, gecacht auf Anzahl + neueste mtime."""
    files = report_ai._exports(cfg)
    key = (len(files), max((f.stat().st_mtime for f in files), default=0))
    if _cache["key"] != key:
        data = []
        for f in files:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            d["_file"] = f.name
            data.append(d)
        _cache.update(key=key, exports=data)
    return files, _cache["exports"]


def _intro(text: str | None) -> str:
    """Die ersten Saetze der Nachbesprechung, an einer Satzgrenze gekuerzt."""
    if not text:
        return ""
    flat = re.sub(r"\s+", " ", re.sub(r"[*_#`]", "", text)).strip()
    if len(flat) <= INTRO_CHARS:
        return flat
    cut = flat[:INTRO_CHARS]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > 80 else cut.rstrip() + " …"


def de(v: float, sign: bool = False) -> str:
    """15387 -> "15.387", mit sign "+1.934" / "−415"."""
    txt = f"{abs(v):,.0f}".replace(",", ".")
    if not sign:
        return txt if v >= 0 else "−" + txt
    return ("+" if v > 0 else "−" if v < 0 else "±") + txt


def _short(day: str) -> str:
    """2026-11-12 -> 12.11."""
    return f"{day[8:10]}.{day[5:7]}." if len(day) >= 10 else day


def _greeting(now: datetime, name: str) -> str:
    h = now.hour
    text = ("Guten Morgen" if 5 <= h < 11 else "Hallo" if h < 17 else "Guten Abend" if h < 23 else "Späte Runde")
    return f"{text}, {name}" if name else text


def _week(exports: list[dict], now: datetime) -> dict | None:
    """Letzte WEEK_DAYS Tage: Matches, Bilanz, Premier-Aenderung. None ohne Match."""
    since = (now - timedelta(days=WEEK_DAYS)).strftime("%Y-%m-%d %H:%M")
    recent = [e for e in exports if ((e.get("match") or {}).get("datetime") or "") >= since]
    if not recent:
        return None
    res = [(e.get("match") or {}).get("result") for e in recent]
    delta = sum(float(((e.get("player") or {}).get("rank") or {}).get("change") or 0) for e in recent
                if (((e.get("player") or {}).get("rank") or {}).get("new") or 0) >= 1000)
    return {"n": len(recent), "w": res.count("Sieg"), "l": res.count("Niederlage"),
            "delta": de(delta, True) if delta else "", "up": delta > 0}


def _rounds(e: dict) -> list[dict]:
    """Rundenverlauf des Matches: Sieg/Niederlage, Seite, eigene Kills - Lueckenmarke nach jeder Halbzeit."""
    out = []
    for r in e.get("round_timeline") or []:
        n = int(r.get("round", 0))
        kills = int(r.get("player_kills") or 0)
        out.append({
            "n": n, "won": bool(r.get("won")), "side": r.get("side", ""),
            "gap": n in (12, 24, 27, 30, 33, 36),
            "title": f"Runde {n} ({r.get('side', '')}): {'gewonnen' if r.get('won') else 'verloren'}"
                     + (f", {kills} Kill{'s' if kills != 1 else ''}" if kills else "")
                     + (", gestorben" if r.get("player_died") else ""),
            "kills": kills,
        })
    return out


def _ago(seconds: float) -> str:
    if seconds < 60:
        return f"vor {int(seconds)} s"
    if seconds < 3600:
        return f"vor {int(seconds // 60)} Min."
    if seconds < 86400:
        return f"vor {int(seconds // 3600)} Std."
    days = int(seconds // 86400)
    return f"vor {days} Tag{'en' if days != 1 else ''}"


def _live(config_path: Path, now: float) -> dict:
    """Live-Coach: letztes GSI-Signal und das zuletzt live gezaehlte Match."""
    from .live_coach import MATCHES_DIR, SIGNAL_FILE
    base = Path(config_path).parent
    out: dict = {"connected": False, "ago": None, "last": None}
    try:
        age = now - (base / SIGNAL_FILE).stat().st_mtime
        out.update(connected=age < 30, ago=_ago(age))
    except OSError:
        pass
    files = sorted((base / MATCHES_DIR).glob("*.json")) if (base / MATCHES_DIR).is_dir() else []
    if files:
        try:
            m = json.loads(files[-1].read_text(encoding="utf-8"))
            summ = m.get("summary") or {}
            out["last"] = {"map": str(m.get("map", "")).removeprefix("de_").capitalize(),
                           "date": _short(str(m.get("started", ""))[:10]),
                           "early": summ.get("early"), "full_buys": summ.get("full_buys")}
        except (OSError, ValueError):
            pass
    return out


def _problems(cfg: dict, exports: list[dict]) -> list[str]:
    """Hinweise, die man sonst nicht bemerkt - heute: Demos ohne Analyse (12.11.)."""
    from .health import WARN, check_unanalyzed
    known = {(e.get("match") or {}).get("demo_file") for e in exports}
    c = check_unanalyzed(cfg, known=known)
    return [c.message] if c.state == WARN else []


def _radar(map_name: str) -> str | None:
    key = (map_name or "").lower()
    return key if (RADARS / f"{key}.png").exists() else None


def _premier(exports: list[dict]) -> dict | None:
    """Premier-Rating, Stufe und Kurve der letzten CURVE Matches. None ohne Premier-Daten.

    Wettkampf-Ranks je Map sind kleine Zahlen (0-18), Premier-Werte vierstellig.
    """
    pts = []
    for e in exports:
        r = (e.get("player") or {}).get("rank") or {}
        m = e.get("match") or {}
        if (r.get("new") or 0) >= 1000:
            pts.append({"v": int(r["new"]), "ch": float(r.get("change") or 0), "map": m.get("map", ""),
                        "date": (m.get("datetime") or m.get("date") or "")[:10], "result": m.get("result", ""),
                        "title": f"{(m.get('datetime') or '')[:10]} {m.get('map', '')} "
                                 f"{m.get('score_own', '')}:{m.get('score_enemy', '')}: "
                                 f"{de(int(r['new']))} ({de(float(r.get('change') or 0), True)})"})
    if len(pts) < 2:
        return None
    now = pts[-1]["v"]
    lo, color, name = [t for t in TIERS if now >= t[0]][-1]
    nxt = next((t for t in TIERS if t[0] > now), None)
    curve = pts[-CURVE:]
    start = curve[0]["v"] - curve[0]["ch"]

    # Kurve: SVG-Koordinaten hier, damit das Template nur zeichnet
    w, h, pad = 640, 170, 10
    vals = [p["v"] for p in curve]
    vmin, vmax = min(vals) - 150, max(vals) + 150
    x = [pad + i * (w - 2 * pad) / max(len(vals) - 1, 1) for i in range(len(vals))]
    y = [h - pad - (v - vmin) * (h - 2 * pad) / (vmax - vmin) for v in vals]
    line = " ".join(f"{a:.1f},{b:.1f}" for a, b in zip(x, y))
    refs = [{"y": round(h - pad - (t - vmin) * (h - 2 * pad) / (vmax - vmin), 1), "label": de(t)}
            for t, _, _ in TIERS if vmin < t < vmax]
    return {
        "value": de(now), "change": pts[-1]["ch"], "change_txt": de(pts[-1]["ch"], True),
        "delta": de(now - start, True), "n": len(curve), "peak": de(max(p["v"] for p in pts)),
        "color": color, "tier": name,
        "next": ({"name": nxt[2], "at": de(nxt[0]), "missing": de(nxt[0] - now), "color": nxt[1],
                  "fill": round((now - lo) / (nxt[0] - lo) * 100)} if nxt else None),
        "w": w, "h": h, "line": line, "area": f"{x[0]:.1f},{h} {line} {x[-1]:.1f},{h}", "refs": refs,
        "dots": [{"x": round(a, 1), "y": round(b, 1), "title": p["title"], "last": i == len(curve) - 1,
                  "res": {"Sieg": "w", "Niederlage": "l"}.get(p["result"], "d")}
                 for i, (a, b, p) in enumerate(zip(x, y, curve))],
        "first_date": _short(curve[0]["date"]), "last_date": _short(curve[-1]["date"]),
    }


def _tile(e: dict) -> dict:
    row = _row(e)
    ch = ((e.get("player") or {}).get("rank") or {}).get("change")
    row["change"] = float(ch) if ch is not None and ((e.get("player") or {}).get("rank") or {}).get("new", 0) >= 1000 else None
    row["change_txt"] = de(row["change"], True) if row["change"] is not None else ""
    row["radar"] = _radar(row["map"])
    return row


def _row(e: dict) -> dict:
    m, p = e.get("match") or {}, e.get("player") or {}
    early = early_utility(e)
    return {
        "file": e.get("_file", ""),
        "date": (m.get("datetime") or m.get("date") or "")[:16],
        "map": m.get("map", "?"),
        "score": f"{m.get('score_own', '?')}:{m.get('score_enemy', '?')}",
        "result": m.get("result", ""),
        "rating": p.get("rating"),
        "adr": p.get("adr"),
        "kd": p.get("kd"),
        "early": early if early and early[1] >= 1 else None,
    }


def _goal(plan: dict, exports: list[dict]) -> dict | None:
    goals = plan.get("goals") or []
    if not goals:
        return None
    g = goals[0]
    change = g.change or {}
    out = {
        "label": g.label, "unit": g.unit, "target": g.target, "recent": g.recent, "loss": g.loss,
        "since": (g.since or "")[:10], "matches_since": g.matches_since, "note": g.note,
        "verdict": change.get("verdict"), "why": g.why, "early": g.key == EARLY_KEY,
        "fill": 0, "checks": [],
    }
    if g.recent is not None and g.target:
        frac = g.recent / g.target if g.higher_is_better else g.target / max(g.recent, 1e-9)
        out["fill"] = round(min(frac, 1.0) * 100)
    if g.key == EARLY_KEY:
        for e in exports[-RECENT:]:
            c = early_utility(e)
            if c and c[1]:
                out["checks"].append({"k": c[0], "n": c[1], "ok": 100 * c[0] / c[1] >= g.target,
                                      "map": (e.get("match") or {}).get("map", "")})
    out["hits"] = sum(1 for c in out["checks"] if c["ok"])
    out["min_after"] = MIN_AFTER
    out["left"] = max(MIN_AFTER - g.matches_since, 0)
    out["others"] = [x.label for x in goals[1:]]
    out["achieved"] = [x.label for x in plan.get("achieved") or []]
    return out


def build(cfg: dict, config_path: Path) -> dict:
    """Alles fuer die Startseite. has_data=False ohne Exporte."""
    _, exports = _load(cfg)
    if not exports:
        return {"has_data": False}
    last = exports[-1]
    plan = current_plan([dict(e) for e in exports], Path(config_path).parent / ".training_state.json",
                        datetime.now().strftime("%Y-%m-%d %H:%M"))
    last_row = _tile(last)
    last_row["intro"] = _intro(report_ai.load(config_path, last.get("_file", "")))
    last_row["rounds"] = _rounds(last)
    now = datetime.now()
    return {
        "has_data": True,
        "problems": _problems(cfg, exports),
        "live": _live(config_path, now.timestamp()),
        "greeting": _greeting(now, (cfg.get("player_name") or "").strip()),
        "week": _week(exports, now),
        "total": len(exports),
        "premier": _premier(exports),
        "last": last_row,
        "goal": _goal(plan, exports),
        "tiles": [_tile(e) for e in reversed(exports[-TILES:])],
    }
