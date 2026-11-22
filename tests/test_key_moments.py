from cs2_coach.key_moments import annotate


def _replay(mate_xy, trade=True):
    players = [
        {"steamid": "me", "name": "Ich", "is_target": True, "team": "self"},
        {"steamid": "m1", "name": "Mate", "is_target": False, "team": "mate"},
        {"steamid": "e1", "name": "Gegner", "is_target": False, "team": "enemy"},
    ]
    frames = []
    for i in range(80):
        t = 1000 + i * 8
        hp = 100 if t < 1320 else 0
        frames.append({"t": t, "p": [[0, 500, 500, hp], [1, *mate_xy, 100], [2, 520, 500, 100]]})
    kills = [{"tick": 1320, "attacker": "e1", "victim": "me", "weapon": "ak47"}]
    if trade:
        kills.append({"tick": 1400, "attacker": "m1", "victim": "e1", "weapon": "m4a1"})
    return {"players": players, "frames": frames, "kills": kills, "fps": 8}


def test_close_mate_and_trade_are_good():
    out = annotate(_replay((505, 500)), 1320, scale=5.0)
    tones = {n["tone"] for n in out["notes"]}
    assert "bad" not in tones
    assert any("Getradet" in n["text"] for n in out["notes"])
    assert out["focus"] == 40
    assert out["clip"][0] < out["focus"] < out["clip"][1]


def test_far_mate_without_trade_is_bad():
    out = annotate(_replay((900, 900), trade=False), 1320, scale=5.0)
    texts = " ".join(n["text"] for n in out["notes"] if n["tone"] == "bad")
    assert "zu weit" in texts and "Kein Trade" in texts
    assert out["marks"][0]["type"] == "circle"


def test_duel_facts_who_saw_first_and_crosshair_side():
    from cs2_coach.key_moments import duel_facts, duel_notes, safe_angle
    rows = []
    for t in range(100, 201):
        # Gegner sieht dich ab Tick 120, du ihn ab 150; du schaust nach +X, er steht leicht rechts (y < 0)
        rows.append({"tick": t, "steamid": "me", "X": 0, "Y": 0, "yaw": 0, "spotted_by": ["k"] if t >= 120 else []})
        rows.append({"tick": t, "steamid": "k", "X": 500, "Y": -100, "yaw": 180, "spotted_by": ["me"] if t >= 150 else []})
    shots = [{"tick": 125, "steamid": "k"}, {"tick": 170, "steamid": "me"}]
    f = duel_facts(rows, shots, "me", "k", 200)
    assert (f["his_sight"], f["my_sight"], f["my_shot"], f["his_shot"]) == (120, 150, 170, 125)
    assert f["offset"] > 0  # rechts
    notes, events = duel_notes(f, "Gegner")
    text = " ".join(n["text"] for n in notes)
    assert "469 ms früher" in text and "rechts" in text
    assert safe_angle(float("nan")) == 0 and safe_angle("x") == 0 and safe_angle(12.6) == 13


def test_best_kills_matches_by_order_and_skips_mismatched_rounds():
    from cs2_coach.key_moments import best_kills
    data = {"match": {"demo_file": "m.dem"}, "kill_positions": [
        {"t": "k", "r": 3, "tk": 500, "e": "B", "w": "ak47", "p": ""},
        {"t": "k", "r": 3, "tk": 300, "e": "A", "w": "ak47", "p": ""},
        {"t": "k", "r": 4, "tk": 900, "e": "C", "w": "awp", "p": ""}]}
    d = lambda sec, cost: {"side": "T", "cost": cost, "before": 60.0, "second": sec, "enemy": 5, "own": 4, "planted": False}  # noqa: E731
    kills = {("m", 3): [d(10, 5.0), d(20, 12.0)], ("m", 4): [d(5, 50.0), d(6, 1.0)]}
    out = best_kills(data, kills)
    assert [(m["opponent"], m["gain"], m["side"], m["before"]) for m in out] == [("B", 12.0, "CT", 40.0), ("A", 5.0, "CT", 40.0)]
