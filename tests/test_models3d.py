import json
import struct

import numpy as np

from cs2_coach import models3d


def test_weapon_key_maps_demo_names():
    k = models3d.weapon_key
    assert k("AK-47") == "ak47" and k("M4A4") == "m4a1" and k("M4A1-S") == "m4a1_silencer"
    assert k("USP-S") == "usp_silencer" and k("Desert Eagle") == "deagle" and k("SG 553") == "sg556"
    assert k("Butterfly Knife") == "knife" and k("Zeus x27") == "taser" and k("") is None and k("Quatsch") is None


def test_weapon_class():
    assert models3d.weapon_class("glock") == "pistol"
    assert models3d.weapon_class("awp") == "rifle"
    assert models3d.weapon_class("knife") == "knife" and models3d.weapon_class("flashbang") == "knife"


def test_export_list_covers_agents_weapons_clips():
    lst = models3d.export_list()
    assert "agents/models/ctm_sas/ctm_sas.vmdl_c" in lst
    assert "weapons/models/ak47/weapon_rif_ak47.vmdl_c" in lst
    assert "animation/anims/world/rifle/_default_rifle/run_n_rifle.vnmclip_c" in lst
    assert len([p for p in lst if p.endswith("vnmclip_c")]) == len(models3d.CLASSES) * len(models3d.MOVES)


def test_shrink_drop_meshes_keeps_animation(tmp_path):
    t = np.array([0.0, 1.0], "<f4").tobytes()
    doc = {"asset": {"version": "2.0"}, "scenes": [{"nodes": [0]}], "nodes": [{"name": "pelvis", "mesh": 0}],
           "meshes": [{"primitives": []}], "accessors": [{"bufferView": 0, "componentType": 5126, "count": 2, "type": "SCALAR"}],
           "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 8}],
           "animations": [{"name": "idle", "samplers": [], "channels": []}]}
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    raw = b"glTF" + struct.pack("<II", 2, 0) + struct.pack("<I", len(js)) + b"JSON" + js \
        + struct.pack("<I", len(t)) + b"BIN\x00" + t
    (tmp_path / "c.glb").write_bytes(raw)
    out = models3d.shrink(tmp_path / "c.glb", drop_meshes=True)
    jl = struct.unpack("<I", out[12:16])[0]
    d = json.loads(out[20:20 + jl])
    assert "meshes" not in d and "mesh" not in d["nodes"][0] and d["animations"][0]["name"] == "idle"
