"""Tests fuer die Startseite (cs2_coach/dashboard.py)."""

from __future__ import annotations

import json

from cs2_coach import dashboard


def export(new, change, map_="Cache", result="Sieg", when="2026-11-12 21:43"):
    return {"match": {"map": map_, "result": result, "score_own": 13, "score_enemy": 6, "datetime": when},
            "player": {"rank": {"old": new - change, "new": new, "change": change}}}


def test_zahlen_im_deutschen_format():
    assert dashboard.de(15387) == "15.387"
    assert dashboard.de(1934, sign=True) == "+1.934"
    assert dashboard.de(-415, sign=True) == "−415"


def test_premier_stufe_und_naechste_stufe():
    p = dashboard._premier([export(15025, -415), export(15387, 362)])
    assert (p["value"], p["tier"], p["change_txt"]) == ("15.387", "Lila", "+362")
    assert p["next"]["name"] == "Pink" and p["next"]["missing"] == "4.613"
    assert p["delta"] == "−53"          # Start der Kurve = 15.440 vor dem ersten Match
    assert p["dots"][-1]["last"] and not p["dots"][0]["last"]


def test_wettkampf_ranks_sind_kein_premier_rating():
    """Map-Ranks im Wettkampf sind 0-18 - die duerfen nicht als Rating erscheinen."""
    assert dashboard._premier([export(14, 1), export(15, 1)]) is None
    tile = dashboard._tile(export(15, 1))
    assert tile["change"] is None and tile["change_txt"] == ""


def test_build_ohne_exporte(tmp_path):
    cfg = {"obsidian_vault_path": str(tmp_path)}
    assert dashboard.build(cfg, tmp_path / "config.yaml") == {"has_data": False}


def test_build_mit_exporten(tmp_path):
    folder = tmp_path / "CS2-Coach" / "exports"
    folder.mkdir(parents=True)
    for i, (new, ch) in enumerate([(15025, -415), (15387, 362)]):
        (folder / f"2026-11-1{i}_cache_13-6_2143_coach.json").write_text(
            json.dumps(export(new, ch, when=f"2026-11-1{i} 21:43")), encoding="utf-8")
    dashboard._cache["key"] = None
    d = dashboard.build({"obsidian_vault_path": str(tmp_path)}, tmp_path / "config.yaml")
    assert d["has_data"] and d["total"] == 2
    assert d["premier"]["value"] == "15.387"
    assert d["last"]["change_txt"] == "+362" and d["last"]["radar"] == "cache"
    assert [t["change_txt"] for t in d["tiles"]] == ["+362", "−415"]


def test_begruessung_nach_tageszeit():
    from datetime import datetime
    assert dashboard._greeting(datetime(2026, 11, 13, 21, 0), "deej0t") == "Guten Abend, deej0t"
    assert dashboard._greeting(datetime(2026, 11, 13, 8, 0), "") == "Guten Morgen"


def test_letzte_sieben_tage():
    from datetime import datetime
    now = datetime(2026, 11, 13, 22, 0)
    ex = [export(15000, 100, when="2026-11-01 20:00"),                     # zu alt
          export(15387, 362, when="2026-11-12 21:43"),
          export(15025, -415, result="Niederlage", when="2026-11-12 21:05"),
          export(14, 1, when="2026-11-10 20:00")]                          # Wettkampf, kein Premier
    w = dashboard._week(ex, now)
    assert (w["n"], w["w"], w["l"], w["delta"], w["up"]) == (3, 2, 1, "−53", False)
    assert dashboard._week(ex[:1], now) is None


def test_rundenverlauf_mit_halbzeit_und_kills():
    e = {"round_timeline": [{"round": r, "won": r % 2 == 0, "side": "CT" if r <= 12 else "T",
                             "player_kills": 2 if r == 13 else 0, "player_died": r == 1} for r in range(1, 15)]}
    rd = dashboard._rounds(e)
    assert [r["n"] for r in rd if r["gap"]] == [12]
    assert rd[12]["kills"] == 2 and "2 Kills" in rd[12]["title"] and rd[12]["side"] == "T"
    assert "gestorben" in rd[0]["title"]


def test_live_status_und_letztes_live_match(tmp_path):
    import os
    (tmp_path / ".gsi_last").write_text("")
    os.utime(tmp_path / ".gsi_last", (1000.0, 1000.0))
    (tmp_path / "live_matches").mkdir()
    (tmp_path / "live_matches" / "2026-11-14T20-00-00_de_mirage.json").write_text(json.dumps(
        {"map": "de_mirage", "started": "2026-11-14T20:00:00", "summary": {"early": 5, "full_buys": 7}}))
    live = dashboard._live(tmp_path / "config.yaml", now=1010.0)
    assert live["connected"] and live["ago"] == "vor 10 s"
    assert live["last"] == {"map": "Mirage", "date": "14.11.", "early": 5, "full_buys": 7}
    assert not dashboard._live(tmp_path / "config.yaml", now=1000.0 + 7200)["connected"]


def test_warnband_bei_liegengebliebener_demo(tmp_path):
    import os
    demos = tmp_path / "demos"
    demos.mkdir()
    (demos / "alt.dem").write_bytes(b"x")
    os.utime(demos / "alt.dem", (1000.0, 1000.0))
    cfg = {"demo_folder": str(demos), "obsidian_vault_path": str(tmp_path)}
    (tmp_path / "CS2-Coach" / "exports").mkdir(parents=True)
    assert "alt.dem" in dashboard._problems(cfg, [])[0]
    assert dashboard._problems(cfg, [{"match": {"demo_file": "alt.dem"}}]) == []


def test_trainingskarte_und_naechster_befehl(tmp_path):
    from datetime import datetime
    cfg_path = tmp_path / "config.yaml"
    sess = tmp_path / "training_sessions"
    sess.mkdir()
    (sess / "2026-11-12T20-31-05_de_mirage.json").write_text(json.dumps(
        {"map": "de_mirage", "started": "2026-11-12T20:31:05", "mode": "granaten",
         "duels": [{"outcome": "win"}] * 8 + [{"outcome": "loss"}],
         "fazit": "Du hast 8 von 9 Duellen gewonnen. Danach kommt noch mehr Text."}))
    now = datetime(2026, 11, 14, 20, 0)
    t = dashboard._training(cfg_path, "Cache", now)
    assert t["last"]["mode"] == "Granaten-Trainer" and t["last"]["result"] == "8/9 Lineups getroffen"
    assert t["last"]["map"] == "Mirage" and t["week"] == 1
    assert t["cmd"] == "!warmup cache"                     # ohne Hausaufgabe: aufwaermen
    (tmp_path / "report_ai").mkdir()
    (tmp_path / "report_ai" / "focus.json").write_text(json.dumps(
        {"map": "de_nuke", "key": "early_util", "label": "Frühe Granate im Full Buy"}))
    t = dashboard._training(cfg_path, "Cache", now)
    assert t["cmd"] == "!granaten nuke" and "Frühe Granate" in t["why"]
