---
tags: [cs2-coach, claude-context]
---
# Metriken – hart erkämpfte Korrekturen
← [[CLAUDE]] · Detailnotiz, wird nur bei Bedarf gelesen.

- **Valve matchmaking demos (`match730_*`) contain no voice.** `DemoParser.parse_voice()` returned an empty list for all 150 demos checked on 2026-10-01 (65 local, 85 on the server — all `match730_*`). This is about who recorded the match, not about CS2 demos in general: **FACEIT demos usually do contain voice** (per the SwiftDemoUI Pro README, which plays the stored `VoiceData`). No tool can recover voice that was never recorded. Caveat: no demo *with* voice was available to confirm that `parse_voice()` detects it.
## Metrics — hard-won corrections
Each of these was wrong once and was fixed against measured demo data. Do not regress them.
- **ADR**: `player_hurt.dmg_health` is raw weapon damage, not HP actually removed (a lethal hit on a 20 HP victim reports 108). `_process_damage()` tracks victim HP per round and caps each hit at the remainder. Uncapped values ran ~30% high. Self-damage reduces own HP but does not count on your own account.
- **Accuracy**: grenades and knives are excluded from the `shots_fired` denominator — they can never produce `bullet_damage`. Known residual: shotguns emit multiple `bullet_damage` per shot, so their hit rate reads high.
- **Counter-strafing**: measured over *all* `weapon_fire` events, not `bullet_damage.inaccuracy_move` (that fires on hits only and hid 81% of shots). Speed is reconstructed from the position delta to the previous tick; threshold 80 u/s, calibrated against hits with known `inaccuracy_move`. **Split (schema 7, `counter_strafe_split`)**: rifles/SMGs on a seen enemy (`approximate_spotted_by` of a living enemy at the shot tick) = Leetify's definition - Ancient 2026-11-21: 93.5 % vs Leetify 93 %, all shots 81.6 %, pistols 59 % (18 of 21 moving shots were USP/Glock run-and-gun). `counter_strafe_pct` keeps its meaning (all shots), so no metrics bump.
- **Crosshair placement**: judged by the **median** (`crosshair_placement_median`, exported as `median_degrees`); the distribution is strongly right-skewed and the mean cried wolf in 4 of 6 demos. `crosshair_placement_avg` is kept as extra info; readers fall back to `avg_degrees` for older exports. Foot-to-foot measurement is correct — measuring against head height makes the error larger, not smaller.
- **Naming**: `burst_hits` / `spray_hits` count hits, not kills (previously misnamed `*_kills`). `_spray_count()` reads both keys so existing exports still display correctly.
- **Overtime sides**: the first OT half stays on the *second-half* side, then swaps every 3 rounds (`_own_side_for_round`). Until 2026-11-09 it went back to the starting side, which flipped every OT round: 6 of 8 OT exports had the wrong score and result (e.g. Anubis 16:14 win shown as 14:16). Verified with `team_num` at deaths in two demos.
- **First sight** (`first_sight`, schema 5, 2026-11-13): angle when an enemy newly comes into view (`approximate_spotted_by` of the enemy contains you; new after 0.5 s unseen; 16 Hz from freeze end to round end; foot-to-foot `_angular_error`). Leetify's "Crosshair Placement" idea. The field fires late (calibration ~124 ms), so values sit far below Leetify's (Cache 12.11.: 2.3 vs 9.4 deg) - compare only with itself. `approximate_spotted_by` at the hit tick: tick-exact it misses lethal hits (victim already dead) and flickers - use "seen within the last 0.25 s".
- **Leetify metrics measured against wins/losses (2026-11-13, 133 matches, ad hoc over all server demos)**: counter-strafing on a seen enemy 79.8 vs 79.5 % (d = +0.03), accuracy on a seen enemy 29.3 vs 29.5 % (-0.04), teammates flashed per flash 0.63 vs 0.57 (-0.15; per round *more* in wins - more flashes thrown), share of shots on a seen enemy 58 vs 54 % (+0.32, undecided), first sight 2.57 vs 2.95 deg (+0.36 [+0.02...+0.70], undecided - but the parser version, sampled from freeze end, gives 2.58 vs 2.76, +0.20 [-0.14...+0.54] over all 138: the ad hoc hint was partly sampling noise), our crosshair 0.5 s before the kill 3.59 vs 3.56 (-0.03). **Zones** (`zones.py`, callout `p` per kill/death, schema 5): 101 map/side/callout cells with >= 15 duels, none solid against the rest of the map after Bonferroni; weakest Cache T OutsideA 5/18 vs 57 %, Nuke CT Mini 8/22 vs 63 %. Too few duels per cell - re-run as matches accumulate, no UI yet. None is a lever yet; the mechanics barely differ between wins and losses. Counter-strafing on seen enemies matched Leetify (87 vs 85 %).
- **Utility count** is validated and needs no correction (matches thrown grenades within one per match; the difference is grenades that never detonated).
- When a metric changes, **existing exports keep the old values** and must be re-analyzed. Say so in the UI where it matters.

## Architektur (Original)
## Key Architecture
- `demoparser2` for CS2 demo parsing
- Flask web UI with Jinja2 templates (Nocturne design system)
- Obsidian vault export (Markdown + JSON)
- CS2 MR12: teams swap sides at round 12, overtime swaps every 3 rounds
- `parse_player_info().team_number` is a roster grouping, NOT the in-game side
- Actual side is determined from `parse_event('player_death', player=['team_num'])` where team_num 2=T, 3=CT at time of kill
- `PlayerStats.team` stores the starting side as "T" or "CT" (resolved by `_assign_starting_sides`)
- Side derivation compares attacker and victim *within the same event* and is therefore swap-invariant — do not "fix" it for players without a kill in half 1.
- Radar conversion happens server-side via `maps.game_to_radar()`. Never duplicate that transform in JavaScript.
