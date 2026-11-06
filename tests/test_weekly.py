import json
from datetime import date

from cs2_coach import weekly


def _export(folder, day, result, cs):
    data = {"match": {"result": result, "total_rounds": 20},
            "player": {"counter_strafe_pct": cs, "adr": 80}}
    (folder / f"{day}_mirage_13-9_2000_coach.json").write_text(json.dumps(data), encoding="utf-8")


def test_weekly_once_per_week_with_focus(tmp_path):
    ex = tmp_path / "vault" / "CS2-Coach" / "exports"
    ex.mkdir(parents=True)
    for d in ("2026-10-08", "2026-10-15", "2026-10-22", "2026-10-29"):
        _export(ex, d, "Sieg", 85)
    _export(ex, "2026-11-03", "Niederlage", 60)
    _export(ex, "2026-11-05", "Sieg", 62)
    cfg = {"obsidian_vault_path": str(tmp_path / "vault")}
    seen = []

    def ask(cfg, facts, **k):
        seen.append(facts)
        return "Rueckblick"

    monday = date(2026, 11, 9)
    assert weekly.run(cfg, tmp_path / "c.yaml", log=lambda m: None, today=monday, ask=ask, send=False)
    assert "2 Matches, 1 gewonnen" in seen[0] and "FOKUS FUER DIESE WOCHE: Counter-Strafing" in seen[0]
    assert not weekly.run(cfg, tmp_path / "c.yaml", log=lambda m: None, today=monday, ask=ask, send=False)


def test_no_matches_no_review(tmp_path):
    cfg = {"obsidian_vault_path": str(tmp_path / "vault")}
    assert not weekly.run(cfg, tmp_path / "c.yaml", log=lambda m: None, today=date(2026, 11, 9),
                          ask=lambda *a, **k: "x", send=False)
