"""Tests fuer die Haltbarkeit der Steam-Anmeldung.

Zwei Fehler machten eine dauerhafte Anmeldung unmoeglich:

1. Die Sitzung lag neben dem Quellcode, im Container also unter /app - in
   der Schreibschicht, die Docker bei jedem Neuerstellen verwirft.
2. Lief der Zugriffstoken ab (genau 24 Stunden), loeschte der Code die
   gesamte Sitzung - mitsamt dem Refresh-Token, der rund 210 Tage gilt.

Gemessen am 26.09.2026 an der echten Sitzung:
    steamLoginSecure    aud=['web']                      1.0 Tage
    steamRefresh_steam  aud=['web','renew','derive']   210.9 Tage
"""

from __future__ import annotations

import base64
import json
import pickle
import time

import pytest

from cs2_coach import sharecode


def make_jwt(exp: int) -> str:
    """Minimales JWT, von dem nur das Feld exp gelesen wird."""
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": exp, "aud": ["web"]}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


class FakeCookie:
    def __init__(self, name, value):
        self.name, self.value = name, value


def jar(access_in_h=24, refresh_in_d=210, sessionid="abc123"):
    now = int(time.time())
    out = [FakeCookie("sessionid", sessionid)]
    if access_in_h is not None:
        out.append(FakeCookie(
            "steamLoginSecure",
            f"7656%7C%7C{make_jwt(now + int(access_in_h * 3600))}"))
    if refresh_in_d is not None:
        out.append(FakeCookie(
            "steamRefresh_steam",
            f"7656||{make_jwt(now + refresh_in_d * 86400)}"))
    return out


# ── Ablageort ────────────────────────────────────────────────────────

def test_sitzung_liegt_neben_der_config(monkeypatch, tmp_path):
    """Im Container /data/.steam_session statt /app/.steam_session."""
    monkeypatch.setenv("CS2COACH_CONFIG", str(tmp_path / "config.yaml"))
    assert sharecode._session_file() == tmp_path / ".steam_session"


def test_ohne_env_bleibt_der_alte_ort(monkeypatch):
    monkeypatch.delenv("CS2COACH_CONFIG", raising=False)
    assert sharecode._session_file() == sharecode._LEGACY_SESSION_FILE


def test_bestehende_anmeldung_wird_uebernommen(monkeypatch, tmp_path):
    """Ein Update darf niemanden abmelden."""
    legacy = tmp_path / "legacy" / ".steam_session"
    legacy.parent.mkdir()
    legacy.write_bytes(b"cookie-jar")
    monkeypatch.setattr(sharecode, "_LEGACY_SESSION_FILE", legacy)
    monkeypatch.setenv("CS2COACH_CONFIG", str(tmp_path / "config.yaml"))

    sharecode._migrate_legacy_session()

    assert (tmp_path / ".steam_session").read_bytes() == b"cookie-jar"
    assert not legacy.exists()


def test_migration_ueberschreibt_nichts(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy" / ".steam_session"
    legacy.parent.mkdir()
    legacy.write_bytes(b"alt")
    (tmp_path / ".steam_session").write_bytes(b"neu")
    monkeypatch.setattr(sharecode, "_LEGACY_SESSION_FILE", legacy)
    monkeypatch.setenv("CS2COACH_CONFIG", str(tmp_path / "config.yaml"))

    sharecode._migrate_legacy_session()

    assert (tmp_path / ".steam_session").read_bytes() == b"neu"


# ── Token-Laufzeiten ─────────────────────────────────────────────────

def test_ablauf_wird_aus_dem_cookie_gelesen():
    now = int(time.time())
    cookies = jar(access_in_h=24, refresh_in_d=210)
    assert sharecode._token_expiry(cookies) == pytest.approx(now + 86400, abs=5)
    assert sharecode._token_expiry(cookies, "steamRefresh_steam") == \
        pytest.approx(now + 210 * 86400, abs=5)


def test_fehlendes_oder_kaputtes_cookie_ergibt_null():
    assert sharecode._token_expiry([]) == 0
    assert sharecode._token_expiry([FakeCookie("steamLoginSecure", "kaputt")]) == 0


# ── Status fuer die Oberflaeche ──────────────────────────────────────

def test_status_meldet_die_verbleibenden_tage(monkeypatch, tmp_path):
    monkeypatch.setenv("CS2COACH_CONFIG", str(tmp_path / "config.yaml"))
    (tmp_path / ".steam_session").write_bytes(pickle.dumps(jar()))

    st = sharecode.steam_session_status()

    assert st["logged_in"] is True
    assert st["days_left"] == pytest.approx(210, abs=1)
    assert st["access_hours_left"] == pytest.approx(24, abs=1)


def test_status_ohne_datei(monkeypatch, tmp_path):
    monkeypatch.setenv("CS2COACH_CONFIG", str(tmp_path / "config.yaml"))
    assert sharecode.steam_session_status()["logged_in"] is False


def test_abgelaufener_refresh_token_gilt_als_abgemeldet(monkeypatch, tmp_path):
    monkeypatch.setenv("CS2COACH_CONFIG", str(tmp_path / "config.yaml"))
    (tmp_path / ".steam_session").write_bytes(
        pickle.dumps(jar(access_in_h=-1, refresh_in_d=-1)))
    assert sharecode.steam_session_status()["logged_in"] is False


# ── Erneuerung ───────────────────────────────────────────────────────

class FakeSession:
    def __init__(self, cookies, replies):
        self.cookies = cookies
        self._replies = list(replies)
        self.calls = []

    def post(self, url, **kw):
        self.calls.append(url)
        return self._replies.pop(0)


class FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_erneuerung_ohne_sessionid_scheitert_sofort():
    s = FakeSession([FakeCookie("steamLoginSecure", "x")], [])
    assert sharecode.refresh_steam_session(s) is False
    assert s.calls == []


def test_erneuerung_meldet_nur_bei_echtem_fortschritt(monkeypatch):
    """result:1 allein genuegt nicht - der Token muss neuer sein."""
    cookies = jar(access_in_h=24)
    s = FakeSession(cookies, [
        FakeResp(200, {"success": True, "login_url": "https://x/settoken",
                       "nonce": "n", "auth": "a", "steamID": "7656"}),
        FakeResp(200, {"result": 1}),
    ])
    # Der Jar wird nicht veraendert, der Ablauf bleibt also gleich.
    assert sharecode.refresh_steam_session(s) is False


def test_erneuerung_erkennt_neuen_token(monkeypatch):
    cookies = jar(access_in_h=1)
    s = FakeSession(cookies, [
        FakeResp(200, {"success": True, "login_url": "https://x/settoken",
                       "nonce": "n", "auth": "a", "steamID": "7656"}),
        FakeResp(200, {"result": 1}),
    ])

    def bump(url, **kw):
        s.calls.append(url)
        r = s._replies.pop(0)
        if "settoken" in url:          # Steam setzt den frischen Cookie
            cookies[1] = FakeCookie(
                "steamLoginSecure",
                f"7656%7C%7C{make_jwt(int(time.time()) + 86400)}")
        return r

    s.post = bump
    assert sharecode.refresh_steam_session(s) is True


def test_abgelehnte_erneuerung_bleibt_false():
    s = FakeSession(jar(), [FakeResp(200, {"success": False})])
    assert sharecode.refresh_steam_session(s) is False


def test_netzfehler_loescht_die_anmeldung_nicht(monkeypatch, tmp_path):
    """Ein Ausfall darf keine Abmeldung ausloesen."""
    monkeypatch.setenv("CS2COACH_CONFIG", str(tmp_path / "config.yaml"))
    path = tmp_path / ".steam_session"
    path.write_bytes(pickle.dumps(jar()))

    class Boom:
        def get(self, *a, **kw):
            raise OSError("Netz weg")
        headers = {}
        cookies = None

    monkeypatch.setattr(sharecode, "_requests", None, raising=False)
    import requests
    monkeypatch.setattr(requests, "Session", lambda: Boom())

    assert sharecode.load_steam_session() is None
    assert path.exists(), "Sitzung wurde bei einem Netzfehler geloescht"
