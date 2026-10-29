"""Minimaler Source-RCON-Client (TCP) fuer den eigenen CS2-Server.

Gegen den Server 1.41.9.3 erprobt (29./30.10.2026): nach dem Login (Typ 3)
kommt ein leeres Paket vom Typ 0 und dann die Bestaetigung vom Typ 2 mit
der Anfrage-ID (-1 bei falschem Passwort). Jede Antwort auf einen Befehl
traegt die ID des Befehls.
"""

from __future__ import annotations

import socket
import struct

AUTH, AUTH_RESPONSE, EXEC = 3, 2, 2


class RconError(Exception):
    pass


def _packet(req_id: int, kind: int, body: str) -> bytes:
    data = body.encode("utf-8") + b"\x00\x00"
    return struct.pack("<iii", len(data) + 8, req_id, kind) + data


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise RconError("Verbindung vom Server geschlossen")
        buf += chunk
    return buf


def _read(sock: socket.socket) -> tuple[int, int, str]:
    size = struct.unpack("<i", _recv_exact(sock, 4))[0]
    data = _recv_exact(sock, size)
    req_id, kind = struct.unpack("<ii", data[:8])
    return req_id, kind, data[8:-2].decode("utf-8", errors="replace")


def command(host: str, port: int, password: str, *commands: str, timeout: float = 3.0) -> list[str]:
    """Befehle nacheinander ausfuehren, Antworten in derselben Reihenfolge zurueckgeben."""
    with socket.create_connection((host, int(port)), timeout=timeout) as sock:
        sock.sendall(_packet(1, AUTH, password))
        while True:
            req_id, kind, _ = _read(sock)
            if kind == AUTH_RESPONSE:
                if req_id != 1:
                    raise RconError("RCON-Passwort falsch")
                break
        answers = []
        for n, cmd in enumerate(commands, start=10):
            sock.sendall(_packet(n, EXEC, cmd))
            while True:
                req_id, _, body = _read(sock)
                if req_id == n:
                    answers.append(body)
                    break
        return answers
