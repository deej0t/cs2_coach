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

# Verlauf (6.11.: nach einem Haenger 04:15-07:20 gab es keine Messwerte - nur die letzte
# Momentaufnahme). Eine Zeile pro Lauf, die letzten 1000 (~3,5 Tage bei */5).
LOG="${OUT%.json}.log"
# Temperaturen (6.11. "wie sieht es mit dem Airflow aus"): CPU real (Tdie, Tctl hat beim 1920X
# +27 Grad Versatz), Chipsatz, und die Platten aus Unraids disks.ini - schlafende Platten weckt das nicht.
temps="$(sensors 2>/dev/null | awk '/^Tdie:/{c=$2} /^Chipset Temperature:/{h=$3} END{printf "cpu %s chipset %s", c, h}')"
temps="$temps $(awk -F'"' '/^\[/{n=$2} /^temp=/{if ($2 != "*") printf "%s %s ", n, $2}' /var/local/emhttp/disks.ini 2>/dev/null)"
echo "$(date '+%F %T') load $l1 $l5 $l15 | disks $disks | top $top | D-state $(ps -eo stat= | grep -c '^D') | temp $temps" >> "$LOG"
tail -n 1000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
# Bei Ueberlast zusaetzlich die Prozesse ins Syslog (wird nach /boot gespiegelt, ueberlebt einen Neustart)
if awk -v l="$l5" -v c="$cores" 'BEGIN{exit !(l > c*2)}'; then
    logger -t host-status "Ueberlast: load $l1 $l5 $l15 bei $cores Kernen; top: $top; D-state: $(ps -eo stat=,comm= | awk '$1 ~ /^D/ {print $2}' | sort | uniq -c | sort -rn | head -5 | tr '
' ' '); cpu: $(ps -eo pcpu=,comm= --sort=-pcpu | head -5 | tr -s ' ' | tr '
' ',')"
fi
