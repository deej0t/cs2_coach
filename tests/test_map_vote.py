"""Mapwechsel per Abstimmung: allein sofort, zu zweit braucht es !ja."""

from cs2_coach import map_vote as mv

STATUS = """loaded spawngroup(  1)  : SV:  [1: de_dust2 | main lump | mapload]
---------players--------
  id     time ping loss      state   rate adr name
   2    09:33    0    0     active 786432 192.168.188.75:59684 'dj'
{extra}#end"""


class Server:
    def __init__(self, extra=""):
        self.sent, self.status = [], STATUS.format(extra=extra)

    def __call__(self, *cmds):
        self.sent += cmds
        return [self.status if c == "status" else "" for c in cmds]


def test_alone_changes_immediately(tmp_path):
    s = Server()
    assert mv.handle({}, tmp_path / "c.yaml", s, "1", "dj", "map", "mirage", now=100) == []
    assert "map de_mirage" in s.sent


def test_two_players_need_yes(tmp_path):
    s = Server("   3    09:25   19    0     active 786432 1.2.3.4:5 'Dancho'\n")
    reply = mv.handle({}, tmp_path / "c.yaml", s, "1", "dj", "map", "nuke", now=100)
    assert "!ja" in reply[0] and "(1/2" in reply[0] and "map de_nuke" not in s.sent
    assert mv.handle({}, tmp_path / "c.yaml", s, "2", "Dancho", "ja", "", now=110) == []
    assert "map de_nuke" in s.sent


def test_vote_expires_and_pool_only(tmp_path):
    s = Server("   3    09:25   19    0     active 786432 1.2.3.4:5 'Dancho'\n")
    mv.handle({}, tmp_path / "c.yaml", s, "1", "dj", "map", "nuke", now=100)
    assert "keine Abstimmung" in mv.handle({}, tmp_path / "c.yaml", s, "2", "D", "ja", "", now=200)[0]
    assert "gibt es hier nicht" in mv.handle({}, tmp_path / "c.yaml", s, "1", "dj", "map", "aim_x", now=300)[0]


def test_aim_map_is_votable_via_workshop(tmp_path):
    s = Server()
    assert mv.handle({}, tmp_path / "c.yaml", s, "1", "dj", "map", "aim", now=1) == []
    assert "host_workshop_map 3245740485" in s.sent


def test_leaving_workshop_map_disconnects_players_first(tmp_path):
    s = Server()
    s.status = s.status.replace("de_dust2", "aim_multigame")
    mv.handle({"server_address": "cs2.deej0t.eu"}, tmp_path / "c.yaml", s, "1", "dj", "map", "mirage", now=1)
    kick = next(i for i, c in enumerate(s.sent) if c.startswith("kickid 2 "))
    assert "retry" in s.sent[kick] and "connect cs2.deej0t.eu" in s.sent[kick] and s.sent.index("map de_mirage") > kick


def test_workshop_command_adds_map_and_loads_it(tmp_path):
    from cs2_coach import workshop

    class R:
        def json(self):
            return {"response": {"publishedfiledetails": [{"title": "Prefire Mirage 2026", "consumer_app_id": 730}]}}

    s = Server()
    cfg_path = tmp_path / "c.yaml"
    out = workshop.handle({"steam_id": "1"}, cfg_path, s, "1", "dj",
                          "https://steamcommunity.com/sharedfiles/filedetails/?id=3070244462", post=lambda *a, **k: R())
    assert out == [] and "host_workshop_map 3070244462" in s.sent
    assert workshop.load(cfg_path) == {"prefire_mirage_2026": "3070244462"}
    # danach per Abstimmung erreichbar
    s2 = Server()
    mv.handle({}, cfg_path, s2, "1", "dj", "map", "prefire_mirage_2026", now=1)
    assert "host_workshop_map 3070244462" in s2.sent
    assert "nur der Server-Besitzer" in workshop.handle({"steam_id": "1"}, cfg_path, s, "2", "x", "123456")[0]


def test_map_change_resets_sv_cheats():
    """aim_botz schaltet sv_cheats selbst ein; CS2 setzt es beim Mapwechsel nicht zurueck (09.11.)."""
    from cs2_coach.training_server import map_change_commands
    cmds = map_change_commands("", "de_mirage", {})
    assert cmds.index("sv_cheats 0") < cmds.index("map de_mirage")
