

def test_verdict_compares_with_lobby_not_fixed_threshold():
    """0.82 hiess frueher immer 'Belastung fuer dein Team' - auch als Bester des Teams."""
    from cs2_coach.coach import _lobby_verdict
    from cs2_coach.parser import MatchResult, PlayerStats

    def p(sid, team, kills):
        return PlayerStats(name=sid, steam_id=sid, team=team, kills=kills, deaths=20, rounds_played=24)
    me = p("me", "T", 18)
    lobby = [me] + [p(f"t{i}", "T", 10) for i in range(4)] + [p(f"c{i}", "CT", 25 - i) for i in range(5)]
    r = MatchResult(map_name="de_mirage", score_team1=13, score_team2=11, player_stats=me, all_players=lobby)
    text = _lobby_verdict(r, r.rating)
    assert "Platz 1 von 5 in deinem Team" in text and "stärkste" in text and "Belastung" not in text
