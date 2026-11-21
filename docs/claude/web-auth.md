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
