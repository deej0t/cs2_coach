"""Tages-Briefing per Knopfdruck in der WebUI."""

from __future__ import annotations

import json
import threading
import time

from cs2_coach.web import daily_briefing as D


def _wait(path, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        time.sleep(0.02)
    raise AssertionError("kein Ergebnis geschrieben")


def test_send_runs_in_background_and_blocks_double_click(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    release = threading.Event()
    calls = []

    def runner(cfg, path, **kw):
        calls.append(kw)
        release.wait(5)
        return "Briefing"

    assert D.send_now({}, cfg_path, runner=runner) is True
    # Doppelklick, waehrend der erste Versand noch laeuft: kein zweiter.
    assert D.send_now({}, cfg_path, runner=runner) is False
    release.set()
    result = _wait(tmp_path / ".briefing_manual.json")
    assert result["ok"] is True
    assert len(calls) == 1 and calls[0]["send"] and calls[0]["force"]
    assert not (tmp_path / ".briefing_running").exists()
    # Danach geht es wieder.
    assert D.send_now({}, cfg_path, runner=lambda *a, **k: "x") is True


def test_errors_are_recorded_and_lock_released(tmp_path):
    def runner(cfg, path, **kw):
        kw["log"]("Discord lehnt ab: HTTP 400")
        raise RuntimeError("kaputt")

    assert D.send_now({}, tmp_path / "config.yaml", runner=runner)
    result = _wait(tmp_path / ".briefing_manual.json")
    assert result["ok"] is False and "Fehler: kaputt" in result["log"]
    assert not (tmp_path / ".briefing_running").exists()


def test_stale_lock_from_crashed_worker_is_ignored(tmp_path):
    import os
    lock = tmp_path / ".briefing_running"
    lock.write_text("")
    old = time.time() - D.STALE_LOCK - 5
    os.utime(lock, (old, old))
    assert D.send_now({}, tmp_path / "config.yaml", runner=lambda *a, **k: "x") is True


def test_channels_reflect_settings():
    assert D._channels({}) == []
    assert D._channels({"discord_webhook": "h", "telegram_bot_token": " t "}) == ["Discord", "Telegram"]
