import json

from cs2_coach import report_ai


def _export(folder, name, cs, adr=80):
    data = {"match": {"map": "Mirage", "result": "Sieg", "score_own": 13, "score_enemy": 9, "total_rounds": 22},
            "player": {"counter_strafe_pct": cs, "adr": adr}}
    (folder / name).write_text(json.dumps(data), encoding="utf-8")


def test_facts_compare_with_own_median_and_cached_text(tmp_path):
    ex = tmp_path / "vault" / "CS2-Coach" / "exports"
    ex.mkdir(parents=True)
    for d in range(1, 5):
        _export(ex, f"2026-11-0{d}_mirage_13-9_2000_coach.json", 80)
    _export(ex, "2026-11-05_mirage_13-9_2000_coach.json", 60)
    cfg = {"obsidian_vault_path": str(tmp_path / "vault")}
    seen = []

    def fake_ask(cfg, facts, log, system, instruction, max_len):
        seen.append(facts)
        return "Text"

    assert report_ai.generate_pending(cfg, tmp_path / "c.yaml", log=lambda m: None, limit=1, ask=fake_ask, notify=False) == 1
    assert "Median der letzten 4" in seen[0] and "Mirage, Sieg 13:9" in seen[0]
    assert report_ai.load(tmp_path / "c.yaml", "2026-11-05_mirage_13-9_2000_coach.json") == "Text"
    # schon vorhanden -> nicht noch einmal fragen
    assert report_ai.generate_pending(cfg, tmp_path / "c.yaml", log=lambda m: None, limit=1, ask=fake_ask, notify=False) == 1
    assert report_ai.load(tmp_path / "c.yaml", "2026-11-04_mirage_13-9_2000_coach.json") == "Text"


def test_rejected_ai_text_writes_nothing(tmp_path):
    ex = tmp_path / "vault" / "CS2-Coach" / "exports"
    ex.mkdir(parents=True)
    _export(ex, "2026-11-05_mirage_13-9_2000_coach.json", 60)
    cfg = {"obsidian_vault_path": str(tmp_path / "vault")}
    assert report_ai.generate_pending(cfg, tmp_path / "c.yaml", log=lambda m: None,
                                      ask=lambda *a, **k: None) == 0
    assert report_ai.load(tmp_path / "c.yaml", "2026-11-05_mirage_13-9_2000_coach.json") is None


def test_newest_match_sets_focus_and_training_goal(tmp_path):
    from cs2_coach.training_goal import pick_goal
    ex = tmp_path / "vault" / "CS2-Coach" / "exports"
    ex.mkdir(parents=True)
    for d in range(1, 5):
        _export(ex, f"2026-11-0{d}_mirage_13-9_2000_coach.json", 85)
    _export(ex, "2026-11-05_mirage_13-9_2000_coach.json", 60)  # Counter-Strafing eingebrochen
    cfg = {"obsidian_vault_path": str(tmp_path / "vault")}
    report_ai.generate_pending(cfg, tmp_path / "c.yaml", log=lambda m: None, limit=1,
                               ask=lambda *a, **k: "Text", notify=False)
    focus = report_ai.load_focus(tmp_path / "c.yaml")
    assert focus["key"] == "counter_strafe" and focus["map"] == "de_mirage"
    sessions = [{"duels": [{"first_speed": 50}] * 10 + [{"seen_err": 2}] * 10}]
    assert pick_goal(sessions, focus["key"])["key"] == "stopped"
