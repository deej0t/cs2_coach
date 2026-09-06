"""Tests fuer die GCPD-Suche nach Demo-Downloads.

Zwei Fehler sorgten dafuer, dass Wettkampf-Demos nie gefunden wurden:

1. Abgefragt wurde nur "matchhistorycompetitive". Das ist laut Steams
   eigener Beschriftung die alte CS:GO-Historie ("Wettkampfspiele") und
   bleibt bei CS2-Spielern leer. Der aktuelle Modus liegt unter
   "matchhistorycompetitivepermap" ("Gewertete Wettkampfspiele").

2. Das continue_token ist ein globaler Zeitcursor, kein Zaehler je Tab:
   alle Modi starten mit demselben Wert. Eine leere Seite heisst deshalb
   nur "in diesem Zeitfenster kein Match dieses Modus". Die Schleife
   brach dort ab und erreichte spaetere Seiten nie - bei Wingman lagen
   die Matches nachweislich erst auf Seite 3.
"""

from __future__ import annotations

import json

import pytest

from cs2_coach import sharecode as SC

DEMO_URL = "http://replay273.valve.net/730/003840331293887823900_1407154118.dem.bz2"


def test_cs2_competitive_tab_is_queried():
    """Ohne diesen Tab bleiben Wettkampf-Demos unsichtbar."""
    captured = {}

    class Sess:
        def get(self, url, **kw):
            captured.setdefault("urls", []).append(url)
            raise RuntimeError("stop")

    SC.fetch_gcpd_demo_urls(Sess(), "76561198019262528", on_status=lambda m: None)
    tabs = [u.split("tab=")[1].split("&")[0] for u in captured["urls"] if "tab=" in u]
    assert "matchhistorycompetitivepermap" in tabs
    assert "matchhistorypremier" in tabs
    assert "matchhistorywingman" in tabs


class PagedSession:
    """Simuliert den GCPD: erst leere Seiten, dann eine mit Demo."""

    def __init__(self, demo_on_page: int):
        self.demo_on_page = demo_on_page
        self.page = 0
        self.ajax_calls = 0

    def get(self, url, **kw):
        if "ajax=1" not in url:
            return self._page_response(
                "var g_sGcContinueToken = '2000'; var g_sessionID = 'abc';")
        self.ajax_calls += 1
        self.page += 1
        html = (f'<a href="{DEMO_URL}">dl</a>'
                if self.page == self.demo_on_page else "<tr><td>leer</td></tr>")
        body = json.dumps({"success": True, "html": html,
                           "continue_token": str(1000 - self.page)})
        return self._page_response(body, is_json=True)

    def _page_response(self, text, is_json=False):
        outer = self

        class R:
            status_code = 200
            url = "https://steamcommunity.com/id/x/gcpd/730"
            def __init__(self):
                self.text = text
            def json(self):
                if not is_json:
                    raise ValueError("kein JSON")
                return json.loads(text)
        return R()


def test_empty_first_pages_do_not_stop_the_search(monkeypatch):
    """Kern des Fehlers: die Demo lag hinter zwei leeren Seiten."""
    monkeypatch.setattr(SC.time, "sleep", lambda *_: None)
    sess = PagedSession(demo_on_page=3)

    demos = SC.fetch_gcpd_demo_urls(sess, "1", tabs=["matchhistorycompetitivepermap"],
                                    on_status=lambda m: None)

    assert len(demos) == 1, "Demo hinter leeren Seiten muss gefunden werden"
    assert demos[0]["url"] == DEMO_URL


def test_search_gives_up_after_enough_empty_pages(monkeypatch):
    """Nicht endlos blaettern - Valve haelt Demos nur rund zwei Wochen vor."""
    monkeypatch.setattr(SC.time, "sleep", lambda *_: None)
    sess = PagedSession(demo_on_page=999)   # nie eine Demo

    SC.fetch_gcpd_demo_urls(sess, "1", tabs=["matchhistorypremier"],
                            on_status=lambda m: None)

    assert sess.ajax_calls <= SC._GCPD_MAX_PAGES
    assert sess.ajax_calls >= SC._GCPD_MAX_EMPTY_PAGES


def test_page_limit_is_respected(monkeypatch):
    """Auch wenn staendig Treffer kommen, wird nicht unbegrenzt geblaettert."""
    monkeypatch.setattr(SC.time, "sleep", lambda *_: None)

    class Always(PagedSession):
        def get(self, url, **kw):
            if "ajax=1" in url:
                self.ajax_calls += 1
                self.page += 1
                body = json.dumps({
                    "success": True,
                    "html": f'<a href="http://replay1.valve.net/730/00384033129388782390{self.page}_1407154118.dem.bz2">dl</a>',
                    "continue_token": str(1000 - self.page)})
                return self._page_response(body, is_json=True)
            return self._page_response(
                "var g_sGcContinueToken = '2000'; var g_sessionID = 'abc';")

    sess = Always(demo_on_page=0)
    SC.fetch_gcpd_demo_urls(sess, "1", tabs=["matchhistorypremier"],
                            on_status=lambda m: None)
    assert sess.ajax_calls == SC._GCPD_MAX_PAGES


def test_stalled_cursor_ends_the_tab(monkeypatch):
    """Bewegt sich der Cursor nicht mehr, ist der Modus durchsucht."""
    monkeypatch.setattr(SC.time, "sleep", lambda *_: None)

    class Stalled(PagedSession):
        def get(self, url, **kw):
            if "ajax=1" in url:
                self.ajax_calls += 1
                body = json.dumps({"success": True, "html": "<tr></tr>",
                                   "continue_token": "2000"})   # wie die Seite
                return self._page_response(body, is_json=True)
            return self._page_response(
                "var g_sGcContinueToken = '2000'; var g_sessionID = 'abc';")

    sess = Stalled(demo_on_page=0)
    SC.fetch_gcpd_demo_urls(sess, "1", tabs=["matchhistorypremier"],
                            on_status=lambda m: None)
    assert sess.ajax_calls == 1, "unveraenderter Token = sofort Schluss"
