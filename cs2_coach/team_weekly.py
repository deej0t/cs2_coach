"""Team-Wochenrueckblick: eine Nachricht fuer alle Spieler der Instanz in einen Team-Discord.

Je Spieler: Matches und Siege der Vorwoche und sein Fokus (aus weekly.summary,
dieselbe Rechnung wie im persoenlichen Rueckblick). Dazu, wie viele Matches ihr
zusammen gespielt habt und wie die liefen. Keine KI, keine Hebel-Behauptungen.

Aus, solange ``team_webhook`` (Admin-config.yaml) leer ist oder es keine Mates
gibt. Einmal pro Woche; Marke ``.team_week-<KW>`` in users.shared_dir.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from . import users


def _display(base_config: Path, name: str | None, cfg: dict) -> str:
    return (users.load_users(base_config).get(name, {}).get("display") if name else None) \
        or cfg.get("player_name") or name or "Admin"


def build(base_cfg: dict, base_config: Path, today: date) -> str | None:
    from .duo import _results
    from .weekly import summary
    players = users.all_players(base_cfg, base_config)
    if len(players) < 2:
        return None
    start = today - timedelta(days=today.weekday() + 7)
    end = start + timedelta(days=7)
    lines, demos_by = [], {}
    for name, cfg, path in players:
        s = summary(cfg, path, today)
        if s is None:
            continue
        pct = round(100 * s["wins"] / s["matches"])
        line = f"👤 **{_display(base_config, name, cfg)}**: {s['matches']} Matches · {s['wins']} Siege ({pct} %)"
        if s["trainings"]:
            line += f" · {s['trainings']}× Training"
        if s["focus"]:
            line += f"\n   🎯 Fokus: {s['focus'][0]}"
        lines.append(line)
        # gemeinsame Matches der Woche: Demo -> Spieler (Datum aus dem Export, wie weekly)
        from .report_ai import _exports
        from .training_effect import _match_time
        idx = {v: k for k, v in users.demo_index(cfg).items()}
        for f in _exports(cfg):
            t = _match_time(f)
            if t is not None and start <= t.date() < end and f.name in idx:
                demos_by.setdefault(idx[f.name], []).append((name, cfg))
    if not lines:
        return None
    out = [f"**👥 Team-Woche {start:%d.%m.}–{end - timedelta(days=1):%d.%m.}**", ""] + lines
    together = {d: ps for d, ps in demos_by.items() if len(ps) > 1}
    if together:
        wins = sum(1 for d, ps in together.items() if _results(ps[0][1]).get(d, ("", False))[1])
        out += ["", f"🤝 **Zusammen:** {len(together)} Matches · {wins} Siege ({round(100 * wins / len(together))} %)"]
    return "\n".join(out)


def run(base_cfg: dict, base_config: Path, log=print, today: date | None = None, post=None) -> bool:
    """Team-Rueckblick der Vorwoche einmal verschicken. True = verschickt."""
    from .briefing import _chunks
    from .weekly import week_id
    hook = (base_cfg.get("team_webhook") or "").strip()
    if not hook or not users.load_users(base_config):
        return False
    today = today or datetime.now().date()
    mark = users.shared_dir(base_config) / f".team_week-{week_id(today - timedelta(days=7))}"
    if mark.exists():
        return False
    text = build(base_cfg, base_config, today)
    if text is None:
        return False
    if post is None:
        import requests
        post = requests.post
    for part in _chunks(text):
        try:
            r = post(hook, json={"content": part}, timeout=15)
        except Exception as e:  # noqa: BLE001 - naechster Sync versucht es erneut
            log(f"Team-Discord nicht erreichbar: {e}")
            return False
        if getattr(r, "status_code", 204) >= 400:
            log(f"Team-Discord lehnt ab: HTTP {r.status_code}")
            return False
    mark.write_text(text, encoding="utf-8")
    log("Team-Wochenrueckblick verschickt")
    return True
