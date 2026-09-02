"""Tests fuer die Speicherung der KI-Chat-Sitzungen.

Der Chat war zustandslos: der Verlauf lebte in einer Variable im Browser
und war nach einem Reload verloren.
"""

from __future__ import annotations

import json

import pytest

from cs2_coach import chat_store as CS

SUB = "CS2-Coach"


def msgs(*pairs):
    out = []
    for role, content in pairs:
        out.append({"role": role, "content": content})
    return out


def test_save_creates_json_and_markdown(tmp_path):
    meta = CS.save_session(str(tmp_path), SUB, msgs(
        ("user", "Wie verbessere ich meine Utility?"),
        ("assistant", "Wirf mehr Smokes."),
    ))
    d = CS.chats_dir(str(tmp_path), SUB)
    assert (d / f"{meta['id']}.json").exists()
    assert (d / f"{meta['id']}.md").exists(), "Markdown fuer Obsidian"


def test_title_comes_from_first_user_message(tmp_path):
    meta = CS.save_session(str(tmp_path), SUB, msgs(
        ("assistant", "Hallo!"),
        ("user", "Warum verliere ich auf Nuke?"),
    ))
    assert meta["title"] == "Warum verliere ich auf Nuke?"


def test_long_title_is_shortened():
    long = "wort " * 40
    t = CS.derive_title(msgs(("user", long)))
    assert len(t) <= CS.TITLE_MAX + 1
    assert t.endswith("…")


def test_title_without_user_message():
    assert CS.derive_title(msgs(("assistant", "hi"))) == "Ohne Titel"


def test_update_keeps_id_and_created(tmp_path):
    first = CS.save_session(str(tmp_path), SUB, msgs(("user", "Frage 1")))
    second = CS.save_session(str(tmp_path), SUB,
                             msgs(("user", "Frage 1"), ("assistant", "Antwort"),
                                  ("user", "Frage 2")),
                             session_id=first["id"])
    assert second["id"] == first["id"]
    assert second["created"] == first["created"]
    assert second["message_count"] == 3
    assert len(CS.list_sessions(str(tmp_path), SUB)) == 1, "kein Duplikat"


def test_load_returns_full_history(tmp_path):
    meta = CS.save_session(str(tmp_path), SUB, msgs(
        ("user", "A"), ("assistant", "B"), ("user", "C")))
    loaded = CS.load_session(str(tmp_path), SUB, meta["id"])
    assert [m["content"] for m in loaded["messages"]] == ["A", "B", "C"]


def test_list_is_sorted_newest_first(tmp_path):
    a = CS.save_session(str(tmp_path), SUB, msgs(("user", "alt")))
    b = CS.save_session(str(tmp_path), SUB, msgs(("user", "neu")))
    # Zeitstempel sind minutengenau; Reihenfolge ueber updated erzwingen
    p = CS.chats_dir(str(tmp_path), SUB) / f"{a['id']}.json"
    r = json.loads(p.read_text(encoding="utf-8"))
    r["updated"] = "2020-01-01 00:00"
    p.write_text(json.dumps(r), encoding="utf-8")

    ids = [s["id"] for s in CS.list_sessions(str(tmp_path), SUB)]
    assert ids[0] == b["id"] and ids[-1] == a["id"]


def test_delete_removes_both_files(tmp_path):
    meta = CS.save_session(str(tmp_path), SUB, msgs(("user", "weg damit")))
    d = CS.chats_dir(str(tmp_path), SUB)
    assert CS.delete_session(str(tmp_path), SUB, meta["id"]) is True
    assert not (d / f"{meta['id']}.json").exists()
    assert not (d / f"{meta['id']}.md").exists()


def test_delete_unknown_session_is_false(tmp_path):
    assert CS.delete_session(str(tmp_path), SUB, "abcdef123456") is False


@pytest.mark.parametrize("bad", [
    "../../etc/passwd", "..", "/absolut", "with/slash",
    "x" * 40, "GROSS", "kurz", "", "semi;colon",
])
def test_path_traversal_is_rejected(tmp_path, bad):
    """Die ID kommt aus einer HTTP-Anfrage und darf nicht ausbrechen."""
    assert CS.load_session(str(tmp_path), SUB, bad) is None
    assert CS.delete_session(str(tmp_path), SUB, bad) is False


def test_empty_messages_are_not_saved(tmp_path):
    assert CS.save_session(str(tmp_path), SUB, []) is None
    assert CS.list_sessions(str(tmp_path), SUB) == []


def test_no_vault_is_survivable():
    assert CS.save_session("", SUB, msgs(("user", "x"))) is None
    assert CS.list_sessions("", SUB) == []
    assert CS.load_session("", SUB, "abcdef123456") is None


def test_corrupt_file_does_not_break_listing(tmp_path):
    CS.save_session(str(tmp_path), SUB, msgs(("user", "gut")))
    (CS.chats_dir(str(tmp_path), SUB) / "kaputt.json").write_text("{nope", encoding="utf-8")
    assert len(CS.list_sessions(str(tmp_path), SUB)) == 1


def test_markdown_contains_both_speakers(tmp_path):
    meta = CS.save_session(str(tmp_path), SUB, msgs(
        ("user", "Meine Frage"), ("assistant", "Meine Antwort")))
    md = (CS.chats_dir(str(tmp_path), SUB) / f"{meta['id']}.md").read_text(encoding="utf-8")
    assert "**Du:**" in md and "**Coach:**" in md
    assert "Meine Frage" in md and "Meine Antwort" in md
    assert "tags: [cs2-coach, ki-chat]" in md
