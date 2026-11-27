"""Duo-Ansicht: gewinnst du mit einem Mate oefter als ohne ihn?

Quelle sind nur die eigenen Exporte (Ergebnis + Map) und der Demo-Index des
Mates (users.demo_index): Match "zusammen" = beide haben einen Export zur
selben Demo. Vergleich ueber evidence.compare_rates mit comparisons = Zahl
der Mates (Best-of-k), damit bei vielen Mates kein Zufallstreffer als Befund
durchgeht. Siegquote ist ein Ergebnis, kein Hebel - die Seite zeigt sie als
Team-Information, Empfehlungen leitet niemand daraus ab.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import users
from .evidence import compare_rates

_cache: dict[str, tuple[float, dict]] = {}
_RE_RESULT = re.compile(r'"result"\s*:\s*"([^"]+)"')
_RE_MAP = re.compile(r'"map"\s*:\s*"([^"]*)"')


def _results(cfg: dict) -> dict[str, tuple[str, bool]]:
    """Demo-Datei -> (Map, gewonnen) aus den eigenen Exporten; Unentschieden fehlen."""
    d = users.export_dir(cfg)
    if d is None or not d.exists():
        return {}
    key, mtime = str(d), d.stat().st_mtime
    hit = _cache.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    out = {}
    for demo, fname in users.demo_index(cfg).items():
        try:
            with open(d / fname, encoding="utf-8") as fh:
                head = fh.read(4096)
        except OSError:
            continue
        r, m = _RE_RESULT.search(head), _RE_MAP.search(head)
        if r and r.group(1) in ("Sieg", "Niederlage"):
            out[demo] = ((m.group(1) if m else "") or "?", r.group(1) == "Sieg")
    _cache[key] = (mtime, out)
    return out


def _rate(rows) -> dict:
    n = len(rows)
    w = sum(1 for won in rows if won)
    return {"n": n, "wins": w, "pct": round(100 * w / n) if n else None}


def duo_table(base_cfg: dict, base_config: Path, me: str | None) -> list[dict]:
    """Je anderem Spieler: Siegquote zusammen gegen ohne ihn, gesamt und je Map."""
    players = users.all_players(base_cfg, base_config)
    mine_cfg = next((c for n, c, _ in players if n == me), None)
    if mine_cfg is None:
        return []
    mine = _results(mine_cfg)
    others = [(n, c) for n, c, _ in players if n != me]
    k = max(1, len(others))
    names = users.load_users(base_config)
    out = []
    for name, cfg in others:
        theirs = set(users.demo_index(cfg))
        together = [v for demo, v in mine.items() if demo in theirs]
        if not together:
            continue
        alone = [v for demo, v in mine.items() if demo not in theirs]
        t, a = [won for _, won in together], [won for _, won in alone]
        cmp = compare_rates(sum(t), len(t), sum(a), len(a), comparisons=k)
        maps = {}
        for mp, won in together:
            maps.setdefault(mp, []).append(won)
        out.append({
            "user": name, "als": name or "admin",
            "display": (names.get(name, {}).get("display") if name else None) or cfg.get("player_name") or name or "Admin",
            "together": _rate(t), "alone": _rate(a), "cmp": cmp,
            "note": cmp.note() if cmp else "",
            "maps": sorted(({"map": mp, **_rate(v)} for mp, v in maps.items()), key=lambda x: -x["n"]),
        })
    return sorted(out, key=lambda r: -r["together"]["n"])
