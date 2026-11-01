"""Live-Coach: GSI-Stapel -> geworfene Granaten, Granaten mit ins Grab, Hinweise."""

import json

from cs2_coach import live_coach as lc

ME, MATE = "76561198019262528", "76561198000000001"
CFG = {"steam_id": ME}


def gsi(rnd, phase="live", sid=ME, health=100, nades=("weapon_hegrenade", "weapon_flashbang"), left=60,
        map_phase="live"):
    weapons = {f"weapon_{i}": {"name": n, "type": "Grenade", "ammo_reserve": 1} for i, n in enumerate(nades)}
    weapons["weapon_9"] = {"name": "weapon_ak47", "type": "Rifle"}
    return {"provider": {"steamid": ME}, "map": {"name": "de_mirage", "mode": "competitive", "phase": map_phase,
                                                  "round": rnd - 1,
                                                  "team_ct": {"score": 7}, "team_t": {"score": 6}},
            "round": {"phase": phase}, "phase_countdowns": {"phase_ends_in": str(left)},
            "player": {"steamid": sid, "state": {"health": health}, "weapons": weapons}}


def run(*payloads, state=None):
    state = state or {}
    for p in payloads:
        state = lc.update(state, p, CFG)
    return state


def test_counts_thrown_and_wasted_grenades():
    st = run(gsi(1, "freezetime"), gsi(1), gsi(1, nades=("weapon_flashbang",)),  # HE geworfen
             gsi(1, health=0, nades=("weapon_flashbang",)))                       # tot mit Flash
    assert st["rounds"]["1"] == {"thrown": 1, "wasted": 1, "half": 1}


def test_death_seen_as_spectator_still_counts_wasted():
    st = run(gsi(2, "freezetime"), gsi(2), gsi(2, sid=MATE, nades=()))
    assert st["rounds"]["2"]["wasted"] == 2 and not st["alive"]


def test_round_start_hint_compares_with_own_wins():
    st = run(gsi(1, "freezetime"), gsi(1), gsi(1, phase="over"), state={})
    st["target"] = 1.5
    st = lc.update(st, gsi(2, "freezetime"), CFG)
    assert "Runde 2: 0 Granaten in 1 Runden" in st["hint"]["text"] and "Ziel waeren 2" in st["hint"]["text"]


def test_late_round_reminder_once():
    st = run(gsi(3, "freezetime"), gsi(3, left=50), gsi(3, left=30), gsi(3, left=20))
    late = [h for h in st["log"] if h["kind"] == "late"]
    assert len(late) == 1 and "HE" in late[0]["text"] and "30 s" in late[0]["text"]


def test_gameover_summary_saved(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    for p in [gsi(1, "freezetime"), gsi(1), gsi(1, nades=()), gsi(1, phase="over", map_phase="gameover")]:
        lc.ingest(CFG, cfg_path, p)
    saved = list((tmp_path / lc.MATCHES_DIR).glob("*.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text())["summary"]["thrown"] == 2


def test_target_from_won_matches(tmp_path):
    folder = tmp_path / "CS2-Coach" / "exports"
    folder.mkdir(parents=True)
    for i, (res, n) in enumerate([("Sieg", 10), ("Sieg", 20), ("Niederlage", 40)]):
        (folder / f"2026-10-2{i}_mirage_13-8_2049_coach.json").write_text(json.dumps(
            {"match": {"result": res, "total_rounds": 20},
             "utility_positions": [{"t": "h", "x": 0, "y": 0, "r": 1}] * n}))
    assert lc.target_per_round({"obsidian_vault_path": str(tmp_path)}, "de_mirage") == 0.75


def test_ingest_endpoint_checks_token(tmp_path, monkeypatch):
    import importlib
    cfg = tmp_path / "config.yaml"
    cfg.write_text("language: de\n", encoding="utf-8")
    monkeypatch.setenv("CS2COACH_CONFIG", str(cfg))
    import cs2_coach.web.app as app_module
    try:
        client = importlib.reload(app_module).create_app().test_client()
        token = lc.load_or_create_token(cfg)
        assert client.post("/api/gsi/falsch", json={"auth": {"token": token}}).status_code == 403
        assert client.post(f"/api/gsi/{token}", json={"auth": {"token": token}, **gsi(1)}).status_code == 200
        assert lc.load_state(cfg)["map"] == "de_mirage"
        assert b"CS2 Coach Live" in client.get("/live/gamestate_integration_cs2coach.cfg").data
    finally:
        monkeypatch.delenv("CS2COACH_CONFIG")
        importlib.reload(app_module)


def test_ignores_deathmatch_aim_maps_and_training(tmp_path, monkeypatch):
    dm = gsi(1)
    dm["map"].update(mode="deathmatch", name="aim_multigame")
    assert lc.update({}, dm, CFG) == {}
    from cs2_coach import training_server as ts
    monkeypatch.setattr(ts, "is_active", lambda path: True)
    lc.ingest(CFG, tmp_path / "config.yaml", gsi(1))
    assert lc.load_state(tmp_path / "config.yaml") == {}


def test_ai_hint_at_halftime_uses_only_facts(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    st = run(gsi(1, "freezetime"), gsi(1), gsi(1, nades=("weapon_flashbang",)))
    st["target"] = 1.85
    lc.save_state(cfg_path, st)
    seen = {}

    def fake(cfg, system, prompt):
        seen["prompt"] = prompt
        return "Wirf in der zweiten Halbzeit frueher deine HE."

    assert lc.ai_hint(CFG, cfg_path, 0, ask_fn=fake)
    assert "R1: 1 geworfen" in seen["prompt"] and "1.85" in seen["prompt"]
    hint = lc.load_state(cfg_path)["hint"]
    assert hint["kind"] == "ai" and hint["text"].startswith("Coach: Wirf")
