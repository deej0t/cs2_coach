"""Hebel-Suche (cs2_coach/levers.py)."""

from __future__ import annotations

import json

from cs2_coach import levers


def _export(i, win, acc):
    return {"match": {"map": "Mirage", "result": "Sieg" if win else "Niederlage", "total_rounds": 20,
                      "datetime": f"2026-11-{1 + i // 10:02d} 2{i % 10}:00", "score_own": 13, "score_enemy": 7},
            "player": {"accuracy": acc, "survival_rate": 50.0 if win else 20.0}}


def _write(tmp_path, exports):
    folder = tmp_path / "CS2-Coach" / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    for f in folder.glob("*.json"):
        f.unlink()
    for i, e in enumerate(exports):
        (folder / f"2026-11-{1 + i // 10:02d}_mirage_13-7_2{i % 10}00_coach.json").write_text(json.dumps(e))
    return {"obsidian_vault_path": str(tmp_path)}


def _matches(spread):
    """20 Siege, 20 Niederlagen; Accuracy in Siegen um *spread* hoeher."""
    out = []
    for i in range(40):
        win = i % 2 == 0
        out.append(_export(i, win, 24.0 + (spread if win else 0) + (i % 5) * 0.5))
    return out


def test_kandidaten_mit_ebene_und_markierung():
    levs, zones = levers.candidates(_matches(4.0))
    by = {x.key: x for x in levs}
    assert by["accuracy"].level == "Match" and by["accuracy"].solid
    assert by["survival"].outcome_driven                    # steigt einfach mit dem Sieg
    assert zones["tested"] == 0


def test_erster_lauf_meldet_nichts_dann_genau_einmal(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("cs2_coach.report_ai._send_text", lambda cfg, cp, msg, log: sent.append(msg))
    cp = tmp_path / "config.yaml"
    cfg = _write(tmp_path, _matches(0.0))
    assert levers.check(cfg, cp) == [] and sent == []        # Ausgangslage
    cfg = _write(tmp_path, _matches(4.0))
    new = levers.check(cfg, cp)
    assert [x["key"] for x in new] == ["accuracy"]           # Survival ist ergebnisabhaengig
    assert len(sent) == 1 and "Accuracy" in sent[0]
    assert levers.check(cfg, cp) == [] and len(sent) == 1    # schon bekannt: keine zweite Nachricht
    assert levers.load(cp)["matches"] == 40
