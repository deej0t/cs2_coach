from cs2_coach import own_lineups as ol

STATUS = "spawngroup  1 : [1: de_mirage | main lump | mapload]\n"
LAST = "coach_nade_last smoke 100.00 200.00 -168.00 -30.50 45.00 900.00 -300.00 -100.00 0.50 1 0 0 2"


class Server:
    def __init__(self, last=LAST):
        self.last, self.sent = last, []

    def __call__(self, *cmds):
        self.sent += cmds
        if cmds[0] == "status":
            return [STATUS]
        if cmds[0].startswith("coach_nade_last"):
            return [self.last]
        return [""]


def test_parse_last_reads_style_and_side():
    g = ol.parse_last(LAST)
    assert g["type"] == "smoke" and g["side"] == "T" and g["pos"] == [100.0, 200.0, -168.0]
    assert g["style"]["click"] == "both" and g["style"]["jump"] and g["land"] == [900.0, -300.0, -100.0]
    assert ol.parse_last("coach_nade_last none") is None


def test_save_list_delete(tmp_path, monkeypatch):
    import cs2_coach.training_server as ts
    monkeypatch.setattr(ts, "_current_map_from", lambda status: "de_mirage")
    cfg = tmp_path / "c.yaml"
    s = Server()
    assert "Gespeichert: Window" in ol.handle(cfg, s, "dj", "speichern", "Window", "3")[0]
    assert "coach_nade_last 3" in s.sent
    assert ol.load(cfg)["de_mirage"][0]["name"] == "Window"
    assert "Window (Smoke)" in ol.handle(cfg, s, "dj", "lineups", "", "3")[0]
    assert "geloescht" in ol.handle(cfg, s, "dj", "loeschen", "window", "3")[0]
    assert ol.load(cfg)["de_mirage"] == []


def test_nothing_landed_saves_nothing(tmp_path, monkeypatch):
    import cs2_coach.training_server as ts
    monkeypatch.setattr(ts, "_current_map_from", lambda status: "de_mirage")
    cfg = tmp_path / "c.yaml"
    assert "Kein gelandeter Wurf" in ol.handle(cfg, Server("coach_nade_last none"), "dj", "speichern", "X", "3")[0]
    assert ol.load(cfg) == {}
