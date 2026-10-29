"""Server-Bestenliste live: Chat-Befehle, Einschleusungsschutz, Discord-Meldungen genau einmal."""

from __future__ import annotations

from datetime import datetime

from cs2_coach import server_live as sl
from cs2_coach import server_stats as ss

DJ = '"dj<2><[U:1:58996800]><CT>"'
DA = '"Dancho<3><[U:1:923059335]><TERRORIST>"'
DJ64 = str(ss.STEAM64_BASE + 58996800)
NOW = datetime(2026, 10, 30, 1, 0)
CFG = {"public_url": "https://cs2-coach.deej0t.eu", "server_rcon_host": "srv",
       "server_rcon_password": "pw", "discord_webhook": "https://hook"}


def line(sec: int, msg: str, minute: int = 0) -> str:
    return f"10/30/2026 - 00:{minute:02d}:{sec:02d}.100 - {msg}"


def kill(sec, a, v, flags="", minute=0):
    tail = f" ({flags})" if flags else ""
    return line(sec, f'{a} [0 0 0] killed {v} [1 1 1] with "ak47"{tail}', minute)


def store(tmp_path, lines, now=NOW):
    cfg = tmp_path / "config.yaml"
    ss.store_batch(cfg, {"X-Server-Instance-Token": "i"}, ("\n".join(lines) + "\n").encode(), now=now)
    return cfg


def duel_lines(n_dj: int, n_da: int) -> list[str]:
    out = [kill(s % 60, DJ, DA, "headshot" if s % 2 else "", minute=s // 60) for s in range(n_dj)]
    out += [kill(s % 60, DA, DJ, minute=10 + s // 60) for s in range(n_da)]
    return out


def test_parse_commands_from_chat():
    cmds = sl.parse_commands([line(1, f'{DJ} say "!top"'), line(2, f'{DA} say_team "!vs dj"'),
                              line(3, f'{DJ} say "gg"'), line(4, '"Bot<4><BOT><CT>" say "!top"')])
    assert cmds == [(DJ64, "dj", "top", ""), (str(ss.STEAM64_BASE + 923059335), "Dancho", "vs", "dj")]


def test_names_cannot_inject_rcon_commands():
    say = sl._say_commands(['Platz 1: x"; rcon_password hacked; quit // \n'])
    assert say == ["say [Coach] Platz 1: x rcon_password hacked quit //"]
    assert all('"' not in c and ";" not in c for c in say)


def test_long_answers_are_split():
    say = sl._say_commands(["x" * 300])
    assert len(say) == 3 and all(len(c) <= sl.MAX_SAY + 6 for c in say)


def test_answers(tmp_path):
    log = ss.read_logs(store(tmp_path, duel_lines(25, 10)))
    top = sl.answer("top", "", DJ64, "dj", log, CFG, now=NOW)
    assert top[0].startswith("Top 5: 1. dj") and "Dancho" in top[0]
    assert top[1] == "Alle Werte: cs2-coach.deej0t.eu/server"
    rank = sl.answer("myrank", "", DJ64, "dj", log, CFG, now=NOW)[0]
    assert "Platz 1 von 2" in rank and "25:10" in rank
    assert sl.answer("vs", "danc", DJ64, "dj", log, CFG, now=NOW) == ["dj gegen Dancho: 25:10 Kills"]
    assert "Nemesis Dancho (10x" in sl.answer("nemesis", "", DJ64, "dj", log, CFG, now=NOW)[0]
    assert "letzte 6 h: 25:10" in sl.answer("session", "", DJ64, "dj", log, CFG, now=NOW)[0]
    assert sl.answer("help", "", DJ64, "dj", log, CFG, now=NOW)[0] == sl.HELP
    assert sl.answer("quatsch", "", DJ64, "dj", log, CFG, now=NOW) == []


def test_provisional_player_sees_missing_duels(tmp_path):
    log = ss.read_logs(store(tmp_path, duel_lines(3, 2)))
    assert "noch 25 Duelle bis zur Wertung" in sl.answer("rank", "", DJ64, "dj", log, CFG, now=NOW)[0]
    assert sl.answer("top", "", DJ64, "dj", log, CFG, now=NOW)[0].startswith("Noch niemand mit 30 Duellen")


def test_handle_commands_sends_via_rcon_with_cooldown(tmp_path):
    cfg_path = store(tmp_path, duel_lines(5, 1))
    sent = []
    batch = [line(30, f'{DJ} say "!rank"'), line(31, f'{DJ} say "!top"')]
    sl._last_command.clear()
    assert sl.handle_commands(CFG, cfg_path, batch, send=lambda *c: sent.append(c), now=NOW) == 1
    assert sent[0][0].startswith("say [Coach] dj: vorläufig")
    assert sl.handle_commands({}, cfg_path, batch, send=lambda *c: sent.append(c)) == 0  # ohne RCON


def test_game_over_posted_once(tmp_path):
    lines = [line(0, 'Loading map "aim_map"')] + duel_lines(8, 2) + [
        line(59, "Game Over: competitive mg_active aim_map score 8:2 after 3 min", minute=20)]
    cfg_path = store(tmp_path, lines)
    posts = []

    class Ok:
        status_code = 204

    def post(url, json, timeout):
        posts.append(json["content"])
        return Ok()

    cfg = dict(CFG)
    assert any(k.startswith("game-") for k in sl.handle_posts(cfg, cfg_path, lines, post=post, now=NOW))
    assert "Spielende auf aim_map" in posts[0] and "MVP: **dj** 8:2" in posts[0]
    n = len(posts)
    sl.handle_posts(cfg, cfg_path, lines, post=post, now=NOW)  # derselbe Stapel noch einmal
    assert len(posts) == n


def test_failed_post_is_retried(tmp_path):
    lines = duel_lines(8, 2) + [line(59, "Game Over: deathmatch mg_x aim_map score 0:0 after 10 min", minute=20)]
    cfg_path = store(tmp_path, lines)

    class Bad:
        status_code = 500

    assert sl.handle_posts(CFG, cfg_path, lines, post=lambda *a, **k: Bad(), now=NOW) == []
    posts = []

    class Ok:
        status_code = 204

    sent = sl.handle_posts(CFG, cfg_path, lines, post=lambda u, json, timeout: posts.append(json) or Ok(), now=NOW)
    assert any(k.startswith("game-") for k in sent)


def test_player_of_the_week(tmp_path):
    # Montag 26.10. bis Sonntag 1.11. gespielt, gemeldet am Montag 2.11.
    lines = [f"10/28/2026 - 20:{i // 60:02d}:{i % 60:02d}.000 - " + kill(0, DJ, DA).split(" - ", 2)[2]
             for i in range(40)]
    cfg_path = tmp_path / "config.yaml"
    ss.store_batch(cfg_path, {}, ("\n".join(lines) + "\n").encode(), now=datetime(2026, 10, 28, 20, 0))
    key, text = sl.weekly_summary(ss.read_logs(cfg_path), CFG, now=datetime(2026, 11, 2, 18, 0))
    assert key == "week-2026-W44"
    assert "Spieler der Woche (KW 44)**: **dj**" in text
    key, text = sl.weekly_summary(ss.read_logs(cfg_path), CFG, now=datetime(2026, 11, 9, 18, 0))
    assert key == "week-2026-W45" and text is None  # in der Woche danach niemand gespielt
