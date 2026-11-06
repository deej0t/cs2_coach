#!/bin/bash
# luefter: regelt die beiden Gehaeuseluefter (IT8665E pwm2/pwm3) nach der heissesten Stelle,
# die das BIOS nicht sieht: Festplatten (Unraids disks.ini, weckt nichts), NVMe, dazu die CPU.
# CPU-Luefter (pwm1) und Chipsatz-Luefter (pwm4) bleiben bei der Chip-/BIOS-Automatik.
#
# Anlass 6.11.: "Ich wuerde den Airflow gern durch dich regeln lassen". Vorher liefen die
# Gehaeuseluefter dauerhaft auf 100 % (pwm_enable 0). Treiber: Unraid-Plugin "IT87 Drivers"
# (ich777), erkennt den IT8665E des ROG Zenith Extreme.
#
# Sicherheit: jede fehlende Messung, ein Luefter mit 0 U/min bei Ansteuerung oder ein Fehler
# -> 100 %. Nie unter MIN. Cron: */1, /boot/config/plugins/dynamix/luefter.cron (mit bash!).

MIN=${MIN:-35}          # % - darunter laufen manche Luefter nicht sicher an
STATE=${STATE:-/var/run/luefter.state}
DRYRUN=${DRYRUN:-0}
CHANNELS="2 3"

H=""
for h in /sys/class/hwmon/hwmon*; do [ "$(cat "$h/name" 2>/dev/null)" = "it8665" ] && H=$h; done

# Temperatur -> Prozent: linear zwischen "ab" (MIN) und "voll" (100 %)
kurve() { awk -v t="$1" -v lo="$2" -v hi="$3" -v m="$MIN" 'BEGIN{
    if (t <= lo) p = m; else if (t >= hi) p = 100; else p = m + (100 - m) * (t - lo) / (hi - lo);
    printf "%d", p }'; }

ziel=100
grund="Fallback"
if [ -n "$H" ]; then
    hdd=$(awk -F'"' '/^\[/{n=$2} /^temp=/{if ($2 != "*" && n !~ /cache|flash/) print $2}' /var/local/emhttp/disks.ini 2>/dev/null | sort -n | tail -1)
    nvme=$(sensors 2>/dev/null | awk '/^Composite:/{gsub(/[+°C]/,"",$2); print int($2)}' | sort -n | tail -1)
    cpu=$(sensors 2>/dev/null | awk '/^Tdie:/{gsub(/[+°C]/,"",$2); print int($2); exit}')
    if [ -n "$nvme" ] && [ -n "$cpu" ]; then
        # HDD 36->45 C, NVMe 50->65 C, CPU (Tdie) 60->80 C; schlafende Platten zaehlen nicht
        p_nvme=$(kurve "$nvme" 50 65)
        p_cpu=$(kurve "$cpu" 60 80)
        p_hdd=$MIN; [ -n "$hdd" ] && p_hdd=$(kurve "$hdd" 36 45)
        ziel=$(printf "%s\n%s\n%s\n" "$p_hdd" "$p_nvme" "$p_cpu" | sort -n | tail -1)
        grund="hdd ${hdd:-schlaeft} nvme $nvme cpu $cpu"
    fi
    # Luefter steht trotz Ansteuerung? -> sofort voll
    for c in $CHANNELS; do
        [ "$(cat "$H/pwm$c")" -gt 60 ] && [ "$(cat "$H/fan${c}_input" 2>/dev/null || echo 0)" = 0 ] && { ziel=100; grund="Luefter $c steht"; }
    done
fi

pwm=$(( ziel * 255 / 100 ))
if [ "$DRYRUN" = 1 ]; then echo "ziel $ziel % ($grund) -> pwm $pwm"; exit 0; fi
if [ -z "$H" ]; then
    logger -t luefter "IT8665E nicht gefunden (Treiber geladen?) - Luefter bleiben, wie sie sind"
    exit 1
fi
for c in $CHANNELS; do
    echo 1 > "$H/pwm${c}_enable" 2>/dev/null
    echo "$pwm" > "$H/pwm$c"
done
alt=$(cat "$STATE" 2>/dev/null)
# Nur groessere Spruenge ins Syslog, sonst jede Minute eine Zeile
if [ -z "$alt" ] || [ $(( ziel > alt ? ziel - alt : alt - ziel )) -ge 15 ]; then
    logger -t luefter "Gehaeuseluefter $ziel % ($grund)"
    echo "$ziel" > "$STATE"
fi
