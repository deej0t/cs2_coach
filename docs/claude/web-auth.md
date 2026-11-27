---
tags: [cs2-coach, claude-context]
---
# Web-Auth
← [[CLAUDE]] · Detailnotiz, wird nur bei Bedarf gelesen.

## Web Auth
- `CS2COACH_PASSWORD` (env, plaintext, hashed at startup) takes precedence over `auth.password_hash` in config.yaml. No password set = auth fully inactive.
- The password hash is read from the **config file** (cached on its mtime), not from the in-memory cfg dict — otherwise gunicorn workers disagree and the login loops endlessly. Costs one `stat()` per request; password changes take effect without restart.
- `settings_save()` must start from `dict(cfg)` so unknown keys (like the auth hash) survive a settings save.
- Public routes: `/share/<file>` and `/static`. `/api/*` answers 401 instead of redirecting; the frontend must translate that into "session expired", not "connection error".
- Only remember a `next` target for GET navigations that want HTML (otherwise `/favicon.ico` becomes the login target). Login locks per IP for 5 minutes after 5 failed attempts.

## Mates (Mehrbenutzer, 2026-11-27)
- `users.py` (Kern) + `web/users_web.py` (je Anfrage) + `web/users_page.py` (`/benutzer` Admin, `/settings/konto` Mate). Ohne `.users.json` ändert sich nichts. Mate-Logins brauchen ein Admin-Passwort (ohne ist Auth aus).
- Login: Feld „Name“ leer/`admin` = Admin, sonst Mate (`session["user"]`). `users.ACTIVE` (ContextVar) wird je Anfrage gesetzt – u. a. Steam-Sitzung je Spieler (`sharecode._session_file` → `users/<name>/.steam_session`; Altdaten-Umzug/Logout der alten Datei nur Admin).
- `cfg` in app.py ist ein `ConfigProxy`: Mate = Admin-Config **ohne** `USER_KEYS` + `users/<name>/config.yaml`, Vault = `users/<name>/vault`. Schreiben (`_write_config`) speichert für Mates nur `USER_KEYS` – Admin-Schlüssel sieht/überschreibt ein Mate nie (Test `test_users.py`).
- Zustand je Spieler neben seiner Config (Hebel, Briefing, Aim-Profil, KI-Berichte, Telegram-Chat, Szenen-Cache). Gemeinsam (`users.shared_dir`): Demo-Caches (winprob, aim_cache), Maps/Modelle, Server, Training, GSI, Map-Vote.
- Admin-Bereiche (`users_web.ADMIN_ONLY_PREFIXES`) leiten Mates um. Fremde Sicht `?als=<name|admin>` nur auf Match-Seite/Szenen/Replay (`VIEW_AS_ENDPOINTS`), Mate nur für selbst gespielte Matches (eigener Export zur selben Demo), dabei nur GET. Prüfung gesammelt in `users_web.guard`.
- Sync (`cli sync` → `_sync_mates`): je Mate eigene GCPD-Demos über seine Steam-Sitzung (Steam per QR auf der Share-Code-Seite verbinden), Analyse nur der Demos, in denen er laut gemeinsamem Aim-Cache mitspielt, dann KI (nur mit eigenem Schlüssel), Hebel, Wochenrückblick, Aim-Profil. `cli briefing --send` schickt auch Mates mit eigenem Webhook/Telegram ihr Briefing. Lokal: 27 gemeinsame Demos eines Mates in 16 min analysiert.
- Registrierung `/registrieren` (öffentlich, Köder-Feld, max. 5/IP/5 min, max. 20 offen) → `.users.json` "pending" → Admin nimmt unter /benutzer an (Discord-Nachricht an `discord_webhook`). Profile: `/spieler`, Dashboard/Aim-Profil anderer per `?als=` für alle Angemeldeten (nur GET).
- Steam-Login `web/steam_openid.py` (OpenID 2.0, Gegenprüfung check_authentication): Admin-Steam-ID → Admin, Mate-Steam-ID → Mate, unbekannt → Anfrage `steam<6 Ziffern>` ohne Passwort. Rücksprung-URL aus `public_url`, sonst X-Forwarded-Proto + Host.
- Duo-Ansicht `duo.py` auf /spieler: Siegquote mit/ohne Mate über `compare_rates` (comparisons = Zahl der Mitspieler), nur Info, kein Hebel. Trade-Partner (`trade_table`): Mate tötet deinen Mörder in derselben Runde binnen `TRADE_WINDOW_TICKS`, Abgleich über Gegnername aus `kill_positions` beider Exporte.
- Team-Lineups: `lineups_own.json` liegt in `users.shared_dir` (Ingame-`!speichern` schreibt neben die Admin-Config), /lineups zeigt sie allen mit „von <by>“; löschen/überschreiben nur eigene (by = Ingame-Name bzw. `player_name`), Admin alle.
