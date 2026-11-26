import gzip
import json
import struct

import numpy as np
import pytest

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from cs2_coach.map3d_render import INCH, convert, keep_material  # noqa: E402


def _read(path):
    raw = gzip.decompress(path.read_bytes())
    jl = struct.unpack("<I", raw[12:16])[0]
    return json.loads(raw[20:20 + jl]), raw[20 + jl + 8:]


def _glb(tmp_path):
    """Ein Mesh, zwei Primitive auf einem gemeinsamen Vertexpuffer (wie Source2Viewer), plus Werkzeug-Material."""
    Image.new("RGB", (64, 64), (200, 100, 50)).save(tmp_path / "wall.png")
    # glTF-Meter, Y oben: Spielpunkt (100, 200, 300) liegt bei x=200, y=300, z=100 Zoll
    pos = np.array([[200, 300, 100], [201, 300, 100], [200, 301, 100], [0, 0, 0]], "<f4") * INCH
    uv = np.zeros((4, 2), "<f4")
    binary, doc = b"", {"accessors": [], "bufferViews": []}
    for data, ctype, typ, n in ((pos.tobytes(), 5126, "VEC3", 4), (uv.tobytes(), 5126, "VEC2", 4),
                                (np.array([0, 1, 2], "<u4").tobytes(), 5125, "SCALAR", 3),
                                (np.array([3, 1, 2], "<u4").tobytes(), 5125, "SCALAR", 3)):
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data)})
        doc["accessors"].append({"bufferView": len(doc["bufferViews"]) - 1, "componentType": ctype, "count": n, "type": typ})
        binary += data
    attrs = {"POSITION": 0, "TEXCOORD_0": 1}
    doc.update({
        "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": attrs, "indices": 2, "material": 0},
                                   {"attributes": attrs, "indices": 3, "material": 1}]}],
        "materials": [{"name": "wall", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
                      {"name": "clip", "extras": {"vmat": {"Name": "materials/tools/toolsclip.vmat"}},
                       "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
        "textures": [{"source": 0}], "images": [{"uri": "wall.png"}]})
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    raw = b"glTF" + struct.pack("<II", 2, 0) + struct.pack("<I", len(js)) + b"JSON" + js \
        + struct.pack("<I", len(binary)) + b"BIN\x00" + binary
    (tmp_path / "de_x.glb").write_bytes(raw)
    return tmp_path / "de_x.glb"


def test_keep_material_drops_tools():
    tex = {"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}
    assert keep_material({"name": "wall", **tex})
    assert not keep_material({"extras": {"vmat": {"Name": "materials/tools/toolsnodraw.vmat"}}, **tex})
    assert not keep_material({"name": "untextured"})


def test_convert_game_units_only_used_vertices(tmp_path):
    info = convert(_glb(tmp_path), tmp_path / "x.render.glb.gz")
    doc, binary = _read(tmp_path / "x.render.glb.gz")
    assert info["materials"] == 1 and info["triangles"] == 1 and info["vertices"] == 3
    acc = doc["accessors"][doc["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    assert np.allclose(acc["min"], [100, 200, 300], atol=1e-3)
    assert doc["images"][0]["mimeType"] == "image/jpeg"
