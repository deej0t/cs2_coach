"""Coach-Engine – generiert direktes, ungeschöntes Feedback."""

from __future__ import annotations

from .parser import MatchResult, PlayerStats


def generate_report(result: MatchResult) -> str:
    s = result.player_stats
    sections = [
        _header(result),
        _kd_section(s),
        _adr_section(s),
        _kast_section(s),
        _opening_duels(s),
        _counter_strafe(s),
        _spray_control(s),
        _weapon_split(s),
        _trade_analysis(s),
        _utility_section(s),
        _flash_section(s),
        _clutch_section(s),
        _multikill_section(s),
        _side_split(s),
        _engagement_distance(s),
        _death_timing_section(s),
        _kill_context(s),
        _rank_section(s),
        _overall_verdict(result),
    ]
    return "\n".join(s for s in sections if s)


def _header(r: MatchResult) -> str:
    date_str = f" — {r.match_date}" if r.match_date else ""
    return (
        f"## Coach-Report\n\n"
        f"**{r.result_str}** auf [[{r.map_name}]] — "
        f"**{r.score_team1}:{r.score_team2}** "
        f"({r.total_rounds} Runden) — "
        f"Rating: **{r.rating}**{date_str}\n"
    )


def _kd_section(s: PlayerStats) -> str:
    kd = s.kd_ratio
    lines = [f"### K/D: {s.kills}/{s.deaths} ({kd:.2f})"]

    if kd < 0.8:
        lines.append(
            "Du stirbst deutlich zu oft. Das ist kein Aim-Problem allein — "
            "das ist Positionierung. Du peekst wie in Quake: wide swing, "
            "Körper komplett exponiert, null Deckung. CS2 bestraft das gnadenlos. "
            "Jeder unnötige Tod kostet dein Team Wirtschaft und Mannvorteil."
        )
    elif kd < 1.0:
        lines.append(
            "Knapp unter 1.0 — du tradest dich quasi selbst. "
            "Überleg bei jedem Peek: Kann mein Team diesen Kill traden, "
            "falls ich sterbe? Wenn nein, peek nicht."
        )
    elif kd < 1.3:
        lines.append(
            "Solides K/D. Du stirbst nicht sinnlos, aber es gibt Luft nach oben. "
            "Fokus auf weniger Overpeeks in Nachrundenszenarien."
        )
    else:
        lines.append(
            "Starkes K/D. Halte das Level — achte darauf, dass du nicht "
            "zu passiv wirst und Runden verschenkst, weil du den Kill-Stat "
            "schützen willst."
        )

    return "\n".join(lines) + "\n"


def _adr_section(s: PlayerStats) -> str:
    adr = s.adr
    lines = [f"### ADR: {adr:.1f}"]

    if adr < 60:
        lines.append(
            "Unter 60 ADR — du bist quasi unsichtbar auf dem Server. "
            "Das bedeutet: Entweder kommst du zu spät in die Fights, "
            "oder du hältst Positionen, die nie kontaktiert werden. "
            "[[Trockener Peek]] und [[Positionierung]] sind deine Baustellen."
        )
    elif adr < 75:
        lines.append(
            "Unterdurchschnittlich. Du nimmst Fights, aber nicht genug davon. "
            "Such dir mehr Kontakt in der Mitte der Runde — nicht nur bei "
            "Retakes oder Clutches."
        )
    elif adr < 90:
        lines.append(
            "Solider ADR-Wert. Du bist konstant in Fights involviert. "
            "Die nächste Stufe: Mehr First-Kontakt-Damage statt Cleanup-Kills."
        )
    else:
        lines.append(
            "Starker ADR. Du dominierst die Fights. "
            "Achte darauf, dass dein Impact auch in wichtigen Runden da ist, "
            "nicht nur in Anti-Ecos."
        )

    return "\n".join(lines) + "\n"


def _opening_duels(s: PlayerStats) -> str:
    total = s.opening_kills + s.opening_deaths
    lines = [
        f"### Opening Duels: {s.opening_kills}W / {s.opening_deaths}L "
        f"({s.opening_duel_rating})"
    ]

    if s.opening_deaths > s.opening_kills and total > 3:
        lines.append(
            "Du verlierst die Mehrzahl der Opening Duels. "
            "Alte Arena-Shooter-Gewohnheit: Du rennst als Erster rein, "
            "ohne Utility, ohne Crosshair-Placement auf Head-Level. "
            "Das ist kein Deathmatch — [[Opening Duels]] in CS2 gewinnt man "
            "mit Vorbereitung, nicht mit Raw-Aim. "
            "Flash rein, Shoulder-Peek für Info, DANN committen."
        )
    elif total > 0 and s.opening_kills >= s.opening_deaths:
        lines.append(
            "Gute Opening-Duel-Bilanz. Du verschaffst deinem Team "
            "regelmäßig den Mannvorteil. Weiter so — aber variiere "
            "deine Peek-Timings, damit du nicht predictable wirst."
        )

    return "\n".join(lines) + "\n"


def _counter_strafe(s: PlayerStats) -> str:
    score = s.counter_strafe_score
    lines = [
        f"### Counter-Strafing: {score:.0f}% stehend geschossen "
        f"(avg Inaccuracy: {s.avg_inaccuracy_move:.4f})"
    ]

    if score < 60:
        lines.append(
            "**ALARM.** Du schießt in über 40% der Fälle in Bewegung. "
            "Das ist DER klassische Arena-Shooter-Fehler in CS2. "
            "In Quake/UT war Movement-Shooting normal — in CS2 ist es "
            "ein Todesurteil. [[Counter-Strafing]] muss in dein Muskelgedächtnis. "
            "Ab auf den Aim-Trainer, nur Counter-Strafe-Drills, jeden Tag 15 Min."
        )
    elif score < 75:
        lines.append(
            "Du schießt noch zu oft in Bewegung. "
            "Bewusst auf [[Counter-Strafing]] achten: "
            "Gegenrichtung antippen, DANN schießen. Kein Spray in Bewegung."
        )
    elif score < 90:
        lines.append(
            "Ordentliches Counter-Strafing. Gelegentlich noch Running-Shots "
            "in Panik-Situationen. Fokus: Auch in [[Clutch-Disziplin]]-Momenten "
            "sauber stoppen."
        )
    else:
        lines.append(
            "Exzellentes Counter-Strafing. Deine Schuss-Disziplin ist stark. "
            "Weiter konsequent halten."
        )

    return "\n".join(lines) + "\n"


def _spray_control(s: PlayerStats) -> str:
    if s.burst_kills + s.spray_kills == 0:
        return ""

    lines = [f"### Spray-Control: {s.burst_spray_ratio} (avg Recoil-Index: {s.avg_recoil_index:.1f})"]

    if s.avg_recoil_index > 5:
        lines.append(
            "Du sprühst zu tief in den Spray. Bei Recoil-Index >5 kontrollierst du "
            "das Pattern kaum noch. [[Spray-Control]]: Burst (2-3 Schüsse), "
            "dann Reset. Nur auf kurze Distanz voll durchsprayen."
        )
    elif s.avg_recoil_index > 3:
        lines.append(
            "Dein Spray geht regelmäßig über die sichere Burst-Zone hinaus. "
            "Bewusst nach 3-4 Bullets stoppen und re-aimen."
        )
    else:
        lines.append(
            "Saubere Burst-Disziplin. Du triffst die Kills "
            "in den ersten Schüssen — exakt richtig."
        )

    return "\n".join(lines) + "\n"


def _weapon_split(s: PlayerStats) -> str:
    lines = [f"### Waffen-Split: {s.awp_rifle_split}"]

    total = s.awp_kills + s.rifle_kills
    if total > 0:
        awp_pct = s.awp_kills / total
        if awp_pct > 0.6:
            lines.append(
                "Du bist sehr [[AWP]]-lastig. Für einen dedizierten AWPer okay — "
                "aber wenn du nicht der Main-AWP bist, klaust du deinem Team "
                "die Wirtschaft. Überprüf, ob deine [[Rifle]]-Mechanik "
                "auf dem gleichen Level ist."
            )
        elif awp_pct < 0.15 and s.awp_kills > 0:
            lines.append(
                "Fast reine [[Rifle]]-Performance. "
                "Solange das deine Rolle ist: perfekt. "
                "Aber scheue die [[AWP]] nicht, wenn die Wirtschaft es erlaubt "
                "und die Situation es verlangt."
            )
        else:
            lines.append(
                "Ausgewogener Mix zwischen [[AWP]] und [[Rifle]]. "
                "Gute Vielseitigkeit."
            )

    if s.pistol_kills > 0:
        lines.append(f"Pistol-Kills: {s.pistol_kills}")

    return "\n".join(lines) + "\n"


def _trade_analysis(s: PlayerStats) -> str:
    lines = [f"### Trade Frags: {s.trade_kills}"]

    trade_ratio = s.trade_kills / max(s.kills, 1)
    if trade_ratio < 0.1 and s.kills > 5:
        lines.append(
            "Kaum Trade Kills. Du spielst zu isoliert — "
            "typisch für Solo-Spieler aus Arena-Shootern. "
            "CS2 ist ein Team-Spiel: Buddy-System, zusammen peeken, "
            "Trades sichern. [[Teamplay]]: Ein getradeter Kill ist besser als "
            "ein Hero-Play, das scheitert."
        )
    elif trade_ratio > 0.2:
        lines.append(
            "Gute Trade-Quote. Du spielst teamorientiert und "
            "sicherst die Kills deiner Mates ab."
        )

    return "\n".join(lines) + "\n"


def _utility_section(s: PlayerStats) -> str:
    total = s.flashes_thrown + s.smokes_thrown + s.he_thrown + s.molotovs_thrown
    upr = s.utility_per_round
    lines = [
        f"### Utility: {total} geworfen ({upr:.1f}/Runde)",
        f"Flashes: {s.flashes_thrown} | Smokes: {s.smokes_thrown} | "
        f"HEs: {s.he_thrown} | Molotovs: {s.molotovs_thrown}",
    ]

    if upr < 1.0:
        lines.append(
            "**Unter 1 Nade pro Runde?** Du spielst wie im Deathmatch. "
            "[[Utility-Usage]]: Jede Runde solltest du mindestens 2 Nades nutzen. "
            "Smokes für Map-Control, Flashes für Peeks, Molotov für Denial. "
            "Geld für Nades > Geld für bessere Pistole."
        )
    elif upr < 2.0:
        lines.append(
            "Unterdurchschnittliche Utility-Nutzung. "
            "Du kaufst wahrscheinlich Nades, wirfst sie aber nicht alle. "
            "Lern 2-3 Standard-Lineups pro Map und wirf sie konsequent."
        )
    elif upr < 3.5:
        lines.append(
            "Solide Utility-Nutzung. Du setzt deine Nades ein — "
            "die nächste Stufe: Timing und Lineups verfeinern."
        )
    else:
        lines.append(
            "Exzellente Utility-Nutzung. Du nutzt dein Equipment konsequent."
        )

    return "\n".join(lines) + "\n"


def _flash_section(s: PlayerStats) -> str:
    if s.flashes_thrown == 0:
        return ""

    lines = [
        f"### Flash-Effektivität: {s.flash_effectiveness}",
        f"Gegner geblindet: {s.flash_enemies_blinded} "
        f"(avg {s.flash_avg_enemy_duration:.1f}s) | "
        f"Team geblindet: {s.flash_teammates_blinded}",
    ]

    if s.flash_teammates_blinded > s.flash_enemies_blinded:
        lines.append(
            "**Du blindest dein eigenes Team öfter als den Gegner.** "
            "Das ist aktiv schädlich. [[Flash-Technik]]: "
            "Pop-Flashes lernen, die über Hindernisse fliegen. "
            "Nie einfach blind um die Ecke werfen."
        )
    elif s.flash_enemies_blinded > 0 and s.flash_avg_enemy_duration < 1.5:
        lines.append(
            "Deine Flashes blenden den Gegner, aber zu kurz. "
            "Versuche Pop-Flashes, die direkt im Sichtfeld detonieren — "
            "nicht Flashes, denen man sich wegdrehen kann."
        )
    elif s.flash_enemies_blinded > 0 and s.flash_avg_enemy_duration >= 2.5:
        lines.append(
            "Starke Flash-Arbeit. Deine Flashes blenden lang und "
            "schaffen echte Peek-Fenster."
        )

    return "\n".join(lines) + "\n"


def _clutch_section(s: PlayerStats) -> str:
    if s.clutch_attempts == 0:
        return ""

    lines = [f"### Clutches: {s.clutch_rate}"]

    if s.clutch_attempts >= 3 and s.clutch_wins == 0:
        lines.append(
            "Mehrere Clutch-Versuche, keiner gewonnen. "
            "[[Clutch-Disziplin]]: Nicht panisch re-peeken. "
            "Zeit managen, Sound nutzen, nur 1v1-Situationen forcen."
        )
    elif s.clutch_wins > 0:
        lines.append(
            f"{s.clutch_wins} Clutch(es) gewonnen mit {s.clutch_kills} Kills. "
            "Du hältst die Nerven in Drucksituationen."
        )

    return "\n".join(lines) + "\n"


def _engagement_distance(s: PlayerStats) -> str:
    if s.total_shots_hit == 0:
        return ""

    lines = [
        f"### Kampfdistanz: avg {s.avg_fight_distance:.0f} units",
        f"Nah (<400): {s.close_range_kills} | "
        f"Mittel (400-900): {s.mid_range_kills} | "
        f"Weit (>900): {s.long_range_kills}",
    ]

    if s.avg_fight_distance < 400 and s.awp_kills > s.rifle_kills:
        lines.append(
            "Du spielst [[AWP]] auf kurze Distanz — das ist Verschwendung. "
            "AWP = lange Sichtlinien halten, nicht Nahkampf."
        )
    elif s.avg_fight_distance > 900 and s.awp_kills == 0:
        lines.append(
            "Du nimmst viele Fernkampf-Duelle mit der [[Rifle]]. "
            "Auf Distanz ist Burst/Tap-Fire Pflicht — kein Full-Spray."
        )

    return "\n".join(lines) + "\n"


def _kill_context(s: PlayerStats) -> str:
    highlights = []
    if s.kills_thru_smoke > 0:
        highlights.append(f"Thru Smoke: {s.kills_thru_smoke}")
    if s.kills_while_blind > 0:
        highlights.append(f"While Blind: {s.kills_while_blind}")
    if s.kills_noscope > 0:
        highlights.append(f"No-Scope: {s.kills_noscope}")
    if s.kills_penetrated > 0:
        highlights.append(f"Wallbang: {s.kills_penetrated}")
    if s.kills_airborne > 0:
        highlights.append(f"Airborne: {s.kills_airborne}")

    if not highlights:
        return ""

    return f"### Kill-Highlights: {' | '.join(highlights)}\n"


def _rank_section(s: PlayerStats) -> str:
    if s.rank_old == 0 and s.rank_new == 0:
        return ""

    change_str = f"+{s.rank_change:.0f}" if s.rank_change >= 0 else f"{s.rank_change:.0f}"
    lines = [
        f"### Rank: {s.rank_old} -> {s.rank_new} ({change_str}) "
        f"| {s.rank_wins} Siege total"
    ]
    return "\n".join(lines) + "\n"


def _kast_section(s: PlayerStats) -> str:
    lines = [
        f"### KAST%: {s.kast_pct:.0f}% | "
        f"Survival: {s.survival_rate:.0f}% | "
        f"Accuracy: {s.accuracy:.1f}%"
    ]

    if s.kast_pct < 55:
        lines.append(
            "Unter 55% KAST — in fast jeder zweiten Runde hattest du "
            "weder Kill, Assist, Survival noch Trade. Du bist ein Geist. "
            "Entweder stirbst du früh ohne Impact, oder du kommst zu spät."
        )
    elif s.kast_pct < 70:
        lines.append(
            "Unterdurchschnittliche KAST%. Du hast in zu vielen Runden "
            "keinen messbaren Beitrag. Ziel: In jeder Runde mindestens "
            "ein Kill, Assist, oder überleben."
        )
    elif s.kast_pct >= 80:
        lines.append(
            "Starke KAST%. Du bist in fast jeder Runde relevant — "
            "das ist Konstanz."
        )

    if s.survival_rate < 20:
        lines.append(
            f"Survival-Rate nur {s.survival_rate:.0f}% — du stirbst fast jede Runde. "
            "Frage dich: Musste ich da peeken? Hätte ich die Runde überleben können?"
        )

    return "\n".join(lines) + "\n"


def _multikill_section(s: PlayerStats) -> str:
    if s.rounds_2k + s.rounds_3k + s.rounds_4k + s.rounds_5k == 0:
        return ""
    lines = [f"### Multi-Kills: {s.multikill_str}"]
    total_mk = s.rounds_2k + s.rounds_3k + s.rounds_4k + s.rounds_5k
    if total_mk >= 3:
        lines.append(
            f"{total_mk} Multi-Kill-Runden. Du kannst die Runde "
            "im Alleingang drehen, wenn du in Form bist."
        )
    return "\n".join(lines) + "\n"


def _side_split(s: PlayerStats) -> str:
    lines = [f"### CT/T-Split: {s.ct_t_split}"]

    ct_kd = s.ct_kills / max(s.ct_deaths, 1)
    t_kd = s.t_kills / max(s.t_deaths, 1)
    diff = abs(ct_kd - t_kd)

    if diff > 0.5:
        weak = "CT" if ct_kd < t_kd else "T"
        lines.append(
            f"Deutlicher Unterschied zwischen den Seiten. "
            f"Deine {weak}-Side ist signifikant schwächer. "
            f"{'Auf CT: Halte Winkel, peek nicht unnötig.' if weak == 'CT' else 'Auf T: Utility nutzen, nicht dry peeken.'}"
        )
    else:
        lines.append("Ausgeglichene Performance auf beiden Seiten.")

    return "\n".join(lines) + "\n"


def _death_timing_section(s: PlayerStats) -> str:
    total = s.deaths_early + s.deaths_mid + s.deaths_late
    if total == 0:
        return ""

    lines = [f"### Death-Timing: {s.death_timing_str}"]

    if s.deaths_early > total * 0.4:
        lines.append(
            "Du stirbst auffällig oft früh in der Runde. "
            "Das heißt: Du pushst ohne Info, peekst aggressiv ohne Backup, "
            "oder deine [[Positionierung]] ist zu exposed. "
            "Frühe Tode = dein Team spielt 4v5 für den Rest der Runde."
        )
    elif s.deaths_late > total * 0.5:
        lines.append(
            "Die meisten Tode kommen spät — das ist okay, wenn du Impact hattest. "
            "Aber wenn du spät stirbst OHNE Kills: Du warst zu passiv und hast "
            "die Runde verschenkt."
        )

    return "\n".join(lines) + "\n"


def _overall_verdict(r: MatchResult) -> str:
    s = r.player_stats
    rating = r.rating
    lines = ["### Gesamtbewertung\n"]

    weaknesses = []
    if s.counter_strafe_score < 70:
        weaknesses.append("[[Counter-Strafing]]")
    if s.opening_deaths > s.opening_kills:
        weaknesses.append("[[Opening Duels]]")
    if s.adr < 70:
        weaknesses.append("[[Positionierung]]")
    if s.trade_kills / max(s.kills, 1) < 0.1:
        weaknesses.append("[[Teamplay]]")
    if s.kd_ratio < 0.9:
        weaknesses.append("[[Clutch-Disziplin]]")
    if s.utility_per_round < 1.5:
        weaknesses.append("[[Utility-Usage]]")
    if s.flash_teammates_blinded > s.flash_enemies_blinded and s.flashes_thrown > 3:
        weaknesses.append("[[Flash-Technik]]")
    if s.avg_recoil_index > 5:
        weaknesses.append("[[Spray-Control]]")

    if rating >= 1.2:
        lines.append(
            f"Rating **{rating}** — Starke Performance. "
            "Aber ein gutes Match macht noch keine Konstanz. "
            "Wiederhole das über 10 Spiele, dann reden wir."
        )
    elif rating >= 0.9:
        lines.append(
            f"Rating **{rating}** — Durchschnitt. Reicht zum Mithalten, "
            "nicht zum Tragen. Du musst nicht der Star sein, aber du musst "
            "Impact in den entscheidenden Runden haben."
        )
    else:
        lines.append(
            f"Rating **{rating}** — Unter dem Durchschnitt. Klartext: "
            "Du warst eine Belastung für dein Team in diesem Match. "
            "Das ist kein Flame, sondern ein Weckruf. "
            "Die gute Nachricht: Jeder dieser Punkte ist trainierbar."
        )

    if weaknesses:
        lines.append(
            f"\n**Fokus-Bereiche für das nächste Training:** "
            f"{', '.join(weaknesses)}"
        )

    return "\n".join(lines)
