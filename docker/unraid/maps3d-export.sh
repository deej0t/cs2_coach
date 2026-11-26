#!/bin/bash
# maps3d-export: Kartengeometrie fuer die 3D-Ansicht der Schluesselmomente (/momente).
#
# Liest die Kollisionsgeometrie (maps/<map>/world_physics.vmdl_c) aus den Map-VPKs
# des CS2-Servers, exportiert sie mit Source2Viewer-CLI als glb und wandelt sie im
# cs2-coach-Container in kompakte .geo.gz um (cs2_coach/map3d.py, ~2-3 MB je Map).
# Source2Viewer liegt auf dem Host, nicht im Image: es braucht die Spieldateien,
# und die gibt es nur hier.
# Zusaetzlich die texturierte Render-Map (map3d_render.py, ~45 MB je Map bei 512er Texturen).
#
# Nach CS2-Updates mit geaenderten Maps einfach erneut ausfuehren (dauert < 1 min).
# Aufruf: bash /boot/config/scripts/maps3d-export.sh [map ...]   (ohne Argument: alle de_-Maps)
set -euo pipefail

mountpoint -q /mnt/user || { echo "/mnt/user nicht gemountet - Abbruch"; exit 1; }

S2V_VERSION="21.1"
TOOLS=/mnt/user/appdata/s2v
MAPS=/mnt/ssdcache/appdata/cs2/game/csgo/maps
DATA=/mnt/user/appdata/cs2-coach/data       # im Container /data
WORK="$DATA/maps3d_glb"

mkdir -p "$TOOLS/bin" "$WORK"
if [ ! -x "$TOOLS/bin/Source2Viewer-CLI" ]; then
    curl -sfLo "$TOOLS/cli.zip" "https://github.com/ValveResourceFormat/ValveResourceFormat/releases/download/$S2V_VERSION/cli-linux-x64.zip"
    unzip -oq "$TOOLS/cli.zip" -d "$TOOLS/bin"
    chmod +x "$TOOLS/bin/Source2Viewer-CLI"
fi

if [ "$#" -gt 0 ]; then names=("$@"); else names=(); for f in "$MAPS"/de_*.vpk; do n=$(basename "$f" .vpk); case "$n" in *_vanity) continue;; esac; names+=("$n"); done; fi

for n in "${names[@]}"; do
    n="de_${n#de_}"
    vpk="$MAPS/$n.vpk"
    [ -f "$vpk" ] || { echo "$n: kein VPK"; continue; }
    rm -rf "$WORK/tmp"
    "$TOOLS/bin/Source2Viewer-CLI" -i "$vpk" -f "maps/$n/world_physics.vmdl_c" \
        --gltf_export_format glb -o "$WORK/tmp" >/dev/null
    mkdir -p "$WORK/$n"
    mv "$WORK/tmp/maps/$n/world_physics_physics.glb" "$WORK/$n/" && echo "$n: Kollision exportiert"
    # Texturierte Render-Geometrie (map3d_render.py): Map samt Materialien; die Texturen
    # liegen in pak01 - Source2Viewer findet sie ueber den Spielordner neben dem Map-VPK
    rm -rf "$WORK/render/$n"
    "$TOOLS/bin/Source2Viewer-CLI" -i "$vpk" -f "maps/$n.vmap_c"         --gltf_export_format glb --gltf_textures_adapt -o "$WORK/render/$n" >/dev/null         && echo "$n: Render-Map exportiert" || echo "$n: Render-Export fehlgeschlagen"
done
rm -rf "$WORK/tmp"

# Spielermodelle, Waffen, Animationen (models3d.py) aus pak01 - die Liste kennt der Container
PAK="$(dirname "$MAPS")/pak01_dir.vpk"
if [ "$#" -eq 0 ] && [ -f "$PAK" ]; then
    list=$(docker exec cs2-coach python -m cs2_coach.models3d --list | paste -sd, -)
    rm -rf "$WORK/models"
    "$TOOLS/bin/Source2Viewer-CLI" -i "$PAK" -f "$list" --gltf_export_format glb --gltf_textures_adapt         -o "$WORK/models" >/dev/null && echo "Modelle exportiert" || echo "Modell-Export fehlgeschlagen"
fi

# Umwandeln im Container (numpy ist dort vorhanden); Ziel = maps3d/ neben der Config
docker exec cs2-coach python -m cs2_coach.map3d /data/maps3d_glb /data/maps3d
# Texturen-Kantenlaenge: MAPS3D_TEX=256 halbiert die Downloadgroesse (~45 MB -> ~20 MB je Map)
docker exec cs2-coach python -m cs2_coach.map3d_render /data/maps3d_glb/render /data/maps3d "${MAPS3D_TEX:-512}"
[ -d "$WORK/models" ] && docker exec cs2-coach python -m cs2_coach.models3d /data/maps3d_glb/models /data/maps3d
rm -rf "$WORK"
