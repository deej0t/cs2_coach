"""Texturierte Kartengeometrie fuer die 3D-Ansicht (wie smokepractice.com statt Kollisionsklotz).

Quelle: die Render-Geometrie der Map (``maps/<map>.vmap_c``), mit
Source2Viewer-CLI samt Materialien als glb exportiert
(docker/unraid/maps3d-export.sh). Der Roh-Export ist ~90 MB Geometrie plus
~700 Texturen (~300 MB) - fuer den Browser viel zu gross. Diese Datei macht
daraus ein einzelnes, eigenstaendiges glb:

- nur Position, Normale und die Farb-UV je Vertex (Tangenten, Lightmap-UVs,
  Vertexfarben fliegen raus),
- alle Primitive gleichen Materials zu einem Mesh zusammengefasst,
- Werkzeug-Materialien (tools/), Skinned Meshes und Overlays ohne Textur weg,
- nur die Farbtextur, verkleinert auf ``tex_size`` (JPEG, mit Alpha PNG).

Koordinaten: der Render-Export liegt in Metern mit Y oben (glTF). Die
Rueckrechnung in Spieleinheiten (Z oben) kommt aus dem Physik-Export derselben
Map, dessen Knotenmatrix genau diese Umrechnung traegt - so passen Demo-
Positionen ohne Raten der Achsen.
"""

from __future__ import annotations

import gzip
import io
import json
import struct
from pathlib import Path

DROP_MATERIAL = ("materials/tools/", "tools_", "toolsnodraw", "toolsclip", "toolsskybox")
# Umrechnung aus dem Physik-Export (Spaltenvektoren): glTF = M * Spiel. Spiel = M^-1 * glTF.
GAME_FROM_GLTF = ((0, 0, 1), (1, 0, 0), (0, 1, 0))     # zeilenweise: x_spiel = z_gltf usw.
INCH = 0.0254

_DTYPES = {5126: "<f4", 5125: "<u4", 5123: "<u2", 5121: "u1", 5122: "<i2", 5120: "i1"}
_WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
_NORM = {5121: 255.0, 5123: 65535.0, 5120: 127.0, 5122: 32767.0}


def _read_glb(path: Path):
    raw = Path(path).read_bytes()
    jlen = struct.unpack("<I", raw[12:16])[0]
    doc = json.loads(raw[20:20 + jlen])
    return doc, raw[20 + jlen + 8:] if len(raw) > 20 + jlen else b""


def _accessor(np, doc, binary, idx):
    acc = doc["accessors"][idx]
    view = doc["bufferViews"][acc["bufferView"]]
    width = _WIDTH[acc["type"]]
    dt = np.dtype(_DTYPES[acc["componentType"]])
    off = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = view.get("byteStride") or dt.itemsize * width
    count = acc["count"]
    if stride == dt.itemsize * width:
        arr = np.frombuffer(binary, dtype=dt, count=count * width, offset=off).reshape(count, width)
    else:
        raw = np.frombuffer(binary, dtype="u1", count=stride * (count - 1) + dt.itemsize * width, offset=off)
        arr = np.lib.stride_tricks.as_strided(raw, shape=(count, dt.itemsize * width), strides=(stride, 1))
        arr = np.ascontiguousarray(arr).view(dt).reshape(count, width)
    if acc.get("normalized"):
        arr = arr.astype("<f4") / _NORM[acc["componentType"]]
    return arr[:, 0] if width == 1 else arr


def _node_matrix(np, node):
    if "matrix" in node:
        return np.array(node["matrix"], dtype="f8").reshape(4, 4).T
    m = np.eye(4)
    if "scale" in node:
        m = np.diag(list(node["scale"]) + [1.0]) @ m
    if "rotation" in node:
        x, y, z, w = node["rotation"]
        r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        rm = np.eye(4)
        rm[:3, :3] = r
        m = rm @ m
    if "translation" in node:
        t = np.eye(4)
        t[:3, 3] = node["translation"]
        m = t @ m
    return m


def _walk(np, doc):
    """(mesh-Index, Weltmatrix) fuer jeden Knoten mit Mesh, ohne Skins."""
    out = []

    def go(i, parent):
        node = doc["nodes"][i]
        m = parent @ _node_matrix(np, node)
        if "mesh" in node and "skin" not in node:
            out.append((node["mesh"], m))
        for c in node.get("children", []):
            go(c, m)
    for scene in doc.get("scenes", [])[:1]:
        for i in scene["nodes"]:
            go(i, np.eye(4))
    return out


def keep_material(mat: dict) -> bool:
    name = ((mat.get("extras") or {}).get("vmat") or {}).get("Name", "") or mat.get("name", "")
    n = name.lower()
    if any(k in n for k in DROP_MATERIAL):
        return False
    return "baseColorTexture" in (mat.get("pbrMetallicRoughness") or {})


def _texture(src_dir: Path, doc: dict, tex_idx: int, size: int, cache: dict):
    """(Bytes, MIME, hat Alpha) der verkleinerten Farbtextur."""
    from PIL import Image
    img_idx = doc["textures"][tex_idx]["source"]
    if img_idx in cache:
        return cache[img_idx]
    uri = doc["images"][img_idx].get("uri")
    res = None
    if uri and (src_dir / uri).exists():
        im = Image.open(src_dir / uri)
        im.thumbnail((size, size), Image.LANCZOS)
        alpha = im.mode in ("RGBA", "LA") and im.getchannel("A").getextrema()[0] < 250
        buf = io.BytesIO()
        if alpha:
            im.convert("RGBA").save(buf, "PNG", optimize=True)
            res = (buf.getvalue(), "image/png", True)
        else:
            im.convert("RGB").save(buf, "JPEG", quality=82)
            res = (buf.getvalue(), "image/jpeg", False)
    cache[img_idx] = res
    return res


def convert(glb: Path, out: Path, tex_size: int = 512) -> dict:
    """Render-glb (mit Texturen daneben) -> kompaktes glb in Spieleinheiten, Z oben."""
    import numpy as np
    doc, binary = _read_glb(glb)
    src_dir = Path(glb).parent
    conv = np.array(GAME_FROM_GLTF, dtype="f8") / INCH
    groups: dict[int, dict] = {}
    for mesh_idx, world in _walk(np, doc):
        lin, trans = world[:3, :3], world[:3, 3]
        nrm_m = np.linalg.inv(lin).T
        for prim in doc["meshes"][mesh_idx]["primitives"]:
            mi = prim.get("material")
            if mi is None or prim.get("mode", 4) != 4:
                continue
            mat = doc["materials"][mi]
            if not keep_material(mat):
                continue
            attrs = prim["attributes"]
            tc = mat["pbrMetallicRoughness"]["baseColorTexture"].get("texCoord", 0)
            if "POSITION" not in attrs or f"TEXCOORD_{tc}" not in attrs:
                continue
            raw_pos = _accessor(np, doc, binary, attrs["POSITION"])
            idx = _accessor(np, doc, binary, prim["indices"]).astype("<u4") if "indices" in prim \
                else np.arange(len(raw_pos), dtype="<u4")
            # Primitive teilen sich oft den ganzen Vertexpuffer des Meshes - nur die benutzten kopieren
            used, idx = np.unique(idx, return_inverse=True)
            idx = idx.reshape(-1).astype("<u4")
            pos = raw_pos[used].astype("f8") @ lin.T + trans
            pos = pos @ conv.T
            if "NORMAL" in attrs:
                nrm = _accessor(np, doc, binary, attrs["NORMAL"])[used].astype("f8") @ nrm_m.T @ np.array(GAME_FROM_GLTF, "f8").T
                nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)
            else:
                nrm = np.zeros_like(pos)
                nrm[:, 2] = 1
            uv = _accessor(np, doc, binary, attrs[f"TEXCOORD_{tc}"])[used].astype("<f4")
            if np.linalg.det(lin) < 0:      # gespiegelte Instanz: Umlaufsinn umdrehen
                idx = idx.reshape(-1, 3)[:, ::-1].reshape(-1)
            g = groups.setdefault(mi, {"pos": [], "nrm": [], "uv": [], "idx": [], "n": 0})
            g["pos"].append(pos.astype("<f4"))
            g["nrm"].append(np.clip(np.round(nrm * 127), -127, 127).astype("i1"))
            g["uv"].append(uv)
            g["idx"].append(idx + g["n"])
            g["n"] += len(pos)

    # ── neues glb schreiben ──
    bin_parts: list[bytes] = []
    blen = 0

    def add_view(data: bytes, target: int | None = None) -> int:
        nonlocal blen
        pad = (-blen) % 4
        if pad:
            bin_parts.append(b"\0" * pad)
            blen += pad
        view = {"buffer": 0, "byteOffset": blen, "byteLength": len(data)}
        if target:
            view["target"] = target
        bin_parts.append(data)
        blen += len(data)
        out_doc["bufferViews"].append(view)
        return len(out_doc["bufferViews"]) - 1

    def add_acc(view, ctype, count, typ, **kw) -> int:
        out_doc["accessors"].append({"bufferView": view, "componentType": ctype, "count": count, "type": typ, **kw})
        return len(out_doc["accessors"]) - 1

    out_doc = {"asset": {"version": "2.0", "generator": "cs2-coach map3d_render"},
               "extensionsUsed": ["KHR_mesh_quantization"], "extensionsRequired": ["KHR_mesh_quantization"],
               "scene": 0, "scenes": [{"nodes": []}], "nodes": [], "meshes": [], "materials": [],
               "textures": [], "images": [], "samplers": [{"wrapS": 10497, "wrapT": 10497, "minFilter": 9987, "magFilter": 9729}],
               "accessors": [], "bufferViews": [], "buffers": []}
    tex_cache, tex_out = {}, {}
    verts = tris = 0
    for mi, g in groups.items():
        mat = doc["materials"][mi]
        tex = _texture(src_dir, doc, mat["pbrMetallicRoughness"]["baseColorTexture"]["index"], tex_size, tex_cache)
        if tex is None:
            continue
        key = id(tex)
        if key not in tex_out:
            v = add_view(tex[0])
            out_doc["images"].append({"bufferView": v, "mimeType": tex[1]})
            out_doc["textures"].append({"sampler": 0, "source": len(out_doc["images"]) - 1})
            tex_out[key] = len(out_doc["textures"]) - 1
        alpha_mode = mat.get("alphaMode", "OPAQUE")
        if alpha_mode != "OPAQUE" and not tex[2]:
            alpha_mode = "OPAQUE"
        m = {"name": mat.get("name", ""), "pbrMetallicRoughness": {"baseColorTexture": {"index": tex_out[key]},
                                                                   "metallicFactor": 0, "roughnessFactor": 1},
             "alphaMode": alpha_mode, "doubleSided": bool(mat.get("doubleSided"))}
        if alpha_mode == "MASK":
            m["alphaCutoff"] = mat.get("alphaCutoff", 0.5)
        out_doc["materials"].append(m)
        pos = np.concatenate(g["pos"])
        nrm = np.concatenate(g["nrm"])
        nrm4 = np.zeros((len(nrm), 4), "i1")
        nrm4[:, :3] = nrm
        uv = np.concatenate(g["uv"])
        idx = np.concatenate(g["idx"]).astype("<u4")
        a_pos = add_acc(add_view(pos.tobytes(), 34962), 5126, len(pos), "VEC3",
                        min=pos.min(0).tolist(), max=pos.max(0).tolist())
        a_nrm = add_acc(add_view(nrm4.tobytes(), 34962), 5120, len(nrm4), "VEC3", normalized=True)
        out_doc["bufferViews"][-1]["byteStride"] = 4
        a_uv = add_acc(add_view(uv.astype("<f4").tobytes(), 34962), 5126, len(uv), "VEC2")
        a_idx = add_acc(add_view(idx.tobytes(), 34963), 5125, len(idx), "SCALAR")
        out_doc["meshes"].append({"name": m["name"], "primitives": [{"attributes": {"POSITION": a_pos, "NORMAL": a_nrm,
                                  "TEXCOORD_0": a_uv}, "indices": a_idx, "material": len(out_doc["materials"]) - 1}]})
        out_doc["nodes"].append({"mesh": len(out_doc["meshes"]) - 1})
        out_doc["scenes"][0]["nodes"].append(len(out_doc["nodes"]) - 1)
        verts += len(pos)
        tris += len(idx) // 3
    binary_out = b"".join(bin_parts)
    binary_out += b"\0" * ((-len(binary_out)) % 4)
    out_doc["buffers"].append({"byteLength": len(binary_out)})
    js = json.dumps(out_doc, separators=(",", ":")).encode()
    js += b" " * ((-len(js)) % 4)
    blob = (struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(binary_out))
            + struct.pack("<II", len(js), 0x4E4F534A) + js
            + struct.pack("<II", len(binary_out), 0x004E4942) + binary_out)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # vorkomprimiert wie die .geo.gz - der Browser entpackt ueber Content-Encoding
    out.write_bytes(gzip.compress(blob, 6))
    return {"vertices": verts, "triangles": tris, "materials": len(out_doc["materials"]),
            "textures": len(out_doc["images"]), "bytes": out.stat().st_size}


MAX_BYTES = 90 * 1024 * 1024   # Cloudflare (Free) liefert Antworten nur bis 100 MB aus


def convert_capped(glb: Path, out: Path, tex_size: int = 512, max_bytes: int = MAX_BYTES) -> dict:
    """Wie convert, halbiert aber die Texturen, bis die Datei unter ``max_bytes`` liegt (min. 64 px)."""
    size = tex_size
    while True:
        info = convert(glb, out, size)
        info["tex_size"] = size
        if info["bytes"] <= max_bytes or size <= 64:
            return info
        size //= 2


def render_path(cfg: dict, config_path: Path, map_key: str) -> Path | None:
    from .map3d import geo_dir
    p = geo_dir(cfg, config_path) / f"{map_key.lower()}.render.glb.gz"
    return p if p.exists() else None


def main(argv: list[str] | None = None) -> None:
    """``python -m cs2_coach.map3d_render <export-ordner> <ziel-ordner> [textur-px]``: alle <map>/<map>.glb."""
    import sys
    args = argv if argv is not None else sys.argv[1:]
    src, dst = Path(args[0]), Path(args[1])
    size = int(args[2]) if len(args) > 2 else 512
    for glb in sorted(src.glob("*/maps/de_*.glb")):
        if glb.stem.endswith(("_physics", "_vanity")):   # Hauptmenue-Kulissen braucht keine Demo
            continue
        map_key = glb.stem.removeprefix("de_")
        info = convert_capped(glb, dst / f"{map_key}.render.glb.gz", size)
        print(f"{map_key}: {info['vertices']} Vertices, {info['triangles']} Dreiecke, "
              f"{info['materials']} Materialien, {info['textures']} Texturen ({info['tex_size']} px), {info['bytes'] // 1048576} MB"
              + ("  ZU GROSS fuer Cloudflare" if info["bytes"] > MAX_BYTES else ""))


if __name__ == "__main__":
    main()
