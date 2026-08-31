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
        _crosshair_placement(s),
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
        _duel_analysis(result),
        _round_patterns(result),
        _economy_section(result),
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


def _crosshair_placement(s: PlayerStats) -> str:
    if s.crosshair_placement_kills == 0:
        return ""

    avg = s.crosshair_placement_median
    lines = [
        f"### Crosshair Placement: {avg:.1f}° avg ({s.crosshair_placement_rating})",
        f"Verteilung: {s.crosshair_placement_str}",
    ]

    if avg > 25:
        lines.append(
            "**Dein Crosshair zeigt fast nie dahin, wo der Gegner auftaucht.** "
            "Das bedeutet massive Nachkorrekturen bei jedem Fight — "
            "du gibst dem Gegner einen Zeitvorteil, bevor du überhaupt schießen kannst. "
            "[[Crosshair Placement]]: Fadenkreuz IMMER auf Kopfhöhe, "
            "IMMER auf den nächsten erwarteten Gegner-Angle gerichtet. "
            "Nicht auf den Boden schauen, nicht in die Luft — Head-Level, Pre-Aim."
        )
    elif avg > 15:
        lines.append(
            "Dein Fadenkreuz steht regelmäßig zu weit vom Gegner entfernt. "
            "Du musst vor jedem Kill deutlich nachjustieren — das kostet Zeit. "
            "[[Crosshair Placement]]: Beim Bewegen durch die Map das Crosshair "
            "immer auf die nächste gefährliche Ecke richten, auf Kopfhöhe. "
            "Üb das bewusst auf leeren Servern: Durch die Map laufen, "
            "jede Ecke pre-aimen."
        )
    elif avg > 8:
        lines.append(
            "Solide Crosshair Placement. Dein Fadenkreuz ist meistens "
            "in der Nähe des Gegners, aber noch nicht tight genug für sofortige "
            "Head-Level-Kills. Feinschliff: Tighter an den Angle kleben, "
            "weniger 'offene' Crosshair-Position in der Map-Mitte."
        )
    elif avg > 4:
        lines.append(
            "Gute Crosshair Placement. Du pre-aimst die meisten Positionen sauber. "
            "Die kleinen Korrekturen, die du noch brauchst, sind minimal — "
            "das gibt dir einen echten Zeitvorteil in Duellen."
        )
    else:
        lines.append(
            "Exzellente Crosshair Placement. Dein Fadenkreuz sitzt fast immer "
            "genau da, wo der Gegner auftaucht. Minimale Mausbewegung nötig — "
            "das ist Pro-Level Pre-Aim."
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
    if s.burst_hits + s.spray_hits == 0:
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


def _duel_analysis(r: MatchResult) -> str:
    duels = r.duel_matrix
    if not duels:
        return ""

    lines = ["### Gegner-Duelle"]

    # Find nemesis (most deaths to) and victim (most kills against)
    nemesis = max(duels, key=lambda d: d["deaths"]) if duels else None
    victim = max(duels, key=lambda d: d["kills"]) if duels else None

    if nemesis and nemesis["deaths"] >= 3:
        lines.append(
            f"**Nemesis: {nemesis['name']}** — {nemesis['deaths']}x gestorben, "
            f"nur {nemesis['kills']}x gekillt. "
            f"Waffen des Gegners gegen dich: hauptsächlich {', '.join(nemesis['top_weapons'][:2]) if nemesis['top_weapons'] else '?'}. "
        )
        if nemesis["hs_deaths"] >= 2:
            lines.append(
                f"Er trifft dich {nemesis['hs_deaths']}x am Kopf — dein Pre-Aim "
                "auf seine Position war schlecht, oder du peekst ihn immer "
                "auf die gleiche Weise. **Tipp:** Variiere deine Peek-Position "
                "und nutze Off-Angles. Wer predictable peekt, stirbt."
            )
        else:
            lines.append(
                "**Tipp:** Vermeide direkte Duelle mit Spielern, die dich dominieren. "
                "Nutze Utility (Flash, Smoke) um den Fight zu deinen Gunsten "
                "zu drehen, oder rotiere und such den Kill woanders."
            )

    if victim and victim["kills"] >= 4 and (not nemesis or victim["name"] != nemesis["name"]):
        lines.append(
            f"**Dominiert: {victim['name']}** — {victim['kills']}x gekillt "
            f"({victim['hs_kills']} Headshots). Du liest seine Positionen gut."
        )

    # Check for even matchups
    even_fights = [d for d in duels if d["total_duels"] >= 4 and 0.8 <= d["kd"] <= 1.25]
    if even_fights:
        names = ", ".join(d["name"] for d in even_fights[:2])
        lines.append(
            f"Enge Duelle mit: {names}. "
            "Diese 50/50-Fights entscheiden Runden — gewinne sie durch "
            "bessere Vorbereitung: Flash vor dem Peek, Shoulder-Peek für Info, "
            "dann committed peeken."
        )

    return "\n".join(lines) + "\n"


def _round_patterns(r: MatchResult) -> str:
    tl = r.round_timeline
    if not tl or len(tl) < 5:
        return ""

    lines = ["### Runden-Muster"]

    # Early deaths
    early_deaths = [rd for rd in tl if rd["died_early"]]
    if len(early_deaths) >= 3:
        rounds_str = ", ".join(f"R{rd['round']}" for rd in early_deaths[:5])
        lines.append(
            f"**{len(early_deaths)} fruehe Tode** (Runden: {rounds_str}). "
            "Du stirbst im ersten Drittel der Runde — noch bevor dein Team "
            "sich aufgestellt hat. Das ist das schaedlichste Muster in CS2: "
            "Dein Team spielt 4v5 ohne Info.\n"
            "**Training:** Spiel 5 Matches bewusst passiv in den ersten "
            "10 Sekunden jeder Runde. Kein Peek, kein Push. Nur Info sammeln "
            "und den Angle halten. Du wirst ueberrascht sein, wie viel besser "
            "du tradeable bist."
        )
    elif len(early_deaths) >= 1:
        rounds_str = ", ".join(f"R{rd['round']}" for rd in early_deaths)
        lines.append(
            f"Fruehe Tode in {rounds_str}. Nicht kritisch, aber pruefe: "
            "War der aggressive Peek noetig, oder haettest du abwarten koennen?"
        )

    # Zero-impact rounds (died without any kills)
    zero_impact = [rd for rd in tl if rd["player_died"] and rd["player_kills"] == 0]
    if len(zero_impact) >= 4:
        lines.append(
            f"**{len(zero_impact)} Runden ohne Impact** — gestorben, "
            "ohne einen Kill zu holen. Das sind verschenkte Runden.\n"
            "**Training:** Bevor du einen Fight nimmst, frag dich: "
            "Habe ich einen Vorteil? (Utility, Info, besserer Angle?) "
            "Wenn nein, warte auf einen besseren Moment oder repositioniere dich."
        )

    # Consecutive losses with deaths
    max_loss_streak = 0
    current_streak = 0
    for rd in tl:
        if not rd["won"] and rd["player_died"]:
            current_streak += 1
            max_loss_streak = max(max_loss_streak, current_streak)
        else:
            current_streak = 0

    if max_loss_streak >= 4:
        lines.append(
            f"**{max_loss_streak} Runden in Folge verloren + gestorben.** "
            "Das deutet auf Tilt hin — nach mehreren Verlusten werden Peeks "
            "aggressiver und unkontrollierter.\n"
            "**Tipp:** Nach 2 verlorenen Runden in Folge: Bewusst zuruecklehnen. "
            "Passiver spielen, Angle halten, das Team machen lassen. "
            "Tilt-Management ist eine Faehigkeit — trainiere sie bewusst."
        )

    # Multi-kill rounds
    multi_kill_rounds = [rd for rd in tl if rd["player_kills"] >= 3]
    if len(multi_kill_rounds) >= 2:
        rounds_str = ", ".join(f"R{rd['round']}({rd['player_kills']}K)" for rd in multi_kill_rounds)
        lines.append(
            f"Multi-Kill-Runden: {rounds_str}. "
            "In diesen Runden hast du dominiert — analysiere WAS du anders "
            "gemacht hast (Position, Timing, Utility) und wiederhole es."
        )

    # First half vs second half performance
    half = len(tl) // 2
    if half >= 4:
        first_half_kills = sum(rd["player_kills"] for rd in tl[:half])
        second_half_kills = sum(rd["player_kills"] for rd in tl[half:])
        first_rounds = half
        second_rounds = len(tl) - half

        first_avg = first_half_kills / max(first_rounds, 1)
        second_avg = second_half_kills / max(second_rounds, 1)

        if first_avg > 0 and second_avg / max(first_avg, 0.1) < 0.5:
            lines.append(
                f"**Performance-Einbruch:** {first_half_kills} Kills in der "
                f"ersten Haelfte vs. {second_half_kills} in der zweiten. "
                "Du faellst nach dem Seitenwechsel ab.\n"
                "**Tipp:** Nach dem Seitenwechsel aktiv den Reset machen: "
                "Neuer Fokus, neue Strategie. Nicht die gleichen Plays wiederholen, "
                "die in der ersten Haelfte funktioniert haben — der Gegner passt sich an."
            )
        elif second_avg > 0 and first_avg / max(second_avg, 0.1) < 0.5:
            lines.append(
                f"**Kaltstart:** Nur {first_half_kills} Kills in der "
                f"ersten Haelfte, aber {second_half_kills} in der zweiten. "
                "Du brauchst zu lang, um ins Spiel zu finden.\n"
                "**Tipp:** Vor dem Match 10 Min. Deathmatch/Aim-Trainer, "
                "um warmzuwerden. Wer kalt ins Match geht, verschenkt die "
                "ersten Runden."
            )

    if len(lines) <= 1:
        return ""

    return "\n".join(lines) + "\n"


def _economy_section(r: MatchResult) -> str:
    econ = r.economy_performance
    if not econ:
        return ""

    fullbuy = econ.get("fullbuy", {})
    eco = econ.get("eco", {})
    force = econ.get("force", {})
    pistol = econ.get("pistol", {})

    if not fullbuy:
        return ""

    lines = ["### Wirtschafts-Performance"]

    fb_kd = fullbuy.get("kd", 0)
    fb_adr = fullbuy.get("adr", 0)
    lines.append(
        f"Fullbuy: {fullbuy.get('kills', 0)}K/{fullbuy.get('deaths', 0)}D "
        f"(KD {fb_kd}) | ADR {fb_adr} | "
        f"Win-Rate {fullbuy.get('win_rate', 0)}% in {fullbuy.get('rounds', 0)} Runden"
    )

    # Eco performance
    if eco and eco.get("rounds", 0) >= 2:
        eco_kd = eco.get("kd", 0)
        eco_adr = eco.get("adr", 0)
        lines.append(
            f"Eco: {eco.get('kills', 0)}K/{eco.get('deaths', 0)}D "
            f"(KD {eco_kd}) | ADR {eco_adr} | "
            f"Win-Rate {eco.get('win_rate', 0)}%"
        )

        if eco_adr < 30:
            lines.append(
                "**Eco-Runden sind quasi unsichtbar.** Du holst kaum Damage "
                "mit der Pistole. Das ist verschenktes Potential.\n"
                "**Training:** Uebe Pistol-Only-Deathmatch — USP/Glock auf "
                "Kopfhoehe, jeder Schuss zaehlt. In Eco-Runden: "
                "Stack als Team eine Position, nutze den Ueberraschungseffekt. "
                "Ein Eco-Kill mit Headshot kann die gegnerische Wirtschaft brechen."
            )
        elif eco_kd >= 1.0:
            lines.append(
                "Starke Eco-Performance! Du holst auch mit Pistole Kills — "
                "das stoert die gegnerische Wirtschaft enorm."
            )

    # Force buy performance
    if force and force.get("rounds", 0) >= 2:
        force_kd = force.get("kd", 0)
        lines.append(
            f"Force-Buy: {force.get('kills', 0)}K/{force.get('deaths', 0)}D "
            f"(KD {force_kd}) | Win-Rate {force.get('win_rate', 0)}%"
        )

        if force.get("win_rate", 0) > 40:
            lines.append(
                "Deine Force-Buys zahlen sich aus — du gewinnst ueber 40% "
                "dieser Runden. Gutes Timing beim Forcen."
            )
        elif force_kd < 0.5 and force.get("rounds", 0) >= 3:
            lines.append(
                "Force-Buys gehen meistens schief. "
                "**Tipp:** Ueberlege, ob ein Full-Save + naechste Runde "
                "Fullbuy nicht besser waere. Ein SMG-Force gegen Fullbuy-Gegner "
                "hat nur ~30% Chance — manchmal ist Sparen klüger."
            )

    # Pistol round
    if pistol and pistol.get("rounds", 0) >= 2:
        lines.append(
            f"Pistolrunden: {pistol.get('kills', 0)}K/{pistol.get('deaths', 0)}D "
            f"| Win-Rate {pistol.get('win_rate', 0)}%"
        )
        if pistol.get("win_rate", 0) < 50 and pistol.get("rounds", 0) >= 2:
            lines.append(
                "Pistolrunden verlierst du zu oft. Sie setzen den Ton "
                "fuer die naechsten 2-3 Runden.\n"
                "**Training:** Pistol-Deathmatch. USP: Tap auf Kopfhoehe. "
                "Glock: Lauf nah ran, Burst auf Kopf. "
                "Und: Utility in Pistolrunden ist massiv underrated — "
                "eine Flash in der Pistolrunde gewinnt Runden."
            )

    # Compare eco+force vs fullbuy
    if eco and fullbuy and eco.get("rounds", 0) >= 2:
        eco_total_kd = eco.get("kd", 0)
        if fb_kd > 0 and eco_total_kd / max(fb_kd, 0.1) < 0.3:
            lines.append(
                "\n**Dein KD bricht in Eco-Runden komplett ein.** "
                "Das zeigt: Du verlasst dich zu stark auf Equipment. "
                "Gute Spieler holen auch mit Pistole/SMG Kills durch "
                "ueberlegenes Positioning und Aim."
            )

    return "\n".join(lines) + "\n"


def _overall_verdict(r: MatchResult) -> str:
    s = r.player_stats
    rating = r.rating
    lines = ["### Gesamtbewertung\n"]

    weaknesses = []
    if s.crosshair_placement_kills > 0 and s.crosshair_placement_median > 15:
        weaknesses.append("[[Crosshair Placement]]")
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
            f"\n**Fokus-Bereiche fuer das naechste Training:** "
            f"{', '.join(weaknesses)}"
        )

    # Concrete training plan
    lines.append("\n### Trainingsplan\n")
    plan = []

    if s.crosshair_placement_kills > 0 and s.crosshair_placement_median > 15:
        plan.append(
            "**Crosshair Placement (10 Min/Tag):** "
            "Workshop-Map \"Prefire Practice\" oder \"Yprac\" — "
            "jeden Tag eine Map durchgehen. Fadenkreuz immer auf Kopfhoehe, "
            "jede Ecke pre-aimen. Ziel: Durchschnitt unter 10°."
        )

    if s.counter_strafe_score < 75:
        plan.append(
            "**Counter-Strafing (15 Min/Tag):** "
            "Aim-Trainer oder Deathmatch — NUR mit Counter-Strafe schiessen. "
            "Kein einziger Schuss in Bewegung. A/D antippen, dann schiessen. "
            "Langsam anfangen, Speed steigern. Ziel: >85% stehend."
        )

    if s.opening_deaths > s.opening_kills:
        plan.append(
            "**Opening Duels (Bewusstsein):** "
            "Vor jedem Peek in den ersten 15 Sekunden fragen: "
            "Habe ich Utility geworfen? Weiss ich, wo der Gegner ist? "
            "Kann mein Teammate mich traden? Wenn nicht alles Ja: Nicht peeken."
        )

    if s.utility_per_round < 1.5:
        plan.append(
            "**Utility (5 Lineups/Map):** "
            "Lern pro Map 5 Standard-Lineups (2 Smokes, 2 Flashes, 1 Molly). "
            "Workshop-Maps wie \"Nade Training\" helfen. "
            "Wirf sie JEDE Runde — auch wenn du denkst, es lohnt sich nicht."
        )

    if s.avg_recoil_index > 4:
        plan.append(
            "**Spray Control (10 Min/Tag):** "
            "Workshop-Map \"Recoil Master\" — AK/M4 Pattern lernen. "
            "Erst 5 Bullets kontrollieren, dann 10, dann Full-Spray. "
            "Im Match: Burst-Disziplin (2-3 Schuss), dann Pause."
        )

    if s.kd_ratio < 0.9 and s.survival_rate < 25:
        plan.append(
            "**Positionierung (Bewusstsein):** "
            "Schau dir 1 Pro-Demo pro Woche an — gleiche Map, gleiche Position. "
            "Achte auf: Wo steht der Pro? Wann peekt er? Wann rotiert er? "
            "Kopiere die Positionen exakt."
        )

    if s.flash_teammates_blinded > s.flash_enemies_blinded and s.flashes_thrown > 3:
        plan.append(
            "**Flashes (5 Pop-Flashes/Map):** "
            "Lern Pop-Flashes, die ueber Waende/Boxen fliegen und "
            "direkt im Sichtfeld des Gegners detonieren. "
            "Dein Team darf sie NICHT sehen. YouTube: \"CS2 Pop Flash Tutorial\"."
        )

    # Round pattern insights for training
    tl = r.round_timeline
    if tl:
        zero_impact = [rd for rd in tl if rd["player_died"] and rd["player_kills"] == 0]
        if len(zero_impact) >= 4:
            plan.append(
                "**Runden-Impact (Bewusstsein):** "
                f"In {len(zero_impact)} Runden bist du gestorben ohne einen Kill. "
                "Regel: Wenn du keinen Kill holst, musst du ueberleben. "
                "Kein Dry-Peek ohne Info, kein Hero-Play ohne Utility. "
                "Lieber passiv die Runde ueberleben als sinnlos sterben."
            )
        early_deaths = [rd for rd in tl if rd["died_early"]]
        if len(early_deaths) >= 3:
            plan.append(
                "**Fruehe Tode vermeiden:** "
                "In den ersten 15 Sekunden jeder Runde: Nur Angle halten, "
                "nicht pushen. Lass den Gegner zu dir kommen. "
                "Jeder fruehe Tod = 4v5 fuer dein Team."
            )
        # Tilt detection
        max_streak = 0
        cur = 0
        for rd in tl:
            if not rd["won"] and rd["player_died"]:
                cur += 1
                max_streak = max(max_streak, cur)
            else:
                cur = 0
        if max_streak >= 4:
            plan.append(
                "**Anti-Tilt (Mental):** "
                "Du hattest eine Verlust-Serie von "
                f"{max_streak} Runden. Nach 2 Niederlagen: "
                "Timeout nehmen, tief durchatmen, Winkel wechseln. "
                "Nicht das gleiche Play wiederholen, das schon 3x gescheitert ist."
            )

    # Duel matrix insights for training
    if r.duel_matrix:
        dominated_by = [d for d in r.duel_matrix if d["deaths"] >= 3 and d["kd"] < 0.5]
        if dominated_by:
            names = ", ".join(d["name"] for d in dominated_by[:2])
            plan.append(
                f"**Duell-Analyse:** Du wirst von {names} dominiert. "
                "Gegen Spieler, die dich wiederholt toeten: "
                "Vermeide den direkten Fight. Nutze Smoke/Flash oder "
                "rotiere auf die andere Seite. Erzwinge 1v1 nur mit Vorteil."
            )

    # Economy insights
    econ = r.economy_performance
    if econ:
        eco_data = econ.get("eco", {})
        if eco_data and eco_data.get("rounds", 0) >= 2 and eco_data.get("adr", 0) < 40:
            plan.append(
                "**Pistol-Aim (10 Min/Tag):** "
                "Deine Eco-Performance ist schwach — das liegt an Pistol-Aim. "
                "Spiel 10 Min. Pistol-Only-Deathmatch: USP Tap auf Kopf, "
                "Glock Burst Nahkampf. Ziel: Mindestens 1 Kill pro Eco-Runde."
            )

    if plan:
        for i, item in enumerate(plan, 1):
            lines.append(f"{i}. {item}")
    else:
        lines.append(
            "Keine kritischen Baustellen identifiziert. "
            "Halte dein Niveau mit regelmaessigem Deathmatch "
            "und Utility-Training."
        )

    return "\n".join(lines)
