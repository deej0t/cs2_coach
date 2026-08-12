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
