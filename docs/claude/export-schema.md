---
tags: [cs2-coach, claude-context]
---
# Export-Schema & 2D-Replay
← [[CLAUDE]] · Detailnotiz, wird nur bei Bedarf gelesen.

## Export Schema
- The export JSON is compact and grows over time: `kill_positions` (with tick), `utility_positions` `{t, x, y, r}`, crosshair buckets, `median_degrees`. **Older exports lack newer fields** — read defensively and tell the user a re-analysis fills them in.
- Ticks in `kill_positions` enable `demo_gototick` jumps.
- **Only the configured player's analyses go into the vault** (`obsidian.is_own_player()`, steam_id first, name as fallback - names change, "deej0ttv" became "deej0t"). Export file names contain date, map, score and time but **not the player**, so the scoreboard's "Analysieren" button used to overwrite the user's own export (and Markdown note) with a teammate's numbers and post them to Discord. Found 2026-10-14: two server exports held a teammate's stats. Teammate analyses now render `result.html` only.
- **Schema 4** (2026-10-14): `player.utility.he_damage` / `fire_damage` and per grenade `tk` (tick) + `s` (seconds since freeze-time end). Grenade damage counts **enemies only** (same starting side = same team, both swap together) and is capped at remaining HP like ADR. ADR itself still includes team damage - unchanged, so the regression values hold.
- **Every export carries `schema_version` and `metrics_version`** (`export_version.py`). They are deliberately separate: schema = which fields exist (missing = incomplete but correct), metrics = how the numbers were computed (missing = complete but *wrong*). Only a metrics bump makes an export unusable.
- **Bump `METRICS_VERSION` whenever a metric's meaning changes** and add the reason to `METRICS_FIXES` — that string is what the UI shows the user. Add to `SCHEMA_FEATURES` when a field is added.
- Exports written before versioning have no fields; `_detect_*` infers both from field presence rather than declaring them all stale. `median_degrees` is the marker for metrics v1 (it landed after the ADR, accuracy and counter-strafing fixes). All 63 existing exports resolve to metrics v1 — they were re-analyzed one minute after the crosshair commit.
- `_get_exports()` attaches `export_status(data)` as `version` to every entry, so any page can flag stale data without re-reading the file.

## 2D Replay (`/api/replay/<export>/<round>`)
- Built on demand rather than exported: positions of all 10 players over a full round cost ~0.3s regardless of resolution (8 fps ≈ 4640 rows).
- Team colors come from `parse_ticks` `team_num` (2=T, 3=CT) in the same ticks read for positions — per round, so the half-time swap is handled automatically. Players without usable `team_num` render grey as "unknown", never guessed.
- Grenades appear at detonation and disappear when they expire. `smokegrenade_expired` / `inferno_expire` carry the same `entityid` as the ignition, but **Steam reuses entity ids within a match** — always take the *next* end after the ignition and clamp at round end. Flash and HE have no end event and get a short fixed display time.
- Player labels are shortened to 9 chars (letters, digits, `.`, `-`, space); if nothing survives filtering, fall back to the original name.
