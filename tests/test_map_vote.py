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


def test_never_from_workshop_map(tmp_path):
    s = Server()
    s.status = s.status.replace("de_dust2", "aim_multigame")
    assert "Workshop" in mv.handle({}, tmp_path / "c.yaml", s, "1", "dj", "map", "mirage", now=1)[0]
    assert not any(c.startswith("map ") for c in s.sent)
