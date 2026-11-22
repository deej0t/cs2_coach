import gzip
import json
import struct

import numpy as np

from cs2_coach.map3d import classify, convert


def _glb(tmp_path, meshes):
    """Minimaler glb: je Mesh ein nicht verschweisstes Dreieck (wie der Source2Viewer-Export)."""
    binary, doc = b"", {"accessors": [], "bufferViews": [], "meshes": []}
    for name, tri in meshes:
        pos = np.array(tri, "<f4").tobytes()
        idx = np.arange(3, dtype="<u4").tobytes()
        for data, ctype, typ in ((pos, 5126, "VEC3"), (idx, 5125, "SCALAR")):
            doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data)})
            doc["accessors"].append({"bufferView": len(doc["bufferViews"]) - 1, "componentType": ctype,
                                     "count": 3, "type": typ})
            binary += data
        n = len(doc["accessors"])
        doc["meshes"].append({"name": name, "primitives": [{"attributes": {"POSITION": n - 2}, "indices": n - 1}]})
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    raw = b"glTF" + struct.pack("<II", 2, 0) + struct.pack("<I", len(js)) + b"JSON" + js \
        + struct.pack("<I", len(binary)) + b"BIN\x00" + binary
    p = tmp_path / "w.glb"
    p.write_bytes(raw)
    return p


def test_classify():
    assert classify("physics_group_concrete") == "solid"
    assert classify("physics_passbullets_chainlink") == "see"
    assert classify("physics_npcclip_playerclip") is None and classify("physics_sky") is None


def test_convert_welds_and_drops_clips(tmp_path):
    a = [[0, 0, 0], [100, 0, 0], [0, 100, 0]]
    b = [[100, 0, 0], [0, 100, 0], [100, 100, 0]]   # teilt zwei Ecken mit a
    glb = _glb(tmp_path, [("physics_group_concrete", a), ("physics_group_glass", b),
                          ("physics_npcclip_playerclip", [[5, 5, 5], [6, 5, 5], [5, 6, 5]])])
    info = convert(glb, tmp_path / "x.geo.gz")
    raw = gzip.decompress((tmp_path / "x.geo.gz").read_bytes())
    nv, ns, nt = struct.unpack("<III", raw[:12])
    assert (nv, ns, nt) == (4, 3, 3) and info["triangles"] == 2
