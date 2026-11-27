# CS2 Coach – Claude Code Guidelines

## Tokens
- Diese Datei wird in jede Session geladen → nur Regeln + Index. Details stehen in `docs/claude/*.md` und werden **nur gelesen, wenn die Aufgabe das Thema berührt** (keine `@`-Imports – die würden alles wieder vorladen).
- Suchen mit `grep`, dann nur benötigte Zeilenbereiche lesen. `web/app.py` (~10.000 Zeilen) nie ganz lesen.
- Kein graphify (2026-10-14 entfernt).
- Neue Gotchas: 1 Zeile hier nur wenn sie *jede* Aufgabe betrifft, sonst in die passende Detailnotiz. Mess-Erzählungen gehören in Commit-Messages.

## Sprache & Commits
- UI/Reports Deutsch; Code-Kommentare/Namen Englisch (deutsche Docstrings ok). Config-Default `de`.
- Commits Deutsch: Betreff = *Wirkung*, Body = *warum* + Messwerte. Ende: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Layout (Kurzform – Details: `docs/claude/layout.md`)
`cs2_coach/`: parser (Demo-Parsing, Metriken) · coach (Report-Prosa) · findings/evidence (Statistik) · utility_analysis · zones · levers (Hebel-Suche, /hebel) · winprob/duel_value/costly_moments (Siegwahrscheinlichkeit je Rundenzustand, Duellwert gegen die Lobby, teuerste Tode) · key_moments/aim_trace/nades3d/map3d/map3d_render/models3d (3D-Szene der Schlüsselmomente, Player `_partials/moment_theater.html`, Assets per `docker/unraid/maps3d-export.sh`, Szenen-Cache `.moments_cache/`) · dashboard · match_compare · export_version · health · training/briefing/briefing_view (Tages-Briefing) · sync/sharecode (Demo-Download) · server_stats/server_live/rcon/map_vote/workshop (CS2-Server) · training_server/training_scenarios/training_goal/training_effect/grenade_lineups/own_lineups/death_review/ingame_coach (Server-Training) · live_coach/live_plan (GSI) · report_ai/weekly · obsidian (Vault-Export) · telegram · ai_chat/chat_store · cli · `web/` (app.py ~97 Routen, auth, i18n, Blueprints, `templates/_partials/`).
`server_plugin/CoachTeleport/` (C#, CounterStrikeSharp) · `tests/` (pytest) · `docker/` (compose, unraid-Skripte).

## Harte Regeln (immer)
- **app.py nicht vergrößern** – neue Funktion = eigenes Modul. Geteiltes Markup → `templates/_partials/`, nie duplizieren.
- **Statistik nur über `evidence.py`** (`compare_groups`/`compare_rates`, `comparisons=k` bei Best-of-k). Keine Empfehlung aus roher Mittelwertdifferenz mit fixem Schwellwert; Effektgrößen immer mit CI + Verdict. Outcome-getriebene Metriken (Survival, K/D, ADR, KAST, Granaten-*Anzahl* pro Match) sind keine Hebel.
- **Exports sortieren mit `obsidian.export_sort_key`**, nie per Dateiname. `_get_exports()` = neueste zuerst.
- **Metrik-Bedeutung geändert → `METRICS_VERSION` bump + `METRICS_FIXES`**; neues Feld → `SCHEMA_FEATURES`. Ältere Exports defensiv lesen.
- Nur Analysen des eigenen Spielers in den Vault (`obsidian.is_own_player()`).
- Radar-Transform nur serverseitig (`maps.game_to_radar()`), nie in JS.
- Side-Logik: `team_number` ist Roster, nicht Seite; Seite aus `player_death.team_num` (2=T, 3=CT). MR12, OT: erste OT-Hälfte = Seite der 2. Hälfte, dann Wechsel alle 3 Runden. Side-Ableitung nicht „fixen“.
- **Secrets nie ins Repo** (RCON-Passwort, Tokens, Heartbeat-URL, Cloudflare-Token).
- Docker: Container nur nach Image-Rebuild recreaten; nie `entrypoint:` in Compose; Port `"5001:5000"`; nach Recreate von außen prüfen (`https://cs2-coach.deej0t.eu/` → 302).
- Unraid-Skripte: nur LF, Cron auf /boot mit `bash`, vor Schreiben unter `/mnt/user` `mountpoint -q /mnt/user`.
- **Prüfen mit `pytest`** vor jedem Commit. Neue Route/Template → `test_pages_smoke.py` muss sie rendern; neue Parser-Metrik → ihre *Anzahl* in `test_parser_regression.py` festhalten (Details: `docs/claude/testing.md`).

## Config
`config.yaml` (Vorlage `config.yaml.example`; `CS2COACH_CONFIG` überschreibt, Web + CLI). `CS2COACH_PASSWORD` > `auth.password_hash`; kein Passwort = Auth aus. `CS2COACH_SECRET_KEY` sonst `.secret_key` (O_EXCL, damit alle gunicorn-Worker denselben Key teilen). Docker: `CS2COACH_UPDATE_INTERVAL` (0 = Auto-Update aus), `CS2COACH_BRANCH`, Volumes `/data/{demos,vault,cfg}`. State-Dateien (`.xyz.json`) liegen neben der Config.

## Detailnotizen – lesen, wenn die Aufgabe das berührt
| Notiz | Thema |
|---|---|
| `docs/claude/metrics.md` | ADR, Accuracy, Counter-Strafe, Crosshair, First Sight, OT-Seiten, Voice |
| `docs/claude/findings.md` | findings.py, Baselines, Relevanz, Tilt, Utility-/Trade-/Hebel-Messungen |
| `docs/claude/export-schema.md` | Export-JSON, Schema-/Metrics-Version, 2D-Replay |
| `docs/claude/briefing.md` | Tages-Briefing, Ziele, Telegram/Discord, /daily |
| `docs/claude/live-coach.md` | GSI, frühe Granate, /live, live_plan |
| `docs/claude/server-board.md` | Server-Log-Ingest, Elo-Bestenliste, Ingame-Befehle, GOTV/server_aim |
| `docs/claude/training-server.md` | !training, Szenarien, Granaten-Trainer, Plugin-Befehle, Bot-Level, Kalibrierung |
| `docs/claude/steam-sync.md` | GCPD, Share-Codes, Steam-Login/Token-Refresh, QR |
| `docs/claude/web-auth.md` | Login, Passwort-Hash, Public Routes |
| `docs/claude/testing.md` | Regression-Fixtures, Recorder, Test-Fallen |
| `docs/claude/ops-server.md` | Unraid, Docker-Deploy, CS2-Container, Waechter, NUT/USV, Lüfter, RAM, docker.img |
| `docs/claude/moments-3d.md` | 3D-Szene/Player, Szenen-Cache, demo_cache, Map-/Modell-Export, Animationen, Aim-/Positions-Profil |
| `docs/claude/layout.md` | Vollständige Modulliste mit Zeilenzahlen |
