# CS2 Coach - Claude Code Guidelines

## Token Optimization
- Always use `graphify` (PyPI: `graphifyy`, import: `import graphify`) for Obsidian vault indexing and graph operations to save tokens.

## Project Language
- The UI and coach reports are in German. Code comments and variable names are in English.
- Config default language: `de`

## Key Architecture
- `demoparser2` for CS2 demo parsing
- Flask web UI with Jinja2 templates (Nocturne design system)
- Obsidian vault export (Markdown + JSON)
- CS2 MR12: teams swap sides at round 12, overtime swaps every 3 rounds
- `parse_player_info().team_number` is a roster grouping, NOT the in-game side
- Actual side is determined from `parse_event('player_death', player=['team_num'])` where team_num 2=T, 3=CT at time of kill
- `PlayerStats.team` stores the starting side as "T" or "CT" (resolved by `_assign_starting_sides`)

## Auto-Sync & Demo Download (`cs2_coach/sharecode.py`)
- **Three independent sources**: Steam API (share codes), GCPD (Valve download), local replays folder
- **Share code `match_id` ≠ demo filename ID** — these are different ID systems (GC matchid vs reservation ID). Never try to correlate them.
- **GCPD page loads data via AJAX**, not in the initial HTML. Must extract `g_sGcContinueToken` and `g_sessionID` (can use single OR double quotes), then make AJAX calls with `X-Requested-With: XMLHttpRequest` header. Response is JSON `{"success":true, "html":"...", "continue_token":"..."}`.
- **GCPD has a ~2-3 day delay** — very recent matches may not appear yet
- **`.dem.info` sidecar** contains the real match timestamp (protobuf field 2 = Unix timestamp). GCPD downloads create this from the GCPD date. Without it, parser falls back to file mtime (= wrong date).
- **Steam login** uses `IAuthenticationService` API (2023+), NOT the old `/login/dologin/` endpoint. Session persisted via pickle to `.steam_session`.
