from cs2_coach import nades3d


def _row(t, typ="CSmokeGrenadeProjectile", eid=5, x=1.0, y=2.0, z=3.0, sid="1"):
    return {"tick": t, "grenade_type": typ, "grenade_entity_id": eid, "x": x, "y": y, "z": z, "steamid": sid}


def test_paths_only_projectiles_and_split_on_reuse():
    rows = [_row(t) for t in range(100, 110)] + [_row(t) for t in range(300, 304)]
    rows += [_row(105, typ="CSmokeGrenade", x=float("nan"))]          # Granate in der Hand: keine Bahn
    rows += [_row(t, typ="CFlashbangProjectile", eid=9) for t in range(120, 124)]
    out = nades3d.paths(rows, 0, 1000)
    assert [p["type"] for p in out] == ["smoke", "flash", "smoke"]   # ID 5 zweimal: Luecke > 1 s trennt
    assert out[0]["pts"][0] == [100, 1.0, 2.0, 3.0] and out[0]["pts"][-1][0] == 109


def test_effects_pair_start_with_next_end_and_keep_running_ones():
    ev = {"smokegrenade_detonate": [{"tick": 100, "entityid": 7, "x": 0, "y": 0, "z": 0, "user_steamid": "1"}],
          "smokegrenade_expired": [{"tick": 50, "entityid": 7}, {"tick": 1300, "entityid": 7}],
          "flashbang_detonate": [{"tick": 5000, "entityid": 3, "x": 0, "y": 0, "z": 0}]}
    out = nades3d.effects(ev, 1000, 2000)
    assert len(out) == 1 and out[0]["type"] == "smoke" and (out[0]["t0"], out[0]["t1"]) == (100, 1300)
