"""Telegram-Versand fuer das Briefing."""

from __future__ import annotations

import json
from datetime import datetime

from cs2_coach import briefing, telegram

from test_briefing import _Resp, _setup


class _Json:
    def __init__(self, data):
        self._d = data
        self.status_code = 200

    def json(self):
        return self._d


def test_html_conversion_keeps_map_names():
    out = telegram.to_html("**Heute** `map de_mirage` & <x>\n_KI nicht verfuegbar_")
    assert out == ("<b>Heute</b> <code>map de_mirage</code> &amp; &lt;x&gt;\n"
                   "<i>KI nicht verfuegbar</i>")


def test_chat_id_is_found_once_and_remembered(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    calls = []

    def get(url, timeout):
        calls.append(url)
        return _Json({"result": [{"message": {"chat": {"id": 111}}},
                                 {"message": {"chat": {"id": 4242}}}]})
    assert telegram.find_chat_id("123:ABCDEF", cfg_path, get=get) == "4242"
    assert telegram.find_chat_id("123:ABCDEF", cfg_path, get=lambda *a, **k: 1 / 0) == "4242"
    assert len(calls) == 1
    # Neues Token -> neu suchen, die alte Chat-ID gehoert zum alten Bot.
    assert telegram.find_chat_id("999:XYZXYZ", cfg_path, get=lambda u, timeout: _Json({"result": []})) == ""


def test_long_text_is_split_below_telegram_limit():
    sent = []
    ok = telegram.send("t", "1", "\n\n".join(["x" * 1500] * 6),
                       post=lambda url, timeout, json: sent.append(json["text"]) or _Resp(200))
    assert ok and len(sent) == 3 and all(len(t) <= 4096 for t in sent)


def test_failed_telegram_is_retried_without_reposting_discord(tmp_path, monkeypatch):
    cfg, config = _setup(tmp_path, monkeypatch)
    cfg["telegram_bot_token"] = "123:ABCDEF"
    (tmp_path / ".telegram_chat.json").write_text(
        json.dumps({"chat_id": 4242, "token_tail": "ABCDEF"}), encoding="utf-8")
    discord, tg = [], []
    dpost = lambda url, json, timeout: discord.append(1) or _Resp(204)  # noqa: E731
    now = datetime(2026, 10, 16, 17, 5)

    assert briefing.run(cfg, config, send=True, now=now, log=lambda m: None, post=dpost,
                        telegram_post=lambda *a, **k: _Resp(502)) is None
    assert briefing.run(cfg, config, send=True, now=now, log=lambda m: None, post=dpost,
                        telegram_post=lambda url, timeout, json: tg.append(json) or _Resp(200))
    assert len(discord) == 1 and len(tg) == 1
    assert tg[0]["chat_id"] == "4242" and tg[0]["parse_mode"] == "HTML"
    # Danach ist der Tag erledigt.
    assert briefing.run(cfg, config, send=True, now=now, log=lambda m: None, post=dpost) is None
    assert len(discord) == 1


def test_missing_chat_does_not_block_the_day(tmp_path, monkeypatch):
    # Ohne /start an den Bot gibt es keinen Chat. Das darf nicht dazu
    # fuehren, dass alle 15 Minuten erneut die KI befragt wird.
    cfg, config = _setup(tmp_path, monkeypatch)
    cfg["telegram_bot_token"] = "123:ABCDEF"
    monkeypatch.setattr(telegram, "find_chat_id", lambda token, path: "")
    msgs = []
    text = briefing.run(cfg, config, send=True, now=datetime(2026, 10, 16, 17), log=msgs.append,
                        post=lambda url, json, timeout: _Resp(204))
    assert text and any("/start" in m for m in msgs)
