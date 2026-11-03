import json

from cs2_coach import training_effect as te


def _export(folder, name, cp, cs, nades, rounds=20):
    data = {"match": {"total_rounds": rounds},
            "player": {"crosshair_placement": {"kills_analyzed": 10, "median_degrees": cp}, "counter_strafe_pct": cs},
            "utility_positions": [{"t": "s"}] * nades}
    (folder / name).write_text(json.dumps(data), encoding="utf-8")


def test_groups_by_training_in_previous_24h(tmp_path):
    ex = tmp_path / "CS2-Coach" / "exports"
    ex.mkdir(parents=True)
    _export(ex, "2026-10-01_de_mirage_13-5_2000_coach.json", 9, 10, 2)    # vor dem ersten Training: ignoriert
    for i in range(8):
        _export(ex, f"2026-11-{2 * i + 2:02d}_de_mirage_13-5_2000_coach.json", 3, 60, 40)  # Training am Vortag
        _export(ex, f"2026-11-{2 * i + 3:02d}_de_mirage_13-5_2000_coach.json", 3.2, 58, 38)  # 47 h danach
    sessions = [{"played": 3, "started": f"2026-11-{2 * i + 1:02d}T21:00:00"} for i in range(8)]
    res = te.analyse({"obsidian_vault_path": str(tmp_path)}, sessions)
    assert (res["with"], res["without"]) == (8, 8)
    rows = {r["name"]: r for r in res["rows"]}
    assert rows["Fadenkreuz vor Kills"]["mean_with"] == 3 and rows["Granaten pro Runde"]["mean_with"] == 2
    assert rows["Counter-Strafing"]["cmp"] is not None


def test_no_training_no_analysis(tmp_path):
    assert te.analyse({"obsidian_vault_path": str(tmp_path)}, [])["since"] is None


def test_live_summary_latest_vs_median(tmp_path):
    ex = tmp_path / "CS2-Coach" / "exports"
    ex.mkdir(parents=True)
    for d in range(1, 5):
        _export(ex, f"2026-11-0{d}_de_mirage_13-5_2000_coach.json", 4, 70, 30)
    _export(ex, "2026-11-05_de_mirage_13-5_2000_coach.json", 3, 80, 50)
    res = te.live_summary({"obsidian_vault_path": str(tmp_path)}, [])
    rows = {r["name"]: r for r in res["latest"]["rows"]}
    assert rows["Fadenkreuz vor Kills"]["better"] is True and rows["Counter-Strafing"]["base"] == 70
    assert res["goal"] is None
