"""Tests fuer die Steam-Anmeldung per QR-Code.

Steam bietet neben Benutzername und Passwort eine Anmeldung, bei der die
Steam-App einen QR-Code scannt und bestaetigt. Damit muessen Passwort und
Steam-Guard-Code nicht durch die Web-Oberflaeche wandern.
"""

from __future__ import annotations

import base64
import json

import pytest

from cs2_coach import sharecode as SC


def jwt_with(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
    def json(self):
        if self._payload is None:
            raise ValueError("kein JSON")
        return self._payload


class FakeSession:
    """Ersetzt requests.Session; zeichnet Aufrufe auf."""
    def __init__(self, payload):
        self._payload = payload
        self.headers = {}
        self.cookies = None
        self.calls = []
    def update_headers(self, d):
        self.headers.update(d)
    def post(self, url, **kw):
        self.calls.append((url, kw.get("data", {})))
        return FakeResponse(self._payload)
    def get(self, url, **kw):
        return FakeResponse(self._payload)


@pytest.fixture
def fake_requests(monkeypatch):
    holder = {}
    class Mod:
        @staticmethod
        def Session():
            return holder["session"]
    monkeypatch.setitem(__import__("sys").modules, "requests", Mod)
    return holder


# ── SteamID aus dem Token ───────────────────────────────────────────────

def test_steamid_is_read_from_token():
    """Bei der QR-Anmeldung liefert Steam die ID nur im Token."""
    assert SC._steamid_from_token(jwt_with({"sub": "76561198019262528"})) == "76561198019262528"


def test_token_padding_is_handled():
    """Base64 im JWT hat kein Padding - ohne Ergaenzung schlaegt das Dekodieren fehl."""
    for sid in ("1", "12", "123", "76561198019262528"):
        assert SC._steamid_from_token(jwt_with({"sub": sid})) == sid


@pytest.mark.parametrize("bad", ["", "kein-jwt", "nur.zwei", "a.!!!nichtbase64!!!.c"])
def test_broken_token_yields_empty(bad):
    assert SC._steamid_from_token(bad) == ""


# ── Sitzung starten ─────────────────────────────────────────────────────

def test_begin_returns_challenge_url(fake_requests):
    fake_requests["session"] = FakeSession({"response": {
        "client_id": 12345, "request_id": "abc==",
        "interval": 5.0, "challenge_url": "https://s.team/q/1/XYZ",
    }})
    out = SC.steam_login_qr_begin()
    assert out["challenge_url"] == "https://s.team/q/1/XYZ"
    assert out["client_id"] == "12345", "als String, damit nichts gerundet wird"
    assert out["interval"] == 5.0


def test_begin_uses_the_qr_endpoint(fake_requests):
    sess = FakeSession({"response": {
        "client_id": 1, "request_id": "r", "challenge_url": "https://s.team/q/1/A"}})
    fake_requests["session"] = sess
    SC.steam_login_qr_begin()
    assert "BeginAuthSessionViaQR" in sess.calls[0][0]


def test_begin_without_challenge_raises(fake_requests):
    fake_requests["session"] = FakeSession({"response": {
        "extended_error_message": "Steam mag gerade nicht"}})
    with pytest.raises(SC.SteamLoginError, match="Steam mag gerade nicht"):
        SC.steam_login_qr_begin()


def test_begin_with_broken_json_raises(fake_requests):
    fake_requests["session"] = FakeSession(None)
    with pytest.raises(SC.SteamLoginError):
        SC.steam_login_qr_begin()


# ── Abfragen ────────────────────────────────────────────────────────────

def test_poll_pending_before_scan(fake_requests):
    fake_requests["session"] = FakeSession({"response": {"had_remote_interaction": False}})
    out = SC.steam_login_qr_poll("1", "r")
    assert out["status"] == "pending"
    assert out["scanned"] is False


def test_poll_reports_scan_before_confirmation(fake_requests):
    """Zwischen Scannen und Bestaetigen soll die Oberflaeche das anzeigen."""
    fake_requests["session"] = FakeSession({"response": {"had_remote_interaction": True}})
    out = SC.steam_login_qr_poll("1", "r")
    assert out["status"] == "pending" and out["scanned"] is True


def test_poll_passes_refreshed_challenge_url(fake_requests):
    """Steam rotiert den Code; die Oberflaeche muss ihn auffrischen koennen."""
    fake_requests["session"] = FakeSession({"response": {
        "had_remote_interaction": False,
        "new_challenge_url": "https://s.team/q/1/NEU",
        "new_client_id": 999,
    }})
    out = SC.steam_login_qr_poll("1", "r")
    assert out["challenge_url"] == "https://s.team/q/1/NEU"
    assert out["client_id"] == "999"


def test_poll_success_finalizes_and_saves(fake_requests, monkeypatch):
    calls = {}
    monkeypatch.setattr(SC, "_finalize_steam_login",
                        lambda s, sid, rt, at: calls.setdefault("final", sid))
    monkeypatch.setattr(SC, "_save_session", lambda s: calls.setdefault("saved", True))

    fake_requests["session"] = FakeSession({"response": {
        "refresh_token": jwt_with({"sub": "76561198019262528"}),
        "access_token": "at", "account_name": "deej0t",
    }})
    out = SC.steam_login_qr_poll("1", "r")

    assert out["status"] == "ok"
    assert out["steamid"] == "76561198019262528"
    assert out["account_name"] == "deej0t"
    assert calls["final"] == "76561198019262528"
    assert calls["saved"] is True, "Sitzung muss wie beim Passwort-Login gespeichert werden"


def test_poll_with_unreadable_token_raises(fake_requests, monkeypatch):
    monkeypatch.setattr(SC, "_finalize_steam_login", lambda *a: None)
    monkeypatch.setattr(SC, "_save_session", lambda s: None)
    fake_requests["session"] = FakeSession({"response": {"refresh_token": "kaputt"}})
    with pytest.raises(SC.SteamLoginError, match="SteamID"):
        SC.steam_login_qr_poll("1", "r")


def test_expired_session_raises(fake_requests):
    fake_requests["session"] = FakeSession({"response": {}})
    with pytest.raises(SC.SteamLoginError, match="abgelaufen"):
        SC.steam_login_qr_poll("1", "r")
