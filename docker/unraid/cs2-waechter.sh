#!/bin/bash
# cs2-waechter: haelt den CS2-Server am Laufen - mit und ohne Trainings-Plugin.
#
# 1. Haenger: Am 30.10.2026 um 06:29 hing der Server beim Mapwechsel von der
#    Workshop-Map aim_multigame auf de_mirage ("WatchDog! Server took too long
#    to process"). Der Prozess lief weiter, antwortete aber weder auf RCON noch
#    auf Serverabfragen - der Container galt als "Up". Antwortet der Server
#    GRENZE Sekunden nicht auf A2S_INFO, wird der Container neu gestartet.
#
# 2. Metamod (Trainings-Plugin, server_plugin/): Metamod wird ueber eine Zeile
#    in game/csgo/gameinfo.gi geladen.
#    - Ein CS2-Update ersetzt gameinfo.gi -> Metamod fehlt, der Server laeuft
#      ohne Training. Die Zeile wird wieder eingetragen; neu gestartet wird
#      erst, wenn niemand spielt.
#    - Nach CS2-Updates ist Metamod oft ein, zwei Tage inkompatibel: der Server
#      stuerzt beim Start ab, Docker startet ihn in einer Schleife neu. Dann
#      wird Metamod abgeschaltet (Zeile raus), damit der Warmup-Server laeuft.
#    - Abgeschaltet: alle RETRY Sekunden die neuesten Metamod- und
#      CounterStrikeSharp-Builds holen und es erneut versuchen.
#
# Laeuft alle 2 Minuten per Cron (cs2-waechter.cron). Tut nichts, wenn der
# Container gestoppt ist (absichtlich aus). Meldet sich ueber Unraid-notify.

set -u
NAME=${NAME:-CS2}
PORT=${PORT:-27015}
GAME=${GAME:-/mnt/cache/appdata/cs2/game/csgo}
GAMEINFO=${GAMEINFO:-$GAME/gameinfo.gi}
RUN=${RUN:-/var/run}
SEIT=$RUN/cs2-waechter.since
LETZTER=$RUN/cs2-waechter.last-restart
NEUSTARTS=$RUN/cs2-waechter.restarts      # "<zeit> <RestartCount>" der letzten Pruefung
AUS=${AUS:-/boot/config/custom/cs2-metamod.disabled}
GRENZE=${GRENZE:-240}   # Sekunden ohne Antwort bis zum Neustart
PAUSE=${PAUSE:-900}     # Mindestabstand zwischen zwei Neustarts
ANLAUF=${ANLAUF:-600}   # Schonfrist nach Containerstart (SteamCMD-Update)
SCHLEIFE=${SCHLEIFE:-3} # so viele Docker-Neustarts in 15 Minuten = Absturzschleife
RETRY=${RETRY:-21600}   # abgeschaltetes Metamod alle 6 h neu versuchen
NOTIFY=/usr/local/emhttp/webGui/scripts/notify
DRYRUN=${DRYRUN:-0}

melde() { logger -t cs2-waechter "$2"; [ "$DRYRUN" = 1 ] && echo "MELDUNG: $2" || "$NOTIFY" -e "CS2-Waechter" -s "$1" -d "$2" -i "${3:-warning}"; }
neustart() { [ "$DRYRUN" = 1 ] && { echo "DRYRUN: neustart"; return; }; date +%s > "$LETZTER"; docker restart -t 20 "$NAME" >/dev/null 2>&1; }

metamod_an()  { grep -q "csgo/addons/metamod" "$GAMEINFO"; }
metamod_ein() { metamod_an || sed -i 's#^\(\t*\)Game_LowViolence\tcsgo_lv // Perfect World content override#&\n\1Game\tcsgo/addons/metamod#' "$GAMEINFO"; metamod_an; }
metamod_aus() { sed -i '/csgo\/addons\/metamod/d' "$GAMEINFO"; }

# Neueste Builds holen. Eigene Plugins (plugins/CoachTeleport) und configs
# bleiben, weil nur die Dateien aus den Archiven ueberschrieben werden.
plugins_aktualisieren() {
    local tmp; tmp=$(mktemp -d) || return 1
    local mms css
    mms=$(curl -sf https://mms.alliedmods.net/mmsdrop/2.0/mmsource-latest-linux) || { rm -rf "$tmp"; return 1; }
    css=$(curl -sf https://api.github.com/repos/roflmuffin/CounterStrikeSharp/releases/latest |
          python3 -c 'import json,sys; print(next(a["browser_download_url"] for a in json.load(sys.stdin)["assets"] if a["name"].startswith("counterstrikesharp-with-runtime-linux")))') || { rm -rf "$tmp"; return 1; }
    curl -sfLo "$tmp/mms.tar.gz" "https://mms.alliedmods.net/mmsdrop/2.0/$mms" &&
    curl -sfLo "$tmp/css.zip" "$css" &&
    tar xzf "$tmp/mms.tar.gz" -C "$GAME" &&
    python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$tmp/css.zip" "$GAME" &&
    chown -R 99:100 "$GAME/addons"
    local ok=$?
    rm -rf "$tmp"
    logger -t cs2-waechter "Plugins aktualisiert: $mms, $(basename "$css") (rc=$ok)"
    return $ok
}

# Antwort auf A2S_INFO -> Spielerzahl auf stdout; keine Antwort -> Exit 1.
# Jede Antwort zaehlt, auch die Challenge ("A") - ein haengender Server antwortet gar nicht.
abfragen() {
    python3 - "$PORT" <<'EOF' 2>/dev/null
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(4)
addr = ("127.0.0.1", int(sys.argv[1]))
q = b"\xff\xff\xff\xffTSource Engine Query\x00"
s.sendto(q, addr)
d = s.recv(1400)
if d[4:5] == b"A":
    s.sendto(q + d[5:9], addr)
    try:
        d = s.recv(1400)
    except socket.timeout:
        print(-1); sys.exit(0)
if d[4:5] != b"I":
    print(-1); sys.exit(0)
p = d[6:].split(b"\x00", 4)
print(p[4][2] if len(p) > 4 and len(p[4]) > 2 else -1)
EOF
}

exec 9>"$RUN/cs2-waechter.lock"
flock -n 9 || exit 0

[ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = "true" ] || { rm -f "$SEIT"; exit 0; }
jetzt=$(date +%s)

# --- Absturzschleife erkennen (vor der Schonfrist: in der Schleife ist der Container nie alt genug)
zahl=$(docker inspect -f '{{.RestartCount}}' "$NAME")
if [ -f "$NEUSTARTS" ]; then
    read -r damals zahl_damals < "$NEUSTARTS"
    if [ $(( jetzt - damals )) -le 900 ] && [ $(( zahl - zahl_damals )) -ge "$SCHLEIFE" ] && metamod_an; then
        metamod_aus; date +%s > "$AUS"
        melde "Metamod abgeschaltet" "Der CS2-Server startete $(( zahl - zahl_damals ))x in 15 Minuten neu - vermutlich ist Metamod nach einem CS2-Update inkompatibel. Metamod ist aus, der Server laeuft ohne Training. Neuer Versuch mit aktuellen Builds in $(( RETRY / 3600 )) h." alert
        neustart; echo "$jetzt $(docker inspect -f '{{.RestartCount}}' "$NAME")" > "$NEUSTARTS"; exit 0
    fi
    [ $(( jetzt - damals )) -gt 900 ] && echo "$jetzt $zahl" > "$NEUSTARTS"
else
    echo "$jetzt $zahl" > "$NEUSTARTS"
fi

gestartet=$(date -d "$(docker inspect -f '{{.State.StartedAt}}' "$NAME")" +%s)
[ $(( jetzt - gestartet )) -ge "$ANLAUF" ] || exit 0

spieler=$(abfragen || abfragen)
if [ -n "$spieler" ]; then
    rm -f "$SEIT"
    # --- Metamod wieder eintragen (CS2-Update hat gameinfo.gi ersetzt) oder neu versuchen
    if ! metamod_an; then
        if [ -f "$AUS" ]; then
            [ $(( jetzt - $(cat "$AUS") )) -ge "$RETRY" ] || exit 0
            [ "$spieler" = 0 ] || exit 0
            plugins_aktualisieren || { date +%s > "$AUS"; exit 0; }
            rm -f "$AUS"
            metamod_ein && melde "Metamod neu versucht" "Aktuelle Metamod- und CounterStrikeSharp-Builds installiert, Server startet neu. Stuerzt er wieder ab, wird Metamod erneut abgeschaltet." normal
            neustart; exit 0
        fi
        metamod_ein || { melde "Metamod nicht eintragbar" "gameinfo.gi hat ein unerwartetes Format - Training bleibt aus." alert; exit 0; }
        logger -t cs2-waechter "gameinfo.gi war ohne Metamod (CS2-Update?) - wieder eingetragen"
        touch "$RUN/cs2-waechter.neustart-noetig"
    fi
    if [ -f "$RUN/cs2-waechter.neustart-noetig" ] && [ "$spieler" = 0 ]; then
        rm -f "$RUN/cs2-waechter.neustart-noetig"
        melde "CS2-Server neu gestartet" "gameinfo.gi war nach einem CS2-Update ohne Metamod. Wieder eingetragen und neu gestartet (Server war leer)." normal
        neustart
    fi
    exit 0
fi

# --- Haenger
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
rm -f "$SEIT"
melde "CS2-Server neu gestartet" "Der Server antwortete ${dauer}s nicht (haengender Prozess) und wurde neu gestartet."
neustart
