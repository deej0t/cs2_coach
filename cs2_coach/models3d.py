"""Spielermodelle, Waffen und Animationen fuer die 3D-Ansicht der Schluesselmomente.

Quelle: pak01 des CS2-Servers, mit Source2Viewer-CLI als glb exportiert
(docker/unraid/maps3d-export.sh). Je Seite ein Agent (CT: SAS, T: Phoenix),
alle Waffenmodelle und je Waffenklasse (Gewehr, Pistole, Messer) die
Bewegungs-Clips Stehen/Ducken/Laufen in vier Richtungen. Die Clips tragen
Knochennamen wie die Agenten-Skelette - three.js spielt sie per Name auf
jedem Agenten ab.

``shrink`` bettet nur die Farbtextur ein (verkleinert), wirft Normal-/AO-/
Metall-Texturen weg und laesst Skelett und Animation unangetastet.
Ausgabe: ``<ziel>/models/<name>.glb.gz`` plus ``manifest.json``.
"""

from __future__ import annotations

import gzip
import io
import json
import struct
from pathlib import Path

AGENTS = {"ct": "agents/models/ctm_sas/ctm_sas", "t": "agents/models/tm_phoenix/tm_phoenix"}

# Demo-Waffenname (active_weapon_name ohne "weapon_", klein) -> Modellpfad im VPK
WEAPONS = {
    "ak47": "weapons/models/ak47/weapon_rif_ak47", "aug": "weapons/models/aug/weapon_rif_aug",
    "awp": "weapons/models/awp/weapon_snip_awp", "bizon": "weapons/models/bizon/weapon_smg_bizon",
    "c4": "weapons/models/c4/weapon_c4", "cz75a": "weapons/models/cz75a/weapon_pist_cz75a",
    "deagle": "weapons/models/deagle/weapon_pist_deagle", "elite": "weapons/models/elite/weapon_pist_elite",
    "famas": "weapons/models/famas/weapon_rif_famas", "fiveseven": "weapons/models/fiveseven/weapon_pist_fiveseven",
    "g3sg1": "weapons/models/g3sg1/weapon_snip_g3sg1", "galilar": "weapons/models/galilar/weapon_rif_galilar",
    "glock": "weapons/models/glock18/weapon_pist_glock18", "hkp2000": "weapons/models/hkp2000/weapon_pist_hkp2000",
    "m249": "weapons/models/m249/weapon_mach_m249", "m4a1_silencer": "weapons/models/m4a1_silencer/weapon_rif_m4a1_silencer",
    "m4a1": "weapons/models/m4a4/weapon_rif_m4a4", "mac10": "weapons/models/mac10/weapon_smg_mac10",
    "mp5sd": "weapons/models/mp5sd/weapon_smg_mp5sd", "mp7": "weapons/models/mp7/weapon_smg_mp7",
    "mp9": "weapons/models/mp9/weapon_smg_mp9", "negev": "weapons/models/negev/weapon_mach_negev",
    "nova": "weapons/models/nova/weapon_shot_nova", "p250": "weapons/models/p250/weapon_pist_p250",
    "p90": "weapons/models/p90/weapon_smg_p90", "revolver": "weapons/models/revolver/weapon_pist_revolver",
    "sawedoff": "weapons/models/sawedoff/weapon_shot_sawedoff", "scar20": "weapons/models/scar20/weapon_snip_scar20",
    "sg556": "weapons/models/sg556/weapon_rif_sg556", "ssg08": "weapons/models/ssg08/weapon_snip_ssg08",
    "taser": "weapons/models/taser/weapon_pist_taser", "tec9": "weapons/models/tec9/weapon_pist_tec9",
    "ump45": "weapons/models/ump45/weapon_smg_ump45", "usp_silencer": "weapons/models/usp_silencer/weapon_pist_usp_silencer",
    "xm1014": "weapons/models/xm1014/weapon_shot_xm1014", "mag7": "weapons/models/mag7/weapon_shot_mag7",
    "knife_ct": "weapons/models/knife/knife_default_ct/weapon_knife_default_ct",
    "knife_t": "weapons/models/knife/knife_default_t/weapon_knife_default_t",
    "flashbang": "weapons/models/grenade/flashbang/weapon_flashbang",
    "hegrenade": "weapons/models/grenade/hegrenade/weapon_hegrenade",
    "smokegrenade": "weapons/models/grenade/smokegrenade/weapon_smokegrenade",
    "molotov": "weapons/models/grenade/molotov/weapon_molotov",
    "incgrenade": "weapons/models/grenade/incendiary/weapon_incendiarygrenade",
    "decoy": "weapons/models/grenade/decoy/weapon_decoy",
}
PISTOLS = {"cz75a", "deagle", "elite", "fiveseven", "glock", "hkp2000", "p250", "revolver", "taser", "tec9", "usp_silencer"}
MOVES = ("idle", "idle_crouch", "run_n", "run_s", "run_e", "run_w", "crouch_n", "crouch_s", "crouch_e", "crouch_w")
CLASSES = ("rifle", "pistol", "knife")

# Namen, die die Demo anders schreibt als die Modelle
ALIASES = {"m4a4": "m4a1", "usp": "usp_silencer", "usps": "usp_silencer", "p2000": "hkp2000", "glock18": "glock",
           "galil": "galilar", "galil_ar": "galilar", "dualies": "elite", "dual_berettas": "elite",
           "zeus": "taser", "zeus_x27": "taser", "incendiary": "incgrenade", "incendiary_grenade": "incgrenade",
           "smoke": "smokegrenade", "smoke_grenade": "smokegrenade", "he": "hegrenade",
           "high_explosive_grenade": "hegrenade", "decoy_grenade": "decoy", "c4_explosive": "c4",
           "desert_eagle": "deagle", "cz75auto": "cz75a", "r8_revolver": "revolver", "m4a1s": "m4a1_silencer",
           "sg_553": "sg556", "sg553": "sg556", "ssg_08": "ssg08", "ppbizon": "bizon", "pp_bizon": "bizon",
           "mag7": "mag7", "sawed_off": "sawedoff"}


def clip_paths() -> dict[str, str]:
    """Clip-Name (z. B. ``rifle_run_n``) -> Pfad im VPK (ohne _c-Endung)."""
    return {f"{c}_{m}": f"animation/anims/world/{c}/_default_{c}/{m}_{c}.vnmclip" for c in CLASSES for m in MOVES}


def weapon_key(name: str | None) -> str | None:
    """Demo-Waffenname -> Schluessel in WEAPONS (oder None)."""
    if not name:
        return None
    n = str(name).lower().removeprefix("weapon_").replace(" ", "_").replace("-", "")
    n = ALIASES.get(n, n)
    if any(k in n for k in ("knife", "bayonet", "karambit", "dagger")):
        return "knife"      # Seite entscheidet der Browser (knife_ct / knife_t)
    return n if n in WEAPONS else None


def weapon_class(key: str | None) -> str:
    """Animationsklasse fuer eine Waffe."""
    if key in PISTOLS:
        return "pistol"
    if key and (key.startswith("knife") or key in ("c4", "flashbang", "hegrenade", "smokegrenade",
                                                    "molotov", "incgrenade", "decoy")):
        return "knife"
    return "rifle"


# HUD-Symbole aus dem Spiel (Vektorgrafik): alle Waffen/Ausruestung plus diese HUD-Teile
ICON_DIR = "panorama/images/icons/equipment/"
HUD_ICONS = ("health_cross", "armor", "armor_helmet", "ammo_reserve_magazine", "deathnotice/icon_headshot",
             "deathnotice/blind_kill", "deathnotice/smoke_kill", "deathnotice/noscope", "deathnotice/inairkill")


def export_list() -> list[str]:
    """Alle VPK-Pfade (mit _c, Ordner als Praefix), die der Export braucht - fuer das Unraid-Skript."""
    out = [p + ".vmdl_c" for p in AGENTS.values()] + [p + ".vmdl_c" for p in WEAPONS.values()]
    out += [ICON_DIR] + [f"panorama/images/hud/{n}.vsvg_c" for n in HUD_ICONS]
    return out + [p + "_c" for p in clip_paths().values()]


def collect_icons(src: Path) -> dict[str, str]:
    """SVG-Texte: Ausruestung unter ihrem Namen (``ak47``), HUD-Teile als ``hud_<name>``."""
    icons = {}
    for f in sorted((Path(src) / ICON_DIR).glob("*.svg")):
        icons[f.stem] = f.read_text(encoding="utf-8", errors="replace")
    for n in HUD_ICONS:
        f = Path(src) / "panorama/images/hud" / f"{n}.svg"
        if f.exists():
            icons["hud_" + n.split("/")[-1]] = f.read_text(encoding="utf-8", errors="replace")
    return icons


def _read_glb(path: Path):
    raw = Path(path).read_bytes()
    jlen = struct.unpack("<I", raw[12:16])[0]
    doc = json.loads(raw[20:20 + jlen])
    return doc, raw[20 + jlen + 8:] if len(raw) > 20 + jlen else b""


def _write_glb(doc: dict, binary: bytes) -> bytes:
    binary += b"\0" * ((-len(binary)) % 4)
    doc["buffers"] = [{"byteLength": len(binary)}] if binary else []
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * ((-len(js)) % 4)
    return (struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + (8 + len(binary) if binary else 0))
            + struct.pack("<II", len(js), 0x4E4F534A) + js
            + (struct.pack("<II", len(binary), 0x004E4942) + binary if binary else b""))


def shrink(glb: Path, tex_size: int = 512, drop_meshes: bool = False) -> bytes:
    """glb mit externen Texturen -> eigenstaendiges glb, nur Farbtexturen, verkleinert.

    ``drop_meshes``: fuer Animations-Clips - nur Knoten, Skin und Animation bleiben.
    """
    from PIL import Image
    doc, binary = _read_glb(glb)
    src = Path(glb).parent
    bin_out = bytearray(binary)
    if drop_meshes:
        for n in doc.get("nodes", []):
            n.pop("mesh", None)
            n.pop("skin", None)
        for k in ("meshes", "materials", "textures", "images", "samplers", "skins"):
            doc.pop(k, None)
        return _write_glb(doc, bytes(bin_out))
    used = set()
    for m in doc.get("materials", []):
        pbr = m.get("pbrMetallicRoughness") or {}
        for k in ("normalTexture", "occlusionTexture", "emissiveTexture"):
            m.pop(k, None)
        pbr.pop("metallicRoughnessTexture", None)
        pbr["metallicFactor"] = 0
        pbr["roughnessFactor"] = 1
        if "baseColorTexture" in pbr:
            used.add(doc["textures"][pbr["baseColorTexture"]["index"]]["source"])
    for i, img in enumerate(doc.get("images", [])):
        uri = img.pop("uri", None)
        data, mime = b"", "image/png"
        if i in used and uri and (src / uri).exists():
            im = Image.open(src / uri)
            im.thumbnail((tex_size, tex_size), Image.LANCZOS)
            buf = io.BytesIO()
            if im.mode in ("RGBA", "LA") and im.getchannel("A").getextrema()[0] < 250:
                im.convert("RGBA").save(buf, "PNG", optimize=True)
            else:
                im.convert("RGB").save(buf, "JPEG", quality=82)
                mime = "image/jpeg"
            data = buf.getvalue()
        else:   # ungenutzt: 1x1-Pixel statt der Datei
            Image.new("RGB", (1, 1), (128, 128, 128)).save(buf := io.BytesIO(), "PNG")
            data = buf.getvalue()
        while len(bin_out) % 4:
            bin_out.append(0)
        doc.setdefault("bufferViews", []).append({"buffer": 0, "byteOffset": len(bin_out), "byteLength": len(data)})
        bin_out.extend(data)
        img["bufferView"] = len(doc["bufferViews"]) - 1
        img["mimeType"] = mime
    return _write_glb(doc, bytes(bin_out))


def convert_all(src: Path, dst: Path, tex_size: int = 512) -> dict:
    """Export-Ordner (VPK-Pfade als Unterordner) -> ``dst/models/*.glb.gz`` + ``dst/models/manifest.json``."""
    src, out_dir = Path(src), Path(dst) / "models"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"agents": {}, "weapons": {}, "clips": {}}

    def one(kind: str, key: str, path: str, **kw):
        glb = src / (path + ".glb")
        if not glb.exists():
            return
        name = f"{kind}_{key}.glb.gz"
        (out_dir / name).write_bytes(gzip.compress(shrink(glb, tex_size, **kw), 6))
        manifest[kind + "s"][key] = name
    for side, path in AGENTS.items():
        one("agent", side, path)
    for key, path in WEAPONS.items():
        one("weapon", key, path, )
    for key, path in clip_paths().items():
        one("clip", key, path.removesuffix(".vnmclip"), drop_meshes=True)
    icons = collect_icons(src)
    if icons:
        (out_dir / "icons.json").write_text(json.dumps(icons, separators=(",", ":")), encoding="utf-8")
        manifest["icons"] = "icons.json"
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def models_dir(cfg: dict, config_path: Path) -> Path:
    from .map3d import geo_dir
    return geo_dir(cfg, config_path) / "models"


def main(argv: list[str] | None = None) -> None:
    """``python -m cs2_coach.models3d <export-ordner> <ziel-ordner> [textur-px]`` oder ``--list``."""
    import sys
    args = argv if argv is not None else sys.argv[1:]
    if args and args[0] == "--list":
        print("\n".join(export_list()))
        return
    m = convert_all(Path(args[0]), Path(args[1]), int(args[2]) if len(args) > 2 else 512)
    print(f"{len(m['agents'])} Agenten, {len(m['weapons'])} Waffen, {len(m['clips'])} Clips")


if __name__ == "__main__":
    main()
