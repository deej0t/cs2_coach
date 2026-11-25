"""Uebungsmodus: Cvars sichern/setzen/wiederherstellen, Rechte, Tasten-Anzeige, Mapwechsel."""

from cs2_coach import practice_mode as pm

STATUS_ONE = "---------players--------\n  id     time ping loss      state   rate adr name\n   0    03:29    0    0     active 786432 1.2.3.4:5 'dj'\n#end\n"
STATUS_TWO = STATUS_ONE.replace("#end", "   3    05:46   21    0     active 786432 5.6.7.8:9 'Dancho'\n#end")


class Server:
    """Antwortet auf Cvar-Abfragen wie CS2 ("name = wert"), merkt sich gesetzte Werte."""

    def __init__(self, status=STATUS_ONE):
        self.status = status
        self.values = {k: "0" for k in pm.SETUP}
        self.values.update(mp_roundtime="1.92", mp_buytime="20", sv_cheats="false")
        self.cmds = []

    def __call__(self, *cmds):
        out = []
        for c in cmds:
            self.cmds.append(c)
            parts = c.split()
            if c == "status":
                out.append(self.status)
            elif len(parts) == 1 and parts[0] in self.values:
                out.append(f"{parts[0]} = {self.values[parts[0]]}\n")
            else:
                if len(parts) == 2 and parts[0] in self.values:
                    self.values[parts[0]] = parts[1]
                out.append("")
        return out


def test_an_und_aus_stellt_vorherige_werte_her(tmp_path):
    cp = tmp_path / "config.yaml"
    srv = Server()
    r = pm.handle({}, cp, srv, "76561", "dj", "uebung", "", uid=0)
    assert r[0].startswith("Uebungsmodus an")
    assert srv.values["sv_cheats"] == "1" and srv.values["mp_roundtime"] == "60"
    assert "coach_practice 1" in srv.cmds and "mp_restartgame 1" in srv.cmds
    assert pm.handle({}, cp, srv, "76561", "dj", "uebung", "", uid=0)[0].startswith("Uebungsmodus laeuft schon")
    srv.cmds.clear()
    r = pm.handle({}, cp, srv, "76561", "dj", "uebung", "aus", uid=0)
    assert "wiederhergestellt" in r[0]
    assert srv.values["sv_cheats"] == "0"  # "false" -> 0
    assert srv.values["mp_roundtime"] == "1.92" and srv.values["mp_buytime"] == "20"
    assert "coach_practice 0" in srv.cmds


def test_mit_anderen_nur_besitzer(tmp_path):
    cp = tmp_path / "config.yaml"
    srv = Server(STATUS_TWO)
    r = pm.handle({}, cp, srv, "76561", "dj", "uebung", "", uid=0)
    assert "nur der Besitzer" in r[0] and srv.values["sv_cheats"] == "false"
    r = pm.handle({"steam_id": "76561"}, cp, srv, "76561", "dj", "uebung", "", uid=0)
    assert r[0].startswith("Uebungsmodus an")


def test_tasten_umschalten(tmp_path):
    cp = tmp_path / "config.yaml"
    srv = Server()
    pm.handle({}, cp, srv, "a", "dj", "tasten", "", uid=4)
    pm.handle({}, cp, srv, "a", "dj", "tasten", "", uid=4)
    pm.handle({}, cp, srv, "a", "dj", "tasten", "an", uid=4)
    assert srv.cmds == ["coach_keys 4 1", "coach_keys 4 0", "coach_keys 4 1"]


def test_mapwechsel_vergisst_modus(tmp_path):
    cp = tmp_path / "config.yaml"
    pm.handle({}, cp, Server(), "a", "dj", "uebung", "", uid=0)
    pm.on_lines(cp, ['11/21/2026 - 21:00:00.000 - Loading map "de_mirage"'])
    assert not pm.load(cp)["on"]


def test_rethrow_und_zurueck(tmp_path):
    cp = tmp_path / "config.yaml"
    srv = Server()
    assert pm.handle({}, cp, srv, "76561", "dj", "rethrow", "", uid=0)[0].startswith("Geht nur")
    pm.handle({}, cp, srv, "76561", "dj", "uebung", "", uid=0)
    assert pm.handle({}, cp, srv, "76561", "dj", "rethrow", "", uid=0) == []
    assert srv.cmds[-2:] == ["coach_rethrow 0", "sv_rethrow_last_grenade"]  # ohne Plugin: RCON

    def plugin(*cmds):
        srv.cmds.extend(cmds)
        return ["coach_rethrow ok 0"]
    pm.handle({}, cp, plugin, "76561", "dj", "nochmal", "", uid=0)
    assert srv.cmds[-1] == "coach_rethrow 0"

    def last(*cmds):
        srv.cmds.extend(cmds)
        if cmds[0].startswith("coach_nade_last"):
            return ["coach_nade_last smoke 100.00 200.00 64.00 -40.50 90.00 900.00 800.00 0.00 1.00 1 0 0 2\n"]
        return [""]
    assert pm.handle({}, cp, last, "76561", "dj", "zurueck", "", uid=7) == []
    assert srv.cmds[-1] == "coach_tp 7 100.0 200.0 64.0 -40.5 90.0"
    none = lambda *c: ["coach_nade_last none"]
    assert "Noch kein" in pm.handle({}, cp, none, "76561", "dj", "zurueck", "", uid=7)[0]


def test_impacts_umschalten_und_wiederherstellen(tmp_path):
    cp = tmp_path / "config.yaml"
    srv = Server()
    pm.handle({}, cp, srv, "76561", "dj", "uebung", "", uid=0)
    assert srv.values["sv_showimpacts"] == "1"
    assert pm.handle({}, cp, srv, "76561", "dj", "impacts", "", uid=0) == ["Einschlaege aus."]
    assert pm.handle({}, cp, srv, "76561", "dj", "impacts", "", uid=0) == ["Einschlaege an."]
    pm.handle({}, cp, srv, "76561", "dj", "uebung", "aus", uid=0)
    assert srv.values["sv_showimpacts"] == "0"


def test_clear_raeumt_ueber_plugin_weg(tmp_path):
    cp = tmp_path / "config.yaml"
    srv = Server()
    assert pm.handle({}, cp, srv, "76561", "dj", "clear", "", uid=0)[0].startswith("Geht nur")
    pm.handle({}, cp, srv, "76561", "dj", "uebung", "", uid=0)
    assert "1.31.0" in pm.handle({}, cp, srv, "76561", "dj", "weg", "", uid=0)[0]  # altes Plugin

    def plugin(*cmds):
        srv.cmds.extend(cmds)
        return ["coach_clear ok 3"]
    assert pm.handle({}, cp, plugin, "76561", "dj", "clear", "", uid=0) == ["Weggeraeumt: 3 Granaten/Smokes/Feuer."]
    assert srv.cmds[-1] == "coach_clear"


def test_uebung_sagt_wenn_plugin_fehlt(tmp_path):
    cp = tmp_path / "config.yaml"
    out = pm.handle({}, cp, Server(), "76561", "dj", "uebung", "", uid=0)
    assert "nicht geladen" in out[0] and "Ping oder !clear" not in out[0]
