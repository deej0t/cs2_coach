---
tags: [cs2-coach, claude-context]
---
# Modul-Layout (vollständig)
← [[CLAUDE]] · Detailnotiz, wird nur bei Bedarf gelesen.

## Layout
```
cs2_coach/
  parser.py      (1700) demo parsing, all metric extraction
  coach.py        (980) prose report generation
  practice.py     (990) CS2 practice-server cfg generator (5 modes + server/warmup)
  findings.py     (600) machine-readable findings, baselines, relevance
  evidence.py     (190) effect size + CI for any two-group comparison
  utility_analysis.py (120) throw relevance per grenade type
  zones.py         (90) duels per callout across all matches (map/side/place vs rest, compare_rates + Bonferroni); measured in levers.py
  levers.py       (270) Hebel-Suche: every sync re-measures all candidates (round: early grenade; match: findings rules + grenade types; death: traded/opening/far; zones) via the existing evidence functions, keeps verdicts in .levers_state.json, result in .levers.json, Discord/Telegram when a non-outcome-driven candidate turns solid (first run = baseline only). Since 2026-11-20 also you vs the lobby (duel_value: all situations, solid zones; per-match net and the five weakest zones as info). Page /hebel (web/levers_page.py)
  winprob.py      (490) P(CT wins the round) from the round state (CT alive, T alive, bomb planted, time bucket) plus the freeze-end equipment edge; learned from all ten players of every 5v5 demo in the demo folder (cache winprob_cache/), `calibration()` predicts every match from the others, `swings()` prices each death in percentage points (with map and callout of victim and killer, cache v2). No page, no advice yet - see findings.md
  costly_moments.py (150) your costliest deaths per match (winprob price, out-of-fold; only deaths with a mate still alive - as last man every death costs nearly the whole rest, the list was all lost 1v1 clutches); joined to the export's kill_positions by round (one own death per round; 845 of 848 rounds matched) for tick, callout, weapon. Used by report_ai (TEUERSTE TODE fact line + phone line with `demo_gototick`, 5 s before the death) and training_server (`duel_order`)
  duel_value.py   (180) your duels in win-probability points vs the lobby at the same callout / in the same situation (side, numbers, bomb), via evidence.py with Bonferroni; prices from the out-of-fold table; per_match = gain/cost/net per round vs the other nine; by_habit = rate of alone/early/opening/unused-grenade deaths per side vs the lobby (cache v3 fields)
  dashboard.py    (230) start page: Premier rating in its tier colour with the last-30 curve (display/motivation only - it is outcome, never advice), last match + AI review intro, goal as "Mission" (training.current_plan, same verdict as the briefing), last server training + next command to copy (from report_ai focus.json: `!granaten` for the mission, `!training` for other homework, else `!warmup`), last 6 matches as radar tiles (_partials/dashboard_focus.html). Premier = `player.rank.new` >= 1000 (map ranks in competitive are 0-18). The old dashboard's fixed-threshold alerts and "Gold Nova" benchmarks are gone
  match_compare.py (190) export detail card: this match vs own last 30 (median, percentile, top 5) + grenades of both teams (_partials/match_compare.html)
  export_version.py   (190) schema/metrics versioning of exports
  health.py       (300) system status checks + Discord alert on state change
  training.py     (160) training goals with start date + progress (before/after)
  sync.py          (50) GCPD demo download without the web UI (`cs2-coach sync`)
  server_stats.py (600) server leaderboard from the CS2 server's HTTP log (Elo, records, H2H)
  server_live.py  (350) in-game chat commands (!top, !rank ...) via RCON, Discord game end + player of the week
  rcon.py          (60) minimal Source RCON client
  ingame_coach.py (240) AI coach in the game chat: !coach <question>, !tipp, end-of-training Fazit
  training_scenarios.py (210) multi-enemy scenarios from demos: situation, retake, site hold
  grenade_lineups.py (170) lineups from the demos: throw spot + view angles + landing, grouped by frequency
  death_review.py (150) every own death from the demos: traded, mate distance, opening, round won (cache death_cache/)
  own_lineups.py  (100) own grenade lineups: !speichern <Name> after a landed throw (plugin coach_nade_last), !lineups, !loeschen, practice with !granaten meine; /lineups shows them + demo lineups on the radar (web/lineups_page.py)
  training_effect.py (110) does server training show in real matches? matches since the first training, with vs without a training in the 24 h before (crosshair, counter-strafe, grenades/round), card on /training
  workshop.py     (90) !workshop <ID|link> [name] (owner/admins): store a workshop map in .workshop_maps.json (title via GetPublishedFileDetails), switch to it (host_workshop_map downloads it); then votable with !map <name>
  report_ai.py   (260) AI match review per export (facts = findings vs own median of the 10 before + round timeline + the MISSION line (early grenade k/n vs target, met or not) + TEUERSTE TODE (costly_moments; the review reads the demos of the matches it reviews first, `prefer=`, since it runs before the lever search fills the cache); coach fixes better/worse/focus, AI only phrases; numbers checked). A missed mission is the focus before the largest regression (`choose_focus`, key `early_util`, homework `!granaten`). focus.json = homework -> training goal + duel order; sent to Discord/Telegram after sync
  weekly.py       (110) weekly review: last week vs the 4 weeks before + trainings + training_effect verdicts, AI-phrased, sent once per week from Monday (report_ai/week-<id>.txt)
  map_vote.py     (80) !map <name> + !ja: map change by majority of connected humans (pool maps only)
  live_coach.py  (230) live coach in real matches via GSI: grenades thrown / taken to the grave, hints on /live
  live_plan.py   (150) match plan for the live coach: your most-thrown grenades per map/side (grenade_lineups cache, landing named by the nearest kill-position callout), target + quote on the map; built in a background thread on the first GSI signal of a map (.live_plan.json)
  training_goal.py (90) goal per training: weakest measured habit, counted per duel, judged with compare_rates
  training_server.py (460) duel training on the server: replays your lost duels with a bot (!training <map>); order `duel_order`: latest match first, within and after it the dearest deaths (last-man deaths count 0), without prices newest first
server_plugin/CoachTeleport/ C# CounterStrikeSharp plugin: `coach_tp` (teleport with view angles) for the training
  briefing.py     (290) daily training briefing: facts, AI intro, sending -> Discord/Telegram + vault
  briefing_view.py (230) fixed briefing layout (goals per match, bars, exercises, progress)
  telegram.py     (110) Telegram bot sending, chat id auto-discovery
  obsidian.py     (600) Markdown + JSON export into the vault
  sharecode.py   (1040) Steam API / GCPD / local demo discovery + download
  cli.py          (650) click CLI: analyze, batch, briefing, health, setup, sync, web
  ai_chat.py      (420) Gemini + Ollama streaming backends
  chat_store.py   (165) chat sessions persisted in the vault
  web/app.py    (11000) Flask app, ~97 routes
  web/auth.py     (160) optional password protection
  web/daily_briefing.py (130) /daily: briefing preview + "send now" (blueprint, not in app.py)
  web/server_board.py    (50) /server (public leaderboard) + /api/serverlog/<token> (log ingest)
  web/live_page.py       (60) /live (phone page, polls /api/live, optional speech) + /api/gsi/<token> (GSI ingest)
  web/training_page.py   (40) /training: results of the server duel training
  web/i18n.py     (830) translations
  web/templates/        Jinja2 (Nocturne design system), _partials/ for shared blocks
tests/                  pytest, ~14 files — run with `pytest` (config in pytest.ini)
docker/                 compose, portainer stack, unraid template
```
- `web/app.py` is still ~10,000 lines. **New functionality goes into its own module** and is imported by the app; do not grow app.py further.
- Shared template markup belongs in `templates/_partials/` (e.g. the 2D kill map used by both `result.html` and `export_detail.html`), never duplicated.
