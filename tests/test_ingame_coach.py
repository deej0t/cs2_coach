"""KI-Coach im Spiel: Antworten kurz und privat, nur fuer den Besitzer, Tipps nur aus Gemessenem."""

from __future__ import annotations

import base64

from cs2_coach import ingame_coach as ic
from cs2_coach import training_server as ts

OWNER = "76561198019262528"


class Server:
    def __init__(self, plugin=True):
        self.sent: list[str] = []
        self.plugin = plugin

    def __call__(self, *cmds):
        self.sent.extend(cmds)
        return ["coach_chat ok" if self.plugin and c.startswith("coach_chat") else "" for c in cmds]

    def private(self) -> list[str]:
        return [base64.b64decode(c.split()[2]).decode("utf-8") for c in self.sent if c.startswith("coach_chat ")]


def cfg(tmp_path):
    return {"steam_id": OWNER, "obsidian_vault_path": ""}, tmp_path / "config.yaml"


def test_clean_strips_markdown_and_cuts_at_sentence():
    text = "**Halte** den Winkel. " + "Dann peek kurz. " * 40
    out = ic.clean(text)
    assert "*" not in out and out.startswith("Halte den Winkel.")
    assert len(out) <= ic.MAX_ANSWER and out.endswith(".")
    assert all(len(line) <= ic.LINE_WIDTH for line in ic.wrap(out))


def test_only_owner_gets_answers(tmp_path):
    c, path = cfg(tmp_path)
    server, asked = Server(), []
    ic.handle(c, path, server, "123", 5, "coach", "wie peeke ich?", ask_fn=lambda *a: asked.append(a))
    assert server.private() == ["Der KI-Coach ist dem Server-Besitzer vorbehalten."] and not asked


def test_question_goes_to_ai_with_facts_and_answer_is_private(tmp_path):
    c, path = cfg(tmp_path)
    server, seen = Server(), {}

    def fake(cfg_, system, prompt):
        seen.update(system=system, prompt=prompt)
        return "Halte A-Rampe mit dem Fadenkreuz auf Kopfhoehe."

    ic.handle(c, path, server, OWNER, 2, "coach", "wie halte ich A-Rampe?", ask_fn=fake)
    assert seen["prompt"] == "Frage des Spielers: wie halte ich A-Rampe?"
    assert "hoechstens 3 kurze Saetze" in seen["system"] and "Keine Matchdaten" in seen["system"]
    assert server.private() == ["Moment, ich schaue nach ...", "Halte A-Rampe mit dem Fadenkreuz auf Kopfhoehe."]
    assert all(c.startswith("coach_chat 2 ") for c in server.sent)  # nur an den Fragenden


def test_ai_down_says_so(tmp_path):
    c, path = cfg(tmp_path)
    server = Server()
    ic.handle(c, path, server, OWNER, 2, "coach", "hilfe", ask_fn=lambda *a: None)
    assert "antwortet gerade nicht" in server.private()[-1]


def test_tipp_explains_last_duel(tmp_path):
    c, path = cfg(tmp_path)
    server, seen = Server(), {}
    ic.handle(c, path, server, OWNER, 2, "tipp", "", ask_fn=lambda *a: "x")
    assert "Noch kein Duell" in server.private()[0]
    ic.save_last_duel(path, {"source": "27.10. R3", "map": "de_mirage", "opponent": "Poepp", "weapon": "M4A4",
                             "outcome": "loss", "tries": 1, "first": "bot", "you_hits": 2, "you_head": 0,
                             "bot_hits": 3, "direction": "links vorne, 18 m", "tip": "Keiner deiner 2 Treffer am Kopf"})
    ic.handle(c, path, server, OWNER, 2, "tipp", "", ask_fn=lambda cfg_, system, prompt: seen.update(p=prompt) or "ok")
    assert "Zuerst getroffen hat: der Gegner." in seen["p"] and "davon am Kopf: 0" in seen["p"]
    assert "links vorne, 18 m" in seen["p"] and "besser machen" in seen["p"]


def test_without_plugin_answer_falls_back_to_say(tmp_path):
    c, path = cfg(tmp_path)
    server = Server(plugin=False)
    ic.handle(c, path, server, OWNER, 2, "coach", "", ask_fn=lambda *a: "x")
    assert any(c.startswith("say [Coach] Frag mich etwas") for c in server.sent)


def test_duel_tips_only_from_measurements():
    assert ts.duel_tip({"you_hits": 3, "you_head": 0, "first": "you"}, "loss").startswith("Keiner deiner 3 Treffer")
    assert ts.duel_tip({"you_hits": 1, "you_head": 1, "first": "bot"}, "loss").startswith("Er hat zuerst getroffen")
    assert "nachsetzen" in ts.duel_tip({"you_hits": 2, "you_head": 1, "first": "you"}, "loss")
    assert ts.duel_tip({"you_hits": 1, "you_head": 1, "first": "you"}, "loss") is None  # nichts Klares
    assert ts.duel_tip({"you_hits": 3, "you_head": 0}, "win") is None


def test_training_history_reaches_the_ai(tmp_path):
    """Ohne Bilanz sagte die KI zu Mirage-Duellen "Dazu liegen mir keine Daten vor"."""
    path = tmp_path / "config.yaml"
    ts.save_session(path, {"map": "de_mirage", "started": "2026-10-30T08:00:00", "duels": [
        {"n": 1, "source": "27.10. R3", "opponent": "Poepp", "weapon": "m4a1", "outcome": "loss", "export": "x", "round": 3},
        {"n": 2, "source": "27.10. R4", "opponent": "Bursche", "weapon": "mp9", "outcome": "win", "export": "y", "round": 4}]},
        "Training fertig")
    facts = ic.training_facts(path)
    assert "auf de_mirage: 1 von 2 Duellen gewonnen" in facts
    assert "Oft verloren: 27.10. R3 gegen Poepp mit M4A4 (1x verloren, 0x gewonnen)" in facts
