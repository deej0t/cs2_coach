"""Server-Bestenliste: Log-Zeilen, doppelte Stapel, Elo, Empfang und Zugriff."""

from __future__ import annotations

import importlib
from datetime import datetime

import pytest

from cs2_coach import server_stats as ss

DJ = '"dj<2><[U:1:58996800]><CT>"'
DA = '"Dancho<3><[U:1:923059335]><TERRORIST>"'
BOT = '"Albert<4><BOT><TERRORIST>"'
DJ64 = str(ss.STEAM64_BASE + 58996800)
DA64 = str(ss.STEAM64_BASE + 923059335)


def line(sec: int, msg: str, minute: int = 0) -> str:
    return f"10/29/2026 - 23:{40 + minute:02d}:{sec:02d}.100 - {msg}"


def kill(sec, a, v, weapon="ak47", flags="", minute=0):
    tail = f" ({flags})" if flags else ""
    return line(sec, f'{a} [-1093 -1311 -167] killed {v} [-1555 -1100 -168] with "{weapon}"{tail}', minute)


def hit(sec, a, v, group):
    return line(sec, f'{a} [0 0 0] attacked {v} [1 1 1] with "ak47" (damage "27") (damage_armor "3") '
                     f'(health "73") (armor "96") (hitgroup "{group}")')


def test_steam_ids():
    assert ss.steam64("[U:1:58996800]") == "76561198019262528"
    assert ss.steam64("STEAM_1:0:29498400") == "76561198019262528"
    assert ss.steam64("BOT") is None
    assert ss.steam64("Console") is None


def test_parses_kills_hits_and_bots():
    log = ss.parse_lines([
        "#batch inst1 0 aim_multigame",
        kill(1, DJ, DA, flags="headshot"),
        kill(2, DJ, DA, flags="headshot penetrated"),
        kill(3, DA, DJ, weapon="deagle"),
        kill(4, DJ, BOT),
        kill(5, BOT, DJ),
        line(6, f'{DJ} [0 0 0] committed suicide with "world"'),
        hit(7, DJ, DA, "head"),
        hit(8, DJ, DA, "chest"),
        line(9, f'{DA} assisted killing {DJ}'),
        line(9, f'{DA} flash-assisted killing {BOT}'),  # Bots zaehlen nicht
    ])
    board = ss.build_board(log, now=datetime(2026, 10, 29, 23, 45))
    rows = {r["name"]: r for r in board["rows"]}
    dj, da = rows["dj"], rows["Dancho"]
    assert (dj["kills"], dj["deaths"], dj["bot_kills"]) == (2, 1, 1)
    assert (da["kills"], da["deaths"]) == (1, 2)
    assert dj["hs_kills"] == 1.0 and da["hs_kills"] == 0.0
    assert dj["head_hits"] == 0.5 and dj["hits"] == 2
    assert da["assists"] == 1
    assert dj["elo"] > ss.ELO_START > da["elo"]
    assert dj["elo"] - ss.ELO_START == pytest.approx(ss.ELO_START - da["elo"], abs=1)
    assert board["online"]["active"] and board["online"]["map"] == "aim_multigame"


def test_resent_batch_is_counted_once():
    """CS2 kennzeichnet jeden Stapel mit Instanz + Offset; eine Wiederholung zaehlt nicht doppelt."""
    batch = ["#batch inst1 215 de_mirage", kill(1, DJ, DA)]
    log = ss.parse_lines(batch + batch + ["#batch inst1 300 de_mirage", kill(2, DJ, DA)])
    assert sum(e.kind == "kill" for e in log.events) == 2


def test_elo_rewards_beating_stronger_player():
    """Ein Kill gegen den Staerkeren bringt mehr als einer gegen den Schwaecheren."""
    lines = ["#batch i 0 m"] + [kill(s, DJ, DA) for s in range(20)]
    lines.append(kill(30, DA, DJ))
    board = ss.build_board(ss.parse_lines(lines), now=datetime(2026, 10, 29, 23, 45))
    gain_underdog = ss.ELO_K * (1 - ss._expected(900, 1100))
    gain_favourite = ss.ELO_K * (1 - ss._expected(1100, 900))
    assert gain_underdog > gain_favourite
    assert all(not r["ranked"] for r in board["rows"])  # 21 Duelle < MIN_DUELS


def test_games_streaks_and_records():
    lines = ["#batch i 0 aim_map", line(0, 'Loading map "aim_map"'), line(0, 'Started map "aim_map"')]
    lines += [kill(s, DJ, DA, flags="headshot" if s % 2 else "") for s in range(1, 13)]
    lines += [kill(20, DA, DJ), line(21, "Game Over: competitive mg_active aim_map score 12:1 after 3 min"),
              line(30, 'Loading map "de_dust2"'), kill(31, DA, DJ), kill(32, DA, DJ)]
    board = ss.build_board(ss.parse_lines(lines), now=datetime(2026, 10, 29, 23, 45))
    assert len(board["games"]) == 1  # zweites Spiel hat zu wenige Kills
    g = board["games"][0]
    assert (g["map"], g["score"], g["mode"], g["finished"]) == ("aim_map", "12:1", "competitive", True)
    rec = {r["title"]: r for r in board["records"]}
    assert rec["Längste Killserie"]["value"] == "12 ohne Tod"
    assert rec["Meiste Kills in einem Spiel"]["name"] == "dj"
    assert board["rows"][0]["best_streak"] == 12


def test_streak_ends_on_death_to_bot():
    lines = ["#batch i 0 m", kill(1, DJ, DA), kill(2, DJ, DA), kill(3, BOT, DJ), kill(4, DJ, DA)]
    board = ss.build_board(ss.parse_lines(lines), now=datetime(2026, 10, 29, 23, 45))
    assert next(r for r in board["rows"] if r["name"] == "dj")["best_streak"] == 2


def test_online_respects_disconnect_and_instance():
    lines = ["#batch old 0 m", line(0, f'{DA} entered the game'),
             "#batch new 0 de_inferno", line(1, f'{DJ} entered the game'),
             line(2, f'"Gast<5><[U:1:5]><>" entered the game'),
             line(3, '"Gast<5><[U:1:5]><CT>" disconnected (reason "Disconnect")')]
    on = ss.online(ss.parse_lines(lines), datetime(2026, 10, 29, 23, 45))
    assert on == {"active": True, "players": ["dj"], "map": "de_inferno"}
    assert not ss.online(ss.parse_lines(lines), datetime(2026, 10, 30, 12, 0))["active"]


def test_period_filters_old_events():
    lines = ["#batch i 0 m", "10/01/2026 - 20:00:00.000 - " + kill(0, DJ, DA).split(" - ", 2)[2],
             kill(1, DA, DJ)]
    log = ss.parse_lines(lines)
    now = datetime(2026, 10, 29, 23, 45)
    assert {r["name"]: r["kills"] for r in ss.build_board(log, "alle", now)["rows"]} == {"dj": 1, "Dancho": 1}
    assert {r["name"]: r["kills"] for r in ss.build_board(log, "7", now)["rows"]} == {"dj": 0, "Dancho": 1}


def test_store_and_read_roundtrip(tmp_path):
    cfg = tmp_path / "config.yaml"
    headers = {"X-Server-Instance-Token": "abc", "X-LogBytes-BeginOffset": "0",
               "X-Game-Map": "aim_multigame", "X-Timestamp": "10/29/2026 - 23:38:45.796"}
    body = (kill(1, DJ, DA) + "\n" + kill(2, DJ, DA) + "\n").encode()
    assert ss.store_batch(cfg, headers, body) == 2
    ss.store_batch(cfg, headers, body)  # Wiederholung desselben Stapels
    assert (tmp_path / "server_logs" / "2026-10-29.log").exists()
    log = ss.read_logs(cfg)
    assert sum(e.kind == "kill" for e in log.events) == 2


@pytest.fixture()
def app_with_auth(tmp_path, monkeypatch):
    """App mit Passwort und leerer Config; Modulzustand danach wiederherstellen."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("language: de\n", encoding="utf-8")
    monkeypatch.setenv("CS2COACH_CONFIG", str(cfg))
    monkeypatch.setenv("CS2COACH_PASSWORD", "geheim")
    import cs2_coach.web.app as app_module
    try:
        mod = importlib.reload(app_module)
        app = mod.create_app()
        app.config["TESTING"] = True
        yield app.test_client(), cfg
    finally:
        monkeypatch.delenv("CS2COACH_CONFIG")
        monkeypatch.delenv("CS2COACH_PASSWORD")
        importlib.reload(app_module)


def test_page_is_public_and_hides_setup(app_with_auth):
    client, _ = app_with_auth
    resp = client.get("/server", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "Server-Bestenliste" in resp.get_data(as_text=True)
    assert "logaddress_add_http" not in resp.get_data(as_text=True)


def test_ingest_requires_token(app_with_auth):
    client, cfg = app_with_auth
    token = ss.load_or_create_token(cfg)
    body = kill(1, DJ, DA).encode()
    assert client.post("/api/serverlog/falsch", data=body).status_code == 403
    resp = client.post(f"/api/serverlog/{token}", data=body,
                       headers={"X-Server-Instance-Token": "t", "X-LogBytes-BeginOffset": "0"})
    assert resp.status_code == 200
    page = client.get("/server").get_data(as_text=True)
    assert "dj" in page and "Dancho" in page


# Echte Zeilen vom Server (29.10.2026, 1.41.9.3), Gegner anonymisiert. Belegt:
# Treffer melden Rohschaden ("109" bei 100 HP), Kill-Zeilen nennen Waffen-
# und Messer-Skins anders als Treffer-Zeilen (hkp2000/usp_silencer).
REAL = """#batch 0d0662c2551c27b1 3436 aim_multigame
10/29/2026 - 23:46:04.702 - "dj<2><[U:1:58996800]><CT>" [2780 -3191 224] attacked "Gegner<3><[U:1:5]><TERRORIST>" [3248 -3168 224] with "ak47" (damage "109") (damage_armor "15") (health "0") (armor "85") (hitgroup "head")
10/29/2026 - 23:46:04.702 - "dj<2><[U:1:58996800]><CT>" [2780 -3191 224] killed "Gegner<3><[U:1:5]><TERRORIST>" [3248 -3168 224] with "ak47" (headshot)
10/29/2026 - 23:46:17.795 - "dj<2><[U:1:58996800]><CT>" [14937 569 128] attacked "Gegner<3><[U:1:5]><TERRORIST>" [14944 521 128] with "knife" (damage "153") (damage_armor "13") (health "0") (armor "87") (hitgroup "generic")
10/29/2026 - 23:46:17.795 - "dj<2><[U:1:58996800]><CT>" [14937 569 128] killed "Gegner<3><[U:1:5]><TERRORIST>" [14944 521 128] with "knife_skeleton"
10/29/2026 - 23:46:27.905 - "dj<2><[U:1:58996800]><CT>" [1096 4383 160] attacked "Gegner<3><[U:1:5]><TERRORIST>" [944 4400 160] with "hkp2000" (damage "68") (damage_armor "33") (health "32") (armor "67") (hitgroup "head")
10/29/2026 - 23:46:29.529 - "dj<2><[U:1:58996800]><CT>" [1093 4380 160] attacked "Gegner<3><[U:1:5]><TERRORIST>" [944 4400 160] with "hkp2000" (damage "68") (damage_armor "33") (health "0") (armor "34") (hitgroup "head")
10/29/2026 - 23:46:29.529 - "dj<2><[U:1:58996800]><CT>" [1093 4380 160] killed "Gegner<3><[U:1:5]><TERRORIST>" [944 4400 160] with "usp_silencer" (headshot)
"""


def test_real_server_lines():
    board = ss.build_board(ss.parse_lines(REAL.splitlines()), now=datetime(2026, 10, 29, 23, 50))
    rows = {r["name"]: r for r in board["rows"]}
    dj = rows["dj"]
    assert (dj["kills"], dj["deaths"], dj["hits"]) == (3, 0, 4)
    assert dj["hs_kills"] == pytest.approx(2 / 3)
    assert dj["head_hits"] == 0.75
    assert rows["Gegner"]["deaths"] == 3
    assert sorted(e.weapon for e in ss.parse_lines(REAL.splitlines()).events if e.kind == "kill") == [
        "ak47", "knife", "usp_silencer"]
    assert board["online"] == {"active": True, "players": ["Gegner", "dj"], "map": "aim_multigame"}


def test_reset_archives_instead_of_deleting(tmp_path):
    cfg = tmp_path / "config.yaml"
    ss.store_batch(cfg, {"X-Timestamp": "10/29/2026 - 23:38:45.796"}, kill(1, DJ, DA).encode())
    assert ss.read_logs(cfg).events
    assert ss.archive_logs(cfg, now=datetime(2026, 10, 30, 0, 5)) == 1
    assert not ss.read_logs(cfg).events
    assert (tmp_path / "server_logs" / "archiv" / "2026-10-30_000500" / "2026-10-29.log").exists()
    assert ss.archive_logs(cfg) == 0


def test_reset_needs_login(app_with_auth):
    client, cfg = app_with_auth
    ss.store_batch(cfg, {}, kill(1, DJ, DA).encode())
    assert client.post("/server/reset").status_code in (302, 403)
    assert ss.read_logs(cfg).events  # nichts archiviert
    with client.session_transaction() as sess:
        sess["authenticated"] = True
    assert client.post("/server/reset").status_code == 302
    assert not ss.read_logs(cfg).events
