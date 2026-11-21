"""aim_fun: Nemesis, Kopfgeld, Clutch, Zwischenstand, Spielende, Ausgleich, Waffen-Abstimmung."""

import base64

from cs2_coach import aim_fun

T0 = "11/21/2026 - 19:32:"
P = {"dj": '"dj<0><[U:1:58996800]><{t}>"', "da": '"Dancho<1><[U:1:923059335]><{t}>"',
     "x": '"Xaver<2><[U:1:111]><{t}>"', "y": '"Yvo<3><[U:1:222]><{t}>"'}
SIDE = {"dj": "TERRORIST", "x": "TERRORIST", "da": "CT", "y": "CT"}


def _p(k):
    return P[k].format(t=SIDE[k])


def kill(a, v, sec=10, hs=False):
    return (f"{T0}{sec:02d}.000 - {_p(a)} [0 0 0] killed {_p(v)} [0 0 0] with \"ak47\""
            + (" (headshot)" if hs else ""))


def round_start(sec=0):
    return f'{T0}{sec:02d}.000 - World triggered "Round_Start"'


def round_end(winner, t, ct, sec=30):
    team = "TERRORIST" if winner == "T" else "CT"
    return f'{T0}{sec:02d}.000 - Team "{team}" triggered "SFUI_Notice_X_Win" (CT "{ct}") (T "{t}")'


def start(*who):
    lines = [f'{T0}00.000 - World triggered "Match_Start" on "aim_multigame"']
    for k in who:
        name, uid, sid = P[k].strip('"').split("<")[:3]
        lines.append(f'{T0}00.000 - "{name}<{uid}<{sid}" switched from team <Unassigned> to <{SIDE[k]}>')
    return lines


def texts(cmds):
    return [base64.b64decode(c.split()[2]).decode() for c in cmds if c.startswith("coach_chat")]


class Rec:
    def __init__(self):
        self.cmds = []

    def __call__(self, *c):
        self.cmds += c
        return [""] * len(c)


def run(tmp_path, lines, cfg=None):
    rec = Rec()
    st = aim_fun.on_lines(cfg or {}, tmp_path / "config.yaml", lines, rec)
    return st, rec.cmds


def test_nemesis_kopfgeld_rache(tmp_path):
    lines = start("dj", "da")
    for i in range(5):
        lines += [round_start(), kill("dj", "da", hs=i < 3), round_end("T", i + 1, 0)]
    lines += [round_start(), kill("da", "dj"), round_end("CT", 5, 1)]
    st, cmds = run(tmp_path, lines)
    t = texts(cmds)
    assert any("NEMESIS: dj hat Dancho 4x" in x for x in t)
    assert not any("3x" in x for x in t)
    assert any("KOPFGELD! Dancho beendet die 5er-Serie von dj" in x for x in t)
    assert not any("RACHE" in x for x in t)  # dasselbe wie das Kopfgeld
    assert any(x.startswith("Zwischenstand nach 5 Runden: dj 5:0 (60 % HS)") for x in t)
    assert st["game"]["[U:1:58996800]"]["best"] == 5
    # jede Ansage geht an beide Menschen
    assert sum("KOPFGELD" in x for x in t) == 2


def test_clutch_nur_bei_rundensieg(tmp_path):
    lines = start("dj", "x", "da", "y") + [round_start(), kill("da", "x"), kill("dj", "da"), kill("dj", "y"),
                                             round_end("T", 1, 0)]
    _, cmds = run(tmp_path, lines)
    assert any("CLUTCH! dj gewinnt das 1v2" in x for x in texts(cmds))
    lines = [round_start(), kill("da", "x"), kill("y", "dj"), round_end("CT", 1, 1)]
    _, cmds = run(tmp_path, lines)
    assert not any("CLUTCH" in x for x in texts(cmds))


def test_ausgleich_2v1(tmp_path):
    lines = start("dj", "x", "da")
    for i in range(5):
        lines += [round_start(), kill("dj", "da"), round_end("T", i + 1, 0)]
    st, cmds = run(tmp_path, lines)
    assert "coach_team 0 ct" in cmds  # dj (stark, fuehrendes Duo) wechselt zu Dancho
    assert st["roster"]["[U:1:58996800]"]["team"] == "CT"
    assert st["since"] == {"T": 0, "CT": 0}


def test_ausgleich_nicht_bei_1v1_und_abschaltbar(tmp_path):
    lines = start("dj", "da")
    for i in range(6):
        lines += [round_start(), kill("dj", "da"), round_end("T", i + 1, 0)]
    _, cmds = run(tmp_path, lines)
    assert not any(c.startswith("coach_team") for c in cmds)
    _, cmds = run(tmp_path, start("dj", "x", "da") + [round_start(), kill("dj", "da"), round_end("T", 1, 0)] * 5,
                  cfg={"aim_fun_balance": False})
    assert not any(c.startswith("coach_team") for c in cmds)


def test_spielende_rangliste(tmp_path):
    lines = start("dj", "da")
    for i in range(5):
        lines += [round_start(0), kill("dj", "da", sec=3 + i, hs=True), round_end("T", i + 1, 0)]
    lines += [f"{T0}59.000 - Game Over: competitive mg_active aim_multigame score 5:0 after 3 min"]
    _, cmds = run(tmp_path, lines)
    t = texts(cmds)
    assert any("Laengste Serie: dj (5)" in x and "Schnellster Kill: dj (3,0 s)" in x
               and "Beste HS-Quote: dj (100 %)" in x for x in t)
    assert "Laengste Serie: dj (5)" in aim_fun.last_game_lines(tmp_path / "config.yaml")


def test_andere_map_still(tmp_path):
    lines = ['11/21/2026 - 19:32:00.000 - World triggered "Match_Start" on "de_mirage"'] + [kill("dj", "da")] * 6
    _, cmds = run(tmp_path, lines)
    assert cmds == []


def test_waffen_abstimmung(tmp_path):
    cp = tmp_path / "config.yaml"
    run(tmp_path, start("dj", "x", "da"))
    rec = Rec()
    r = aim_fun.handle({}, cp, rec, "a", "dj", "waffe", "deagle", now=100)
    assert "1/2" in r[0] and rec.cmds == []
    r = aim_fun.handle({}, cp, rec, "b", "Dancho", "waffe", "deagle", now=110)
    assert "Mehrheit" in r[0] and rec.cmds == ["coach_weapon_mode weapon_deagle"]
    _, cmds = run(tmp_path, [round_start()])  # sofort, ohne Verzoegerung
    assert cmds == ["coach_weapon_mode weapon_deagle"]
    assert aim_fun.handle({}, cp, rec, "a", "dj", "waffe", "bazooka", now=120)[0].startswith("Unbekannte")
    aim_fun.handle({}, cp, rec, "a", "dj", "waffe", "messer", now=130)
    aim_fun.handle({}, cp, rec, "b", "Dancho", "waffe", "messer", now=131)
    assert rec.cmds[-1] == "coach_weapon_mode knife"
    # Mapwechsel weg von aim: Plugin aus
    _, cmds = run(tmp_path, ['11/21/2026 - 19:40:00.000 - World triggered "Match_Start" on "de_mirage"'])
    assert cmds == ["coach_weapon_mode off"]
    assert "weapon" not in aim_fun.load(cp)


def test_befehl_auf_anderer_map(tmp_path):
    r = aim_fun.handle({}, tmp_path / "config.yaml", None, "a", "dj", "waffe", "ak")
    assert r[0].startswith("Nur auf aim_multigame")


def test_rache_ohne_kopfgeld(tmp_path):
    # Dancho erwischt dj 4x, stirbt aber jede Runde an Xaver - keine Serie, also Rache statt Kopfgeld
    lines = start("dj", "x", "da", "y")
    for _ in range(4):
        lines += [round_start(), kill("da", "dj"), kill("x", "da")]
    lines += [round_start(), kill("dj", "da")]
    _, cmds = run(tmp_path, lines)
    t = texts(cmds)
    assert any("NEMESIS: Dancho hat dj 4x" in x for x in t)
    assert any("RACHE! dj erwischt endlich Dancho - nach 4" in x for x in t)
    assert not any("KOPFGELD" in x for x in t)
