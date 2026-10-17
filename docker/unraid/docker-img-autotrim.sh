#!/bin/bash
# docker-img-autotrim: gibt Platz in docker.img sofort zurueck, wenn er
# aufgeblaeht ist - nicht erst beim naechsten geplanten Trim.
#
# Ursache (gefunden 2026-10-17): Unraid ruft bei jedem Start des
# Docker-Dienstes (Neustart, Update, Docker aus/an) in
# /usr/local/sbin/mount_image "fallocate -l 250G docker.img" auf. Das
# belegt alle per TRIM freigegebenen Bereiche wieder: 93 GB genutzt, 249 GB
# belegt. Der Cache lag danach unter dem Mindest-Freiplatz der
# appdata-Freigabe (48,8 GB), und Container konnten keine neuen Dateien
# anlegen. Passiert am 16.10. (Neustart nach Absturz) und 17.10.
# (Update auf 7.3.3).
#
# Laeuft alle 10 Minuten per Cron (docker-img-autotrim.cron). Trimmt nur,
# wenn die Datei mehr als MAX_LUECKE GB ueber der genutzten Menge liegt;
# sonst nur zwei stat-Aufrufe. Ergaenzt cache-wacht.sh (3x taeglich).

set -u
IMG=/mnt/cache/system/docker/docker.img
MAX_LUECKE=${MAX_LUECKE:-20}   # GB

mountpoint -q /var/lib/docker || exit 0
[ -f "$IMG" ] || exit 0

exec 9>/var/run/docker-img-autotrim.lock
flock -n 9 || exit 0

belegt=$(( $(stat -c %b "$IMG") * $(stat -c %B "$IMG") / 1073741824 ))
innen=$(df -BG --output=used /var/lib/docker | tail -1 | tr -dc 0-9)
[ $(( belegt - innen )) -gt "$MAX_LUECKE" ] || exit 0

ausgabe=$(fstrim -v /var/lib/docker 2>&1)
nachher=$(( $(stat -c %b "$IMG") * $(stat -c %B "$IMG") / 1073741824 ))
logger -t docker-img-autotrim "docker.img ${belegt} -> ${nachher} GB (genutzt ${innen} GB): ${ausgabe}"
