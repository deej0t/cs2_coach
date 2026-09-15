# CS2 Coach - Claude Code Guidelines

## Token Optimization
- Always use `graphify` (PyPI: `graphifyy`, import: `import graphify`) for Obsidian vault indexing and graph operations to save tokens.
- graphify is for **exploring the vault**, not for app runtime. The only runtime use is `cs2_coach/graph.py` (knowledge-graph export of the coach folder).

## Project Language
- The UI and coach reports are in German. Code comments and variable names are in English (newer modules also carry German docstrings — both are fine).
- Config default language: `de`
- Commit messages are German: a one-line subject stating the *effect*, then a body explaining *why*, with the measurements that prove it. End with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

## Layout
```
cs2_coach/
  parser.py      (1700) demo parsing, all metric extraction
  coach.py        (980) prose report generation
  practice.py     (990) CS2 practice-server cfg generator (5 modes + server/warmup)
  findings.py     (600) machine-readable findings, baselines, relevance
  obsidian.py     (600) Markdown + JSON export into the vault
  sharecode.py   (1040) Steam API / GCPD / local demo discovery + download
  cli.py          (620) click CLI: analyze, batch, graph, setup, web
  ai_chat.py      (420) Gemini + Ollama streaming backends
  chat_store.py   (165) chat sessions persisted in the vault
  web/app.py    (11000) Flask app, ~97 routes
  web/auth.py     (160) optional password protection
  web/i18n.py     (830) translations
  web/templates/        Jinja2 (Nocturne design system), _partials/ for shared blocks
tests/                  pytest, ~14 files — run with `pytest` (config in pytest.ini)
docker/                 compose, portainer stack, unraid template
```
- `web/app.py` is already over 11,000 lines. **New functionality goes into its own module** and is imported by the app; do not grow app.py further.
- Shared template markup belongs in `templates/_partials/` (e.g. the 2D kill map used by both `result.html` and `export_detail.html`), never duplicated.

## Config & Environment
- Config file: `config.yaml` (see `config.yaml.example`). `CS2COACH_CONFIG` overrides the path — **both** the web app and the CLI honor it.
- `CS2COACH_PASSWORD` (env, plaintext, hashed at startup) takes precedence over `auth.password_hash` in config.yaml. No password set = auth fully inactive.
- `CS2COACH_SECRET_KEY`, else `.secret_key` next to the config, created with `O_CREAT|O_EXCL` so parallel gunicorn workers share one key.
- Docker: `CS2COACH_UPDATE_INTERVAL` (auto-update, 0 disables), `CS2COACH_BRANCH`. Volumes under `/data` (`demos`, `vault`, `cfg`).

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

## Metrics — hard-won corrections
Each of these was wrong once and was fixed against measured demo data. Do not regress them.
- **ADR**: `player_hurt.dmg_health` is raw weapon damage, not HP actually removed (a lethal hit on a 20 HP victim reports 108). `_process_damage()` tracks victim HP per round and caps each hit at the remainder. Uncapped values ran ~30% high. Self-damage reduces own HP but does not count on your own account.
- **Accuracy**: grenades and knives are excluded from the `shots_fired` denominator — they can never produce `bullet_damage`. Known residual: shotguns emit multiple `bullet_damage` per shot, so their hit rate reads high.
- **Counter-strafing**: measured over *all* `weapon_fire` events, not `bullet_damage.inaccuracy_move` (that fires on hits only and hid 81% of shots). Speed is reconstructed from the position delta to the previous tick; threshold 80 u/s, calibrated against hits with known `inaccuracy_move`.
- **Crosshair placement**: judged by the **median** (`crosshair_placement_median`, exported as `median_degrees`); the distribution is strongly right-skewed and the mean cried wolf in 4 of 6 demos. `crosshair_placement_avg` is kept as extra info; readers fall back to `avg_degrees` for older exports. Foot-to-foot measurement is correct — measuring against head height makes the error larger, not smaller.
- **Naming**: `burst_hits` / `spray_hits` count hits, not kills (previously misnamed `*_kills`). `_spray_count()` reads both keys so existing exports still display correctly.
- **Utility count** is validated and needs no correction (matches thrown grenades within one per match; the difference is grenades that never detonated).
- When a metric changes, **existing exports keep the old values** and must be re-analyzed. Say so in the UI where it matters.

## Findings, Baselines, Relevance (`findings.py`)
- Findings are derived from the already-exported player JSON, not from the report prose — so they apply retroactively to all exports without re-parsing a demo. They are deliberately **not** persisted: thresholds live in one place and changes take effect across the whole history.
- `training_priorities()` is the **single source** for training goals on both the dashboard and `/coaching`. It excludes outcome-driven metrics (survival, K/D, ADR, KAST — they rise simply by winning the round) and rules without discriminatory power.
- `build_baselines()` computes the user's personal p10–p90 distribution and flags rules that fire in >90% or <5% of matches as non-discriminating.
- `build_relevance()` reports Cohen's d **with a 95% confidence interval** and a verdict (robustly positive/negative, proven irrelevant, undecided). Never present an effect size as fact — at ~58 matches most are undecided. For undecided cases it also computes how many matches would settle the question.
- Windows are aggregated by **median**, not mean; one 21-0 match otherwise fakes a collapse.

## Export Schema
- The export JSON is compact and grows over time: `kill_positions` (with tick), `utility_positions` `{t, x, y, r}`, crosshair buckets, `median_degrees`. **Older exports lack newer fields** — read defensively and tell the user a re-analysis fills them in.
- Ticks in `kill_positions` enable `demo_gototick` jumps.
- **Every export carries `schema_version` and `metrics_version`** (`export_version.py`). They are deliberately separate: schema = which fields exist (missing = incomplete but correct), metrics = how the numbers were computed (missing = complete but *wrong*). Only a metrics bump makes an export unusable.
- **Bump `METRICS_VERSION` whenever a metric's meaning changes** and add the reason to `METRICS_FIXES` — that string is what the UI shows the user. Add to `SCHEMA_FEATURES` when a field is added.
- Exports written before versioning have no fields; `_detect_*` infers both from field presence rather than declaring them all stale. `median_degrees` is the marker for metrics v1 (it landed after the ADR, accuracy and counter-strafing fixes). All 63 existing exports resolve to metrics v1 — they were re-analyzed one minute after the crosshair commit.
- `_get_exports()` attaches `export_status(data)` as `version` to every entry, so any page can flag stale data without re-reading the file.

## 2D Replay (`/api/replay/<export>/<round>`)
- Built on demand rather than exported: positions of all 10 players over a full round cost ~0.3s regardless of resolution (8 fps ≈ 4640 rows).
- Team colors come from `parse_ticks` `team_num` (2=T, 3=CT) in the same ticks read for positions — per round, so the half-time swap is handled automatically. Players without usable `team_num` render grey as "unknown", never guessed.
- Grenades appear at detonation and disappear when they expire. `smokegrenade_expired` / `inferno_expire` carry the same `entityid` as the ignition, but **Steam reuses entity ids within a match** — always take the *next* end after the ignition and clamp at round end. Flash and HE have no end event and get a short fixed display time.
- Player labels are shortened to 9 chars (letters, digits, `.`, `-`, space); if nothing survives filtering, fall back to the original name.

## Auto-Sync & Demo Download (`cs2_coach/sharecode.py`)
- **Three independent sources**: Steam API (share codes), GCPD (Valve download), local replays folder
- **Share code `match_id` ≠ demo filename ID** — these are different ID systems (GC matchid vs reservation ID). Never try to correlate them.
- **GCPD page loads data via AJAX**, not in the initial HTML. Must extract `g_sGcContinueToken` and `g_sessionID` (can use single OR double quotes), then make AJAX calls with `X-Requested-With: XMLHttpRequest` header. Response is JSON `{"success":true, "html":"...", "continue_token":"..."}`.
- **GCPD tabs**: current competitive matches live under `matchhistorycompetitivepermap` ("Gewertete Wettkampfspiele"). `matchhistorycompetitive` is the old CS:GO history and stays empty for CS2 players. Also query `matchhistorypremier` and `matchhistorywingman`.
- **`continue_token` is a global time cursor, not a per-mode counter.** An empty page only means "no match of this mode in this time window" — never stop there. Paginate up to 12 pages and abort after 6 consecutive empty ones.
- **GCPD has a ~2-3 day delay** — very recent matches may not appear yet. Valve also expires demo links fast: of 140 listed Premier matches only 3 still had a download link.
- **`.dem.info` sidecar** contains the real match timestamp (protobuf field 2 = Unix timestamp). GCPD downloads create this from the GCPD date. Without it, parser falls back to file mtime (= wrong date).
- **Steam login** uses `IAuthenticationService` API (2023+), NOT the old `/login/dologin/` endpoint. Session persisted via pickle to `.steam_session`.
- **QR login**: `BeginAuthSessionViaQR` + poll. Do **not** send `device_details` — it is a protobuf submessage and form-encoding it as a JSON string returns HTTP 400 with an HTML error page. The SteamID is not returned; read it from the JWT `sub` claim (pad the base64 first). Steam rotates the challenge URL while polling — pass the new one through and let the UI redraw.

## Web Auth
- The password hash is read from the **config file** (cached on its mtime), not from the in-memory cfg dict — otherwise gunicorn workers disagree and the login loops endlessly. Costs one `stat()` per request; password changes take effect without restart.
- `settings_save()` must start from `dict(cfg)` so unknown keys (like the auth hash) survive a settings save.
- Public routes: `/share/<file>` and `/static`. `/api/*` answers 401 instead of redirecting; the frontend must translate that into "session expired", not "connection error".
- Only remember a `next` target for GET navigations that want HTML (otherwise `/favicon.ico` becomes the login target). Login locks per IP for 5 minutes after 5 failed attempts.

## Testing
- `pytest` (see `pytest.ini`, `requirements-dev.txt`). `test_pages_smoke.py` renders every parameterless GET route plus all export detail pages — it catches missing templates and silently lost features.
- Tests that reload `cs2_coach.web.app` with a temporary config **must restore module state in a fixture's `finally`**, otherwise later tests find no vault and fail only when run as a suite.
