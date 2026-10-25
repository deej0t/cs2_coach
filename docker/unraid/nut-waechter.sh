#!/bin/bash
# nut-waechter: startet NUT neu, wenn die USV laenger keine Daten liefert.
#
# Anlass: Die USB-Verbindung zur USV (CyberPower UT850EG) brach dreimal ab
# (14.10., 16.10., 25.10.). NUT meldete danach nur noch "Data stale" und
# war blind. Am 25.10. flog um 14:57 die Sicherung - die USV uebernahm,
# aber der Server bekam den Stromausfall nicht mit, fuhr nicht herunter
# und ging aus, als der Akku leer war. Die Verbindung war da schon seit
# 08:32 tot. Ein Neustart von NUT hat jedes Mal geholfen; ein USB-Port-
# Wechsel nicht.
#
# Laeuft alle 2 Minuten per Cron (nut-waechter.cron). Liefert NUT seit
# GRENZE Sekunden keine Daten, wird NUT neu gestartet - hoechstens alle
# PAUSE Sekunden. Meldet sich ueber die Unraid-Benachrichtigung.
# Tut nichts, wenn NUT in den Einstellungen ausgeschaltet ist.

set -u
CFG=/boot/config/plugins/nut-dw/nut-dw.cfg
SEIT=/var/run/nut-waechter.since
LETZTER=/var/run/nut-waechter.last-restart
GRENZE=${GRENZE:-300}   # Sekunden ohne Daten bis zum Neustart
PAUSE=${PAUSE:-900}     # Mindestabstand zwischen zwei Neustarts
NOTIFY=/usr/local/emhttp/webGui/scripts/notify

grep -q '^SERVICE="enable"' "$CFG" 2>/dev/null || exit 0

exec 9>/var/run/nut-waechter.lock
flock -n 9 || exit 0

# Gesund = upsc liefert einen Status wie "OL", "OB DISCHRG", "OL CHRG".
# Fehler beginnen mit "Error:" ("Data stale", "Connection failure", ...).
status_lesen() { timeout 15 upsc ups@127.0.0.1 ups.status 2>&1 | head -1; }
gesund() { [ -n "$1" ] && [ "${1#Error}" = "$1" ]; }

jetzt=$(date +%s)
status=$(status_lesen)
if gesund "$status"; then
    rm -f "$SEIT"
    exit 0
fi

if [ ! -f "$SEIT" ]; then
    echo "$jetzt" > "$SEIT"
    logger -t nut-waechter "USV liefert keine Daten: $status"
    exit 0
fi
dauer=$(( jetzt - $(cat "$SEIT") ))
[ "$dauer" -ge "$GRENZE" ] || exit 0
if [ -f "$LETZTER" ] && [ $(( jetzt - $(cat "$LETZTER") )) -lt "$PAUSE" ]; then
    exit 0
fi
echo "$jetzt" > "$LETZTER"

logger -t nut-waechter "Seit ${dauer}s keine USV-Daten ($status) - starte NUT neu"
/etc/rc.d/rc.nut stop  >/dev/null 2>&1
/etc/rc.d/rc.nut start >/dev/null 2>&1
sleep 20
neu=$(status_lesen)
if ! gesund "$neu"; then
    # Am 16.10. kam NUT erst beim zweiten "start" wieder hoch.
    /etc/rc.d/rc.nut start >/dev/null 2>&1
    sleep 20
    neu=$(status_lesen)
fi

if gesund "$neu"; then
    rm -f "$SEIT"
    logger -t nut-waechter "NUT neu gestartet, USV liefert wieder Daten: $neu"
    "$NOTIFY" -e "NUT-Waechter" -s "USV-Verbindung wiederhergestellt" \
        -d "NUT lieferte ${dauer}s keine Daten und wurde neu gestartet. Status jetzt: $neu" -i normal
else
    logger -t nut-waechter "NUT neu gestartet, USV liefert weiterhin keine Daten: $neu"
    "$NOTIFY" -e "NUT-Waechter" -s "USV liefert keine Daten" \
        -d "Auch nach Neustart von NUT keine Daten ($neu). Bei Stromausfall faehrt der Server NICHT sauber herunter. USB-Kabel zur USV pruefen." -i alert
fi
