"""Tests fuer den Systemstatus.

Geprueft wird vor allem, dass die drei Fehler der letzten Wochen erkannt
wuerden:

- Update-Schleife lief nie (letzte Pruefung 293 Stunden alt),
- WebUI nicht erreichbar, waehrend Docker "healthy" meldete,
- Cache voll (zweimal ueber 90 %).

Und dass Discord nicht bei jedem Lauf dieselbe Warnung bekommt.
"""

from __future__ import annotations

import json
import os
import time
from collections import namedtuple
from pathlib import Path

from cs2_coach import health as H

Usage = namedtuple("Usage", "total used free")


class Resp:
    def __init__(self, status):
        self.status_code = status


# ── WebUI von aussen ─────────────────────────────────────────────────

def test_weiterleitung_auf_login_gilt_als_erreichbar():
    c = H.check_public_url({"public_url": "https://x"}, get=lambda *a, **k: Resp(302))
    assert c.state == H.OK


def test_verbindung_abgelehnt_ist_ein_fehler():
    """So sah der Ausfall vom 14.10. von aussen aus."""
    def refused(*a, **k):
        raise ConnectionError("connection refused")
    c = H.check_public_url({"public_url": "https://x"}, get=refused)
    assert c.state == H.FAIL
    assert "connection refused" in c.message


def test_tunnelfehler_von_cloudflare_ist_ein_fehler():
    c = H.check_public_url({"public_url": "https://x"}, get=lambda *a, **k: Resp(502))
    assert c.state == H.FAIL


def test_ohne_adresse_wird_nicht_geprueft():
    assert H.check_public_url({}).state == H.SKIP


# ── Auto-Update ──────────────────────────────────────────────────────

def _repo(tmp_path, age_h):
    git = tmp_path / ".git"
    git.mkdir()
    fh = git / "FETCH_HEAD"
    fh.write_text("x")
    t = time.time() - age_h * 3600
    os.utime(fh, (t, t))
    return tmp_path


def test_alte_update_pruefung_ist_ein_fehler(tmp_path, monkeypatch):
    """293 Stunden: der Zustand, in dem der Server sechs Wochen lang war."""
    monkeypatch.delenv("CS2COACH_UPDATE_INTERVAL", raising=False)
    c = H.check_updates(_repo(tmp_path, 293), in_container=True)
    assert c.state == H.FAIL
    assert "293 h" in c.message


def test_haengendes_update_wird_erkannt(tmp_path, monkeypatch):
    monkeypatch.delenv("CS2COACH_UPDATE_INTERVAL", raising=False)
    repo = _repo(tmp_path, 1)
    shas = {"HEAD": "aaaaaaa", "origin/main": "bbbbbbb"}
    monkeypatch.setattr(H, "_git", lambda r, *a: shas[a[-1]])
    c = H.check_updates(repo, in_container=True)
    assert c.state == H.FAIL
    assert "aaaaaaa" in c.message and "bbbbbbb" in c.message


def test_frische_pruefung_auf_aktuellem_stand(tmp_path, monkeypatch):
    monkeypatch.delenv("CS2COACH_UPDATE_INTERVAL", raising=False)
    repo = _repo(tmp_path, 1)
    monkeypatch.setattr(H, "_git", lambda r, *a: "aaaaaaa")
    assert H.check_updates(repo, in_container=True).state == H.OK


def test_ausserhalb_des_containers_nicht_geprueft(tmp_path):
    assert H.check_updates(tmp_path, in_container=False).state == H.SKIP


def test_abgeschaltetes_update_nicht_geprueft(tmp_path, monkeypatch):
    monkeypatch.setenv("CS2COACH_UPDATE_INTERVAL", "0")
    assert H.check_updates(tmp_path, in_container=True).state == H.SKIP


# ── Steam ────────────────────────────────────────────────────────────

def test_steam_zustaende():
    assert H.check_steam({}).state == H.WARN
    assert H.check_steam({"refresh_expires": 1, "logged_in": False}).state == H.FAIL
    assert H.check_steam({"refresh_expires": 1, "logged_in": True, "days_left": 5}).state == H.WARN
    assert H.check_steam({"refresh_expires": 1, "logged_in": True, "days_left": 194}).state == H.OK


# ── Speicher ─────────────────────────────────────────────────────────

def test_voller_cache_wird_gemeldet(tmp_path):
    """92 % - so voll war der Cache am 08.10."""
    c = H.check_disk({"Cache": tmp_path}, usage=lambda p: Usage(466, 429, 37))[0]
    assert c.state == H.WARN
    c = H.check_disk({"Cache": tmp_path}, usage=lambda p: Usage(100, 96, 4))[0]
    assert c.state == H.FAIL


def test_cache_und_array_trotz_gleicher_geraetenummer_getrennt(tmp_path):
    """Unraid: /data und /data/demos haben beide st_dev 47 (shfs)."""
    a, b = tmp_path / "cache", tmp_path / "array"
    a.mkdir(), b.mkdir()
    sizes = {str(a): Usage(466, 274, 192), str(b): Usage(51000, 32000, 19000)}
    checks = H.check_disk({"Cache": a, "Array": b}, usage=lambda p: sizes[str(p)])
    assert [c.label for c in checks] == ["Speicher Cache", "Speicher Array"]


def test_gleicher_datentraeger_nur_einmal(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    checks = H.check_disk({"A": a, "B": b}, usage=lambda p: Usage(100, 10, 90))
    assert len(checks) == 1


# ── Demos ────────────────────────────────────────────────────────────

def _setup_demos(tmp_path, exported, present):
    vault = tmp_path / "vault"
    exports = vault / "CS2-Coach" / "exports"
    exports.mkdir(parents=True)
    demos = tmp_path / "demos"
    demos.mkdir()
    for i, d in enumerate(exported):
        (exports / f"2026-10-0{i + 1}_mirage_13-5_2000_coach.json").write_text(
            json.dumps({"match": {"demo_file": d}}), encoding="utf-8")
    for d in present:
        (demos / d).write_bytes(b"")
    return {"obsidian_vault_path": str(vault), "demo_folder": str(demos)}


def test_leerer_demo_ordner_ist_ein_fehler(tmp_path):
    """Der lautlose Fall nach einem falsch eingebundenen Demo-Ordner."""
    cfg = _setup_demos(tmp_path, ["a.dem", "b.dem"], [])
    assert H.check_demos(cfg).state == H.FAIL


def test_einzelne_fehlende_demos_sind_eine_warnung(tmp_path):
    cfg = _setup_demos(tmp_path, ["a.dem", "b.dem"], ["a.dem"])
    c = H.check_demos(cfg)
    assert c.state == H.WARN and "1 von 2" in c.message


def test_alle_demos_da(tmp_path):
    cfg = _setup_demos(tmp_path, ["a.dem"], ["a.dem"])
    assert H.check_demos(cfg).state == H.OK


# ── Benachrichtigung ─────────────────────────────────────────────────

def _checks(**states):
    return [H.Check(k, k, s, f"{k} ist {s}") for k, s in states.items()]


def test_neues_problem_wird_gemeldet(tmp_path):
    sent = []
    state = tmp_path / "state.json"
    text = H.notify(_checks(web=H.FAIL, disk=H.OK), "https://hook", state,
                    post=lambda url, **k: sent.append(k["json"]["content"]) or Resp(204))
    assert text and "web" in sent[0] and "(neu)" in sent[0]
    assert json.loads(state.read_text())["web"] == H.FAIL


def test_unveraendertes_problem_nicht_erneut(tmp_path):
    """Sonst kaeme alle 30 Minuten dieselbe Nachricht."""
    state = tmp_path / "state.json"
    post = lambda url, **k: Resp(204)
    H.notify(_checks(web=H.FAIL), "https://hook", state, post=post)
    assert H.notify(_checks(web=H.FAIL), "https://hook", state, post=post) is None


def test_behebung_wird_gemeldet(tmp_path):
    state = tmp_path / "state.json"
    post = lambda url, **k: Resp(204)
    H.notify(_checks(web=H.FAIL), "https://hook", state, post=post)
    text = H.notify(_checks(web=H.OK), "https://hook", state, post=post)
    assert text and "wieder in Ordnung" in text


def test_ok_und_skip_sind_keine_nachricht(tmp_path):
    state = tmp_path / "state.json"
    assert H.notify(_checks(web=H.OK, x=H.SKIP), "https://hook", state,
                    post=lambda *a, **k: Resp(204)) is None


def test_fehlgeschlagener_versand_wird_wiederholt(tmp_path):
    """Zustand erst nach erfolgreichem Versand speichern."""
    state = tmp_path / "state.json"
    assert H.notify(_checks(web=H.FAIL), "https://hook", state,
                    post=lambda *a, **k: Resp(500)) is None
    assert not state.exists()
    assert H.notify(_checks(web=H.FAIL), "https://hook", state,
                    post=lambda *a, **k: Resp(204)) is not None


def test_ohne_webhook_wird_nichts_gespeichert(tmp_path):
    """Wird der Webhook spaeter eingetragen, kommt das Problem noch an."""
    state = tmp_path / "state.json"
    assert H.notify(_checks(web=H.FAIL), "", state) is None
    assert not state.exists()


def test_schreibzugriff_ok(tmp_path):
    assert H.check_writable(tmp_path).state == H.OK
    assert list(tmp_path.iterdir()) == []


def test_schreibzugriff_voll_ist_fehler(tmp_path, monkeypatch):
    # So sah es am 2026-10-16 aus: 32 GB frei, appdata verlangte 48,8 GB.
    import errno

    def full(self, *a, **k):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(Path, "write_text", full)
    c = H.check_writable(tmp_path)
    assert c.state == H.FAIL and "No space left" in c.message


# ── USV ──────────────────────────────────────────────────────────────

def _fake_nut(reply: bytes):
    """Kleiner NUT-Dienst auf einem freien Port, der eine feste Antwort gibt."""
    import socket
    import threading

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        conn.recv(1024)
        conn.sendall(reply)
        conn.close()
        srv.close()
    threading.Thread(target=serve, daemon=True).start()
    return srv.getsockname()[1]


def test_usv_am_netz():
    port = _fake_nut(b'BEGIN LIST VAR ups\nVAR ups battery.charge "100"\nVAR ups battery.runtime "231"\n'
                     b'VAR ups ups.load "42"\nVAR ups ups.status "OL"\nEND LIST VAR ups\n')
    c = H.check_ups({"ups_host": f"127.0.0.1:{port}"})
    assert c.state == H.OK and "Akku 100 %" in c.message and "Laufzeit 3 Min" in c.message


def test_usv_ohne_daten_ist_fehler():
    # So sah es vom 14. bis 16.10. aus: Dienst laeuft, USB-Verbindung weg.
    port = _fake_nut(b"ERR DATA-STALE\n")
    c = H.check_ups({"ups_host": f"127.0.0.1:{port}"})
    assert c.state == H.FAIL and "DATA-STALE" in c.message


def test_usv_auf_akku_ist_fehler():
    c = H.check_ups({"ups_host": "x"}, read=lambda h, p, u: {"ups.status": "OB DISCHRG", "battery.charge": "80"})
    assert c.state == H.FAIL and "Akku" in c.message


def test_usv_nicht_eingetragen_wird_uebersprungen():
    assert H.check_ups({}).state == H.SKIP


def test_usv_dienst_nicht_erreichbar_ist_warnung():
    def refuse(h, p, u):
        raise ConnectionRefusedError("refused")
    assert H.check_ups({"ups_host": "x"}, read=refuse).state == H.WARN
