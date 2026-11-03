#!/bin/bash
# host-status: schreibt alle 5 Minuten Last, Plattenauslastung, die CPU-staerksten
# Container und SMART-Werte als JSON neben die Coach-Config. Der Coach-Container
# sieht davon selbst nichts (kein Docker-Socket, kein smartctl) - die Statuspruefung
# (health.check_host) liest die Datei und meldet per Discord.
#
# Anlass: 3.11.2026 war der Server "haengend" - in Wahrheit Last ueber 200, weil
# sabnzbd direkt aufs Parity-Array entpackte. Ohne Messwerte sah das aus wie ein
# Absturz. Cron: /boot/config/plugins/dynamix/host-status.cron (*/5).

OUT=${OUT:-/mnt/user/appdata/cs2-coach/data/host_status.json}
TMP="$OUT.tmp"

read -r l1 l5 l15 _ < /proc/loadavg
cores=$(nproc)
# Auslastung je Platte ueber 5 s (zweiter iostat-Block = Messintervall, nicht seit Boot)
disks=$(iostat -dx 5 2 2>/dev/null | awk '/^Device/{n++} n==2 && /^(sd[a-z]+|nvme[0-9]+n1) / {printf "%s\"%s\":%.0f", (c++?",":""), $1, $NF}')
top=$(docker stats --no-stream --format '{{.Name}} {{.CPUPerc}}' 2>/dev/null | tr -d '%' | sort -k2 -rn | head -5 \
      | awk '{printf "%s[\"%s\",%.0f]", (c++?",":""), $1, $2}')
smart=""
for d in /dev/sd[a-z] /dev/nvme[0-9]n1; do
    [ -b "$d" ] || continue
    a=$(smartctl -A -n standby "$d" 2>/dev/null) || true
    echo "$a" | grep -q "STANDBY" && continue   # schlafende Platte nicht wecken
    re=$(echo "$a" | awk '/Reallocated_Sector_Ct/ {print $10}')
    pe=$(echo "$a" | awk '/Current_Pending_Sector/ {print $10}')
    un=$(echo "$a" | awk '/Offline_Uncorrectable/ {print $10}')
    [ -z "$re$pe$un" ] && continue
    smart="$smart${smart:+,}\"${d#/dev/}\":{\"realloc\":${re:-0},\"pending\":${pe:-0},\"uncorrectable\":${un:-0}}"
done
cat > "$TMP" <<EOF
{"time": $(date +%s), "cores": $cores, "load": [$l1, $l5, $l15], "disks": {$disks}, "top": [$top], "smart": {$smart}}
EOF
mv "$TMP" "$OUT"
