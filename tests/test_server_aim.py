"""Aim-Werte aller Spieler aus den Server-Demos (server_aim.py)."""

from types import SimpleNamespace

from cs2_coach import server_aim

HUMAN_LINE = '11/12/2026 - 20:00:00.000 - "dj<2><[U:1:58996800]><CT>" killed "Rex<3><BOT><T>" with "ak47"'
LEAVE_LINE = '11/12/2026 - 20:30:00.000 - "dj<2><[U:1:58996800]><CT>" disconnected (reason "quit")'


def _cfg(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("", encoding="utf-8")
    return path


def test_record_only_with_humans_and_at_most_once_a_minute(tmp_path):
    path, sent = _cfg(tmp_path), []
    send = lambda *c: sent.extend(c) or ["Recording to coach_x.dem"]  # noqa: E731
    assert server_aim.maybe_record(path, ['"Rex<3><BOT><T>" say "hi"'], send, now=1000) is None
    assert server_aim.maybe_record(path, [HUMAN_LINE], send, now=1000)
    assert server_aim.maybe_record(path, [HUMAN_LINE], send, now=1030) is None  # zu frueh
    assert server_aim.maybe_record(path, [HUMAN_LINE], send, now=1061)
    assert len(sent) == 2 and sent[0].startswith("tv_record coach_")


def test_no_recording_during_training(tmp_path):
    path = _cfg(tmp_path)
    (tmp_path / ".training_server.json").write_text("{}", encoding="utf-8")
    assert server_aim.maybe_record(path, [HUMAN_LINE], lambda *c: ["ok"], now=1000) is None


def test_stop_when_last_human_leaves(tmp_path):
    sent = []

    def send(*c):
        sent.extend(c)
        return ["---------players--------\n  0      BOT    0    0     active      0 'Rex'\n#end"] if c == ("status",) else [""]
    assert server_aim.maybe_stop([LEAVE_LINE], send)
    assert sent == ["status", "tv_stoprecord"]


def _player(sid, name, kills, errors):
    return SimpleNamespace(steam_id=sid, name=name, kills=kills, deaths=5, headshots=kills // 2, damage=900,
                           crosshair_placement_errors=errors, shots_standing=80, shots_running=20,
                           total_shots_hit=25, shots_fired=100)


def test_analyse_keeps_humans_deletes_demo_and_board_takes_median_over_all_kills(tmp_path):
    path = _cfg(tmp_path)
    demos = tmp_path / server_aim.DEMO_DIR
    demos.mkdir()
    for name in ("coach_a.dem", "coach_b.dem"):
        (demos / name).write_bytes(b"x")
    games = iter([[1.0] * 6, [9.0] * 5])

    def parse(_):
        return SimpleNamespace(map_name="aim_multigame", all_players=[
            _player("76561198019262528", "dj", 6, next(games)), _player("0", "Rex", 3, [1.0])])
    assert server_aim.analyse_pending(path, parse=parse) == 2
    assert not list(demos.glob("*.dem"))
    rows = server_aim.board(path)["rows"]
    assert [r["name"] for r in rows] == ["dj"]  # Bot (steamid 0) faellt raus
    dj = rows[0]
    # Median ueber alle 11 Kills (6x 1.0, 5x 9.0) = 1.0, nicht Mittel der Demo-Mediane (5.0)
    assert (dj["kills"], dj["cp"], dj["cs"], dj["acc"], dj["games"]) == (12, 1.0, 80, 25, 2)


def test_inactive_gotv_is_switched_on_and_retried_soon(tmp_path):
    path, sent = _cfg(tmp_path), []
    send = lambda *c: sent.extend(c) or ["GOTV[0] not active."]  # noqa: E731
    server_aim.maybe_record(path, [HUMAN_LINE], send, now=1000)
    assert "tv_enable_dynamic 1" in sent and sent[-1] == "tv_enable 1"
    assert server_aim.due(path, [HUMAN_LINE], now=1011)  # nach 10 s nochmal, nicht erst nach 60
