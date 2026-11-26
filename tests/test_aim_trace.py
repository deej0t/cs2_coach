from cs2_coach import aim_trace

ME, EN = "1", "2"


def row(t, sid, x, y, yaw=0.0, pitch=0.0, z=0.0):
    return {"tick": t, "steamid": sid, "X": x, "Y": y, "Z": z, "yaw": yaw, "pitch": pitch, "ducking": False}


def test_errors_sign_convention():
    me = row(0, ME, 0, 0, yaw=10)            # Blick 10° nach links gedreht (gegen Uhrzeigersinn)
    h, v, tot, dist = aim_trace.errors(me, row(0, EN, 1000, 0))
    assert h < 0 and round(abs(h)) == 10 and abs(v) < 0.01 and round(tot) == 10 and round(dist) == 1000
    me = row(0, ME, 0, 0, pitch=-5)          # Blick nach oben
    h, v, tot, _ = aim_trace.errors(me, row(0, EN, 1000, 0))
    assert v > 0 and round(v) == 5


def test_trace_flags_moving_and_missed_first_shot():
    rows = []
    for t in range(0, 20):
        rows.append(row(t, ME, t * 2.0, 0, yaw=20 - t))     # 128 u/s, Fadenkreuz wandert aufs Ziel
        rows.append(row(t, EN, 1000, 0))
    shots = [{"tick": 5, "steamid": ME, "weapon": "ak47"}]
    out = aim_trace.trace(rows, shots, [], ME, EN, sight_tick=0)
    assert len(out["series"]) == 20
    s = out["shots"][0]
    assert s["moving"] and not s["hit"] and s["first"]
    texts = " ".join(m["text"] for m in out["mistakes"])
    assert "Bewegung" in texts and "verfehlt" in texts and "auf dem Kopf" in texts


def test_trace_hit_and_overshoot():
    yaws = [10, 6, 2, -1, -4, -3, -1, 0, 0, 0]
    rows = [r for t, y in enumerate(yaws) for r in (row(t, ME, 0, 0, yaw=y), row(t, EN, 1000, 0))]
    hurts = [{"tick": 8, "attacker": ME, "victim": EN, "hitgroup": "head", "dmg": 100}]
    out = aim_trace.trace(rows, [{"tick": 8, "steamid": ME}], hurts, ME, EN, sight_tick=0)
    assert out["shots"][0]["hit"]
    assert any("überschossen" in m["text"] for m in out["mistakes"])


def test_shots_before_sight_are_not_judged():
    rows = [r for t in range(0, 40) for r in (row(t, ME, t * 2.0, 0, yaw=90), row(t, EN, 1000, 0))]
    out = aim_trace.trace(rows, [{"tick": 2, "steamid": ME}], [], ME, EN, sight_tick=30)
    assert out["shots"][0]["before_sight"]
    assert not any("verfehlt" in m["text"] or "Bewegung" in m["text"] for m in out["mistakes"])
