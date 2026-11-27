"""Kartengeometrie fuer die 3D-Ansicht der Schluesselmomente.

Quelle: die Kollisionsgeometrie der Map (``maps/<map>/world_physics.vmdl_c``
im Map-VPK), mit Source2Viewer-CLI als glb exportiert (docker/export_maps3d.sh
auf dem Unraid-Host - das Werkzeug gehoert nicht ins Image). Kollision statt
Render-Geometrie: schlicht, aber genau das, was Sicht und Kugeln aufhaelt,
und ein Bruchteil der Groesse.

Die Vertex-Positionen im glb sind schon Spieleinheiten (Z oben, die 0.0254
stehen nur in der Knotenmatrix) - Demo-Koordinaten passen ohne Umrechnung.

Ausgabe ``<map>.geo.gz``: gzip ueber
    u32 Vertices, u32 Indizes solide, u32 Indizes durchsichtig,
    f32 Positionen (x, y, z je Vertex), u32 Indizes (solide, dann durchsichtig).
"durchsichtig" = passbullets/Glas (Zaeune, Scheiben): sichtbar, aber durchschaubar.
Clips, Skybox und Granaten-Clips fliegen raus - die sieht man im Spiel nicht.
"""

from __future__ import annotations

import gzip
import json
import struct
from pathlib import Path

DROP = ("clip", "physics_sky", "trigger")
SEE_THROUGH = ("passbullets", "glass", "chainlink")

_DTYPES = {5126: "<f4", 5125: "<u4", 5123: "<u2", 5121: "u1"}
_WIDTH = {"SCALAR": 1, "VEC3": 3}


def _read_glb(path: Path):
    raw = Path(path).read_bytes()
    jlen = struct.unpack("<I", raw[12:16])[0]
    doc = json.loads(raw[20:20 + jlen])
    bin_start = 20 + jlen + 8
    return doc, raw[bin_start:]


def _accessor(np, doc, binary, idx):
    acc = doc["accessors"][idx]
    view = doc["bufferViews"][acc["bufferView"]]
    off = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    width = _WIDTH[acc["type"]]
    arr = np.frombuffer(binary, dtype=_DTYPES[acc["componentType"]], count=acc["count"] * width, offset=off)
    return arr.reshape(-1, width) if width > 1 else arr


def classify(name: str) -> str | None:
    """'solid', 'see' oder None (verwerfen) fuer einen Physik-Mesh-Namen."""
    n = name.lower()
    if any(k in n for k in DROP):
        return None
    if any(k in n for k in SEE_THROUGH):
        return "see"
    return "solid"


def convert(glb: Path, out: Path) -> dict:
    """glb -> .geo.gz; verschweisst gleiche Vertices (der Export indiziert jeden Vertex einzeln)."""
    import numpy as np
    doc, binary = _read_glb(glb)
    pos_parts, groups = [], {"solid": [], "see": []}
    base = 0
    for mesh in doc["meshes"]:
        kind = classify(mesh.get("name", ""))
        for prim in mesh["primitives"]:
            if kind is None or prim.get("mode", 4) != 4:
                continue
            pos = _accessor(np, doc, binary, prim["attributes"]["POSITION"]).astype("<f4")
            idx = _accessor(np, doc, binary, prim["indices"]).astype("<u4") if "indices" in prim \
                else np.arange(len(pos), dtype="<u4")
            pos_parts.append(pos)
            groups[kind].append(idx + base)
            base += len(pos)
    pos = np.concatenate(pos_parts) if pos_parts else np.zeros((0, 3), "<f4")
    # Verschweissen auf 1/8 Einheit - genauer liegen die Kollisionsflaechen ohnehin nicht
    key = np.round(pos * 8).astype("<i4")
    uniq, inverse = np.unique(key, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1).astype("<u4")
    verts = (uniq / 8).astype("<f4")
    solid = inverse[np.concatenate(groups["solid"])] if groups["solid"] else np.zeros(0, "<u4")
    see = inverse[np.concatenate(groups["see"])] if groups["see"] else np.zeros(0, "<u4")
    payload = struct.pack("<III", len(verts), len(solid), len(see)) + verts.tobytes() + solid.tobytes() + see.tobytes()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(gzip.compress(payload, 6))
    return {"vertices": int(len(verts)), "triangles": int((len(solid) + len(see)) // 3),
            "bytes": out.stat().st_size}


def geo_dir(cfg: dict, config_path: Path) -> Path:
    """Ablage der .geo.gz: ``maps3d/`` neben der Config (wie die anderen Caches)."""
    from .users import shared_dir
    return Path(cfg.get("maps3d_dir") or shared_dir(config_path) / "maps3d")


def geo_path(cfg: dict, config_path: Path, map_key: str) -> Path | None:
    p = geo_dir(cfg, config_path) / f"{map_key.lower()}.geo.gz"
    return p if p.exists() else None


def main(argv: list[str] | None = None) -> None:
    """``python -m cs2_coach.map3d <export-ordner> <ziel-ordner>``: alle world_physics*.glb umwandeln."""
    import sys
    args = argv if argv is not None else sys.argv[1:]
    src, dst = Path(args[0]), Path(args[1])
    for glb in sorted(src.glob("*/world_physics_physics.glb")):
        map_key = glb.parent.name.removeprefix("de_")
        info = convert(glb, dst / f"{map_key}.geo.gz")
        print(f"{map_key}: {info['vertices']} Vertices, {info['triangles']} Dreiecke, {info['bytes'] // 1024} KB")


if __name__ == "__main__":
    main()
