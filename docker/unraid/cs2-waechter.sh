#!/bin/bash
# cs2-waechter: startet den CS2-Server neu, wenn er nicht mehr antwortet.
#
# Anlass: Am 30.10.2026 um 06:29 hing der Server beim Mapwechsel von der
# Workshop-Map aim_multigame auf de_mirage ("WatchDog! Server took too long
# to process"). Der Prozess lief weiter, antwortete aber weder auf RCON noch
# auf Serverabfragen - der Container galt als "Up", niemand merkte etwas.
#
# Laeuft alle 2 Minuten per Cron (cs2-waechter.cron). Antwortet der Server
# seit GRENZE Sekunden nicht auf A2S_INFO (dieselbe Abfrage wie der
# Server-Browser), wird der Container neu gestartet - hoechstens alle PAUSE
# Sekunden. In den ersten ANLAUF Sekunden nach dem Containerstart wird nicht
# geprueft: SteamCMD aktualisiert dann ggf. noch das Spiel.
# Tut nichts, wenn der Container gestoppt ist (absichtlich aus).

set -u
NAME=${NAME:-CS2}
PORT=${PORT:-27015}
SEIT=/var/run/cs2-waechter.since
LETZTER=/var/run/cs2-waechter.last-restart
GRENZE=${GRENZE:-240}   # Sekunden ohne Antwort bis zum Neustart
PAUSE=${PAUSE:-900}     # Mindestabstand zwischen zwei Neustarts
ANLAUF=${ANLAUF:-600}   # Schonfrist nach Containerstart
NOTIFY=/usr/local/emhttp/webGui/scripts/notify
DRYRUN=${DRYRUN:-0}

exec 9>/var/run/cs2-waechter.lock
flock -n 9 || exit 0

[ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = "true" ] || { rm -f "$SEIT"; exit 0; }

jetzt=$(date +%s)
gestartet=$(date -d "$(docker inspect -f '{{.State.StartedAt}}' "$NAME")" +%s)
[ $(( jetzt - gestartet )) -ge "$ANLAUF" ] || exit 0

# Jede Antwort zaehlt - auch die Challenge ("A"), die seit 2020 vor der
# eigentlichen Info kommt. Ein haengender Server antwortet gar nicht.
antwortet() {
    python3 - "$PORT" <<'EOF' 2>/dev/null
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(4)
s.sendto(b"\xff\xff\xff\xffTSource Engine Query\x00", ("127.0.0.1", int(sys.argv[1])))
sys.exit(0 if len(s.recv(1400)) >= 5 else 1)
EOF
}

# Zweimal fragen: ein einzelnes verlorenes UDP-Paket ist kein Haenger.
if antwortet || antwortet; then
    rm -f "$SEIT"
    exit 0
fi

if [ ! -f "$SEIT" ]; then
    echo "$jetzt" > "$SEIT"
    logger -t cs2-waechter "CS2-Server antwortet nicht auf Port $PORT"
    exit 0
fi
dauer=$(( jetzt - $(cat "$SEIT") ))
[ "$dauer" -ge "$GRENZE" ] || exit 0
if [ -f "$LETZTER" ] && [ $(( jetzt - $(cat "$LETZTER") )) -lt "$PAUSE" ]; then
    exit 0
fi
if [ "$DRYRUN" = 1 ]; then
    echo "DRYRUN: wuerde $NAME neu starten (seit ${dauer}s keine Antwort)"
    exit 0
fi
echo "$jetzt" > "$LETZTER"
rm -f "$SEIT"

logger -t cs2-waechter "Seit ${dauer}s keine Antwort - starte $NAME neu"
docker restart -t 20 "$NAME" >/dev/null 2>&1
"$NOTIFY" -e "CS2-Waechter" -s "CS2-Server neu gestartet" \
    -d "Der Server antwortete ${dauer}s nicht (haengender Prozess) und wurde neu gestartet." -i warning
