"""Trainingsziele, taegliches Briefing und Sync.

Das Briefing laeuft unbeaufsichtigt jeden Tag. Getestet wird deshalb vor
allem, was dabei still schiefgehen koennte: Startzeitpunkte, die sich
verschieben (dann gaebe es nie einen Fortschrittsvergleich), doppelte
oder verlorene Nachrichten und eine KI, die Zahlen erfindet.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from cs2_coach import briefing, health, sync, training


def _export(i: int, win: bool, he: int, day: str = "2026-09", rounds: int = 20) -> dict:
    return {
        "match": {"datetime": f"{day}-{1 + i % 28:02d} {10 + i // 28:02d}:00",
                  "map": "Mirage", "result": "Sieg" if win else "Niederlage",
                  "score_own": 13 if win else 7, "score_enemy": 7 if win else 13,
                  "total_rounds": rounds},
        "player": {"rating": 1.0, "adr": 80.0},
        "utility_positions": [{"t": "h"}] * he + [{"t": "s"}] * 5,
    }


def _history() -> list[dict]:
    """40 Matches, in denen HE pro Runde Siege klar von Niederlagen trennt."""
    out = []
    for i in range(40):
        win = i % 2 == 0
        out.append(_export(i, win, (10 if win else 4) + i % 3))
    return out


@pytest.fixture(autouse=True)
def _no_findings(monkeypatch):
    # Die Befund-Regeln brauchen vollstaendige Spielerdaten; hier geht es
    # nur um die Granatenziele.
    monkeypatch.setattr(training, "training_priorities", lambda matches: [])


# ── Trainingsziele ───────────────────────────────────────────────────

def test_goal_start_is_fixed_on_first_appearance(tmp_path):
    state = tmp_path / ".training_state.json"
    plan = training.current_plan(_history(), state, "2026-10-01 12:00")
    he = [g for g in plan["goals"] if g.key == "util_h"]
    assert he, "HE pro Runde trennt Siege belastbar und muss ein Ziel sein"
    assert he[0].since == "2026-10-01 12:00"
    assert he[0].change is None and "0 Matches" in he[0].note

    # Ein spaeterer Lauf darf den Start nicht verschieben, sonst gaebe es
    # nie Matches "seit Start".
    plan = training.current_plan(_history(), state, "2026-10-05 12:00")
    assert [g for g in plan["goals"] if g.key == "util_h"][0].since == "2026-10-01 12:00"


def test_progress_is_compared_after_enough_matches(tmp_path):
    state = tmp_path / ".training_state.json"
    training.current_plan(_history(), state, "2026-10-01 12:00")

    later = [_export(i, i % 2 == 0, 12 + i % 2, day="2026-10") for i in range(5, 5 + training.MIN_AFTER)]
    plan = training.current_plan(_history() + later, state, "2026-10-20 12:00")
    g = [g for g in plan["goals"] if g.key == "util_h"][0]
    assert g.matches_since == training.MIN_AFTER
    assert g.change is not None
    assert g.change["n_a"] == training.MIN_AFTER and g.change["n_b"] == 40
    assert g.change["mean_a"] > g.change["mean_b"]
    assert plan["win_change"]["n_a"] == training.MIN_AFTER


def test_goal_that_disappears_is_marked_ended(tmp_path):
    state = tmp_path / ".training_state.json"
    training.current_plan(_history(), state, "2026-10-01 12:00")
    no_signal = [_export(i, i % 2 == 0, 6 + i % 3) for i in range(40)]
    plan = training.current_plan(no_signal, state, "2026-10-09 12:00")
    assert "util_h" not in {g.key for g in plan["goals"]}
    assert plan["ended"]["util_h"]["ended"] == "2026-10-09 12:00"


# ── Fakten und KI ────────────────────────────────────────────────────

def _plan(tmp_path):
    return training.current_plan(_history(), tmp_path / "t.json", "2026-10-01 12:00")


def test_first_briefing_lists_only_the_last_matches(tmp_path):
    facts, newest = briefing.build_facts(_history(), _plan(tmp_path), "")
    assert "Letzte Matches (erstes Briefing)" in facts
    assert facts.count("Mirage") == 3
    assert newest == max(training._when(e) for e in _history())


def test_later_briefing_lists_new_matches_or_says_none(tmp_path):
    exports = _history()
    newest = max(training._when(e) for e in exports)
    facts, _ = briefing.build_facts(exports, _plan(tmp_path), newest)
    assert "Neue Matches seit dem letzten Briefing: 0" in facts
    assert "Keine." in facts


def test_unknown_numbers_ignore_single_digits_and_known_values():
    facts = "Zielwert 0.44/Runde, zuletzt 0.26"
    assert briefing._unknown_numbers("Ziel 0,44, zuletzt 0.26, 3 Uebungen", facts) == []
    assert briefing._unknown_numbers("Du hast 17 % mehr Siege, 0.51 HE", facts) == ["17", "0.51"]


def _cfg():
    return {"ai_provider": "gemini", "gemini_api_key": "k", "ai_model": "m"}


def test_ai_is_retried_after_overload(monkeypatch):
    answers = iter(["**Fehler:** HTTP Error 503", "**Fehler:** HTTP Error 503", "Heute: HE 0.44"])
    monkeypatch.setattr("cs2_coach.ai_chat.call_gemini", lambda *a, **k: next(answers))
    waits = []
    assert briefing.ask_ai(_cfg(), "Zielwert 0.44", log=lambda m: None, sleep=waits.append) == "Heute: HE 0.44"
    assert waits == list(briefing.RETRY_WAITS)


def test_ai_text_with_invented_numbers_is_rejected(monkeypatch):
    monkeypatch.setattr("cs2_coach.ai_chat.call_gemini",
                        lambda *a, **k: "Deine Siegquote stieg um 23 % auf 61 %, 0.57 HE")
    assert briefing.ask_ai(_cfg(), "Zielwert 0.44", log=lambda m: None, sleep=lambda s: None) is None


# ── Versand ──────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, code):
        self.status_code = code


def _setup(tmp_path, monkeypatch, ai="**Heute trainieren** HE"):
    exports = tmp_path / "vault" / "CS2-Coach" / "exports"
    exports.mkdir(parents=True)
    for i, e in enumerate(_history()):
        (exports / f"{i:03d}_coach.json").write_text(json.dumps(e), encoding="utf-8")
    monkeypatch.setattr(briefing, "ask_ai", lambda cfg, facts, log=print: ai)
    cfg = {"obsidian_vault_path": str(tmp_path / "vault"), "discord_webhook": "https://hook"}
    return cfg, tmp_path / "config.yaml"


def test_sends_once_per_day_and_writes_note(tmp_path, monkeypatch):
    cfg, config = _setup(tmp_path, monkeypatch)
    sent = []
    post = lambda url, json, timeout: sent.append(json["content"]) or _Resp(204)  # noqa: E731
    now = datetime(2026, 10, 16, 17, 5)

    text = briefing.run(cfg, config, send=True, now=now, log=lambda m: None, post=post)
    assert text and "Briefing 16.10.2026" in sent[0] and "**Heute trainieren** HE" in sent[0]
    note = tmp_path / "vault" / "CS2-Coach" / "Briefings" / "2026-10-16.md"
    assert "tags: [cs2-briefing]" in note.read_text(encoding="utf-8")

    # Neustart oder naechster Schleifendurchlauf am selben Tag: nichts doppelt.
    assert briefing.run(cfg, config, send=True, now=now, log=lambda m: None, post=post) is None
    assert len(sent) == 1
    # Am naechsten Tag wieder.
    briefing.run(cfg, config, send=True, now=datetime(2026, 10, 17, 17, 0), log=lambda m: None, post=post)
    assert len(sent) == 2


def test_failed_post_is_retried_later(tmp_path, monkeypatch):
    cfg, config = _setup(tmp_path, monkeypatch)
    now = datetime(2026, 10, 16, 17, 5)
    assert briefing.run(cfg, config, send=True, now=now, log=lambda m: None,
                        post=lambda *a, **k: _Resp(429)) is None
    sent = []
    briefing.run(cfg, config, send=True, now=now, log=lambda m: None,
                 post=lambda url, json, timeout: sent.append(1) or _Resp(204))
    assert sent == [1]


def test_without_ai_the_facts_are_sent(tmp_path, monkeypatch):
    cfg, config = _setup(tmp_path, monkeypatch, ai=None)
    text = briefing.run(cfg, config, send=False, now=datetime(2026, 10, 16), log=lambda m: None)
    assert "KI nicht verfuegbar" in text and "HE pro Runde: Zielwert" in text


def test_chunks_respect_discord_limit():
    parts = briefing._chunks("\n\n".join(["x" * 700] * 6), 1900)
    assert len(parts) == 3 and all(len(p) <= 1900 for p in parts)


# ── Sync ─────────────────────────────────────────────────────────────

def test_sync_skips_demos_already_present_with_suffix(tmp_path, monkeypatch):
    mid = "3790000000000000001"
    (tmp_path / f"match730_{mid.zfill(21)}_274.dem").write_bytes(b"")
    listed = [{"match_id": mid, "url": "u1"}, {"match_id": "3790000000000000002", "url": "u2"}]
    fetched = []
    import cs2_coach.sharecode as sc
    monkeypatch.setattr(sc, "load_steam_session", lambda: object())
    monkeypatch.setattr(sc, "fetch_gcpd_demo_urls", lambda s, sid, on_status=None: listed)

    def fake_download(url, folder, match_date="", on_status=None):
        fetched.append(url)
        p = folder / f"{url}.dem"
        p.write_bytes(b"")
        return p
    monkeypatch.setattr(sc, "download_demo_bz2", fake_download)

    new = sync.download_new_demos({"demo_folder": str(tmp_path)}, log=lambda m: None)
    assert fetched == ["u2"] and [p.name for p in new] == ["u2.dem"]


def test_sync_without_demo_folder_does_nothing(monkeypatch):
    import cs2_coach.sharecode as sc
    monkeypatch.setattr(sc, "load_steam_session", lambda: pytest.fail("darf nicht laufen"))
    assert sync.download_new_demos({"demo_folder": ""}, log=lambda m: None) == []


def test_health_empty_demo_folder_is_not_the_working_directory():
    # Path("") ist "." - eine fehlende Einstellung galt als vorhandener Ordner.
    assert health.check_demos({"demo_folder": ""}).state == health.FAIL
