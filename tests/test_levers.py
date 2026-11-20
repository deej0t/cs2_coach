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


# --- Lobby (duel_value ueber winprob) ---------------------------------------

CT = [f"7656119800000000{i}" for i in range(5)]
T = [f"7656119800000001{i}" for i in range(5)]
ME = CT[0]


def _lobby_matches(n=24, weak=True):
    """Du (CT[0]) verlierst in Connector jedes Duell, die anderen CT dort fast nie."""
    out = []
    for k in range(n):
        rounds = []
        for i in range(10):
            j = k * 10 + i
            if j % 2 == 0:
                victim, killer, vp, kp = (ME if weak else T[0]), (T[0] if weak else ME), "Connector", "Palace"
                if not weak:
                    victim, vp, kp = T[0], "Palace", "Connector"
            else:
                victim, killer, vp, kp = T[1], CT[1 + j % 4], "Palace", "Connector"
            lost = "CT" if victim in CT else "T"        # wer zuerst stirbt, verliert meistens
            winner = lost if j % 7 == 0 else ("T" if lost == "CT" else "CT")
            rounds.append({"n": i + 1, "map": "Mirage", "winner": winner, "seconds": 60.0,
                           "ct": CT, "t": T, "ct_equip": 20000, "t_equip": 20000,
                           "events": [[15.0, "d", victim, killer, vp, kp]]})
        out.append({"demo": f"m{k:02d}", "rounds": rounds})
    return out


def test_lobby_kandidaten_vergleichen_dich_mit_den_anderen():
    levs, info = levers.lobby_candidates(_lobby_matches(), ME)
    by = {x.key: x for x in levs}
    zone = by["lobby_zone_Mirage_CT_Connector"]
    assert zone.level == "Lobby-Zone" and zone.verdict == "solid_negative" and zone.matches_needed == 0
    assert zone.unit == levers.LOBBY_UNIT and zone.mean_win < 0 < zone.mean_loss
    assert info["demos"] == 24 and info["tested"] >= 1 and info["solid"] >= 1
    assert info["closest"][0]["key"] == zone.key and info["net"]["matches"] == 24
    assert all(x.level in levers.LOBBY_LEVELS for x in levs)
    assert levers.lobby_candidates(_lobby_matches()[:1], ME) == ([], {**info, "demos": 1, "tested": 0,
                                                                     "solid": 0, "closest": [], "net": None})


def test_lobby_meldet_erst_nach_vollstaendigem_einlesen(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("cs2_coach.report_ai._send_text", lambda cfg, cp, msg, log: sent.append(msg))
    cp = tmp_path / "config.yaml"
    cfg = {**_write(tmp_path, _matches(0.0)), "steam_id": ME}
    state = {"matches": _lobby_matches(weak=False), "stats": {"new": 0, "waiting": 30}}

    def fake_collect(cfg, config_path, log=None, max_new=0, stats=None):
        stats.update(state["stats"])
        return state["matches"]

    monkeypatch.setattr("cs2_coach.winprob.collect", fake_collect)
    assert levers.check(cfg, cp) == []                          # erster Lauf: Ausgangslage
    state["matches"] = _lobby_matches(weak=True)                # Urteil kippt nur durch Nachladen
    assert levers.check(cfg, cp) == [] and sent == []
    assert levers.load(cp)["lobby"]["complete"] is False
    state["stats"] = {"new": 0, "waiting": 0}                   # alles eingelesen -> Ausgangslage
    assert levers.check(cfg, cp) == [] and sent == []
    state["matches"] = _lobby_matches(weak=False)
    levers.check(cfg, cp)
    state["matches"] = _lobby_matches(weak=True)                # jetzt kippt es wirklich neu
    new = levers.check(cfg, cp)
    # alle Testduelle sind 5v5 - die Lage kippt mit der Zone
    assert sorted(x["key"] for x in new) == ["lobby_sit_CT_Gleichstand_0", "lobby_zone_Mirage_CT_Connector"]
    assert len(sent) == 1 and "du schwächer als die Lobby" in sent[0] and "24 Demos" in sent[0]
