#!/bin/bash
# server-demos: schiebt fertige GOTV-Demos (coach_*.dem) vom CS2-Server zum Coach.
#
# Der Coach startet die Aufnahme per RCON (cs2_coach/server_aim.py), CS2 schreibt
# sie in seinen Spielordner - den sieht der cs2-coach-Container nicht. Fertig =
# 5 Minuten nicht mehr geschrieben (Mapwechsel oder tv_stoprecord, wenn der letzte
# Mensch geht). Der Coach wertet sie beim naechsten Aufruf von /server aus und
# loescht sie danach. Cron: */2 (server-demos.cron), mit bash (FAT, kein exec-Bit).
set -u
SRC=${SRC:-/mnt/ssdcache/appdata/cs2/game/csgo}
DST=${DST:-/mnt/user/appdata/cs2-coach/data/server_demos}
mkdir -p "$DST"
find "$SRC" -maxdepth 1 -name 'coach_*.dem' -mmin +5 -print0 | while IFS= read -r -d '' f; do
    mv "$f" "$DST/" && logger -t server-demos "$(basename "$f") ($(du -h "$DST/$(basename "$f")" | cut -f1)) an den Coach"
done
