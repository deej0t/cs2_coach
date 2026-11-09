

def test_overtime_first_half_keeps_second_half_side():
    """Gemessen 2026-11-08 (Anubis 16:14): T-Start, CT in Runde 13-27, T in 28-30."""
    from cs2_coach.parser import _own_side_for_round
    sides = [_own_side_for_round(i, "T") for i in range(36)]
    assert sides[:12] == ["T"] * 12 and sides[12:24] == ["CT"] * 12
    assert sides[24:27] == ["CT"] * 3 and sides[27:30] == ["T"] * 3 and sides[30:33] == ["CT"] * 3
