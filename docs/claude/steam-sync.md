---
tags: [cs2-coach, claude-context]
---
# Auto-Sync, Demo-Download, Steam-Login
← [[CLAUDE]] · Detailnotiz, wird nur bei Bedarf gelesen.

## Auto-Sync & Demo Download (`cs2_coach/sharecode.py`)
- **Three independent sources**: Steam API (share codes), GCPD (Valve download), local replays folder
- **Share code `match_id` ≠ demo filename ID** — these are different ID systems (GC matchid vs reservation ID). Never try to correlate them.
- **GCPD page loads data via AJAX**, not in the initial HTML. Must extract `g_sGcContinueToken` and `g_sessionID` (can use single OR double quotes), then make AJAX calls with `X-Requested-With: XMLHttpRequest` header. Response is JSON `{"success":true, "html":"...", "continue_token":"..."}`.
- **GCPD tabs**: current competitive matches live under `matchhistorycompetitivepermap` ("Gewertete Wettkampfspiele"). `matchhistorycompetitive` is the old CS:GO history and stays empty for CS2 players. Also query `matchhistorypremier` and `matchhistorywingman`.
- **`continue_token` is a global time cursor, not a per-mode counter.** An empty page only means "no match of this mode in this time window" — never stop there. Paginate up to 12 pages and abort after 6 consecutive empty ones.
- **GCPD has a ~2-3 day delay** — very recent matches may not appear yet. Valve also expires demo links fast: of 140 listed Premier matches only 3 still had a download link.
- **`.dem.info` sidecar** contains the real match timestamp (protobuf field 2 = Unix timestamp). GCPD downloads create this from the GCPD date. Without it, parser falls back to file mtime (= wrong date).
- **Steam login** uses `IAuthenticationService` API (2023+), NOT the old `/login/dologin/` endpoint. Session persisted via pickle to `.steam_session`.
- **Steam issues two tokens with very different lifetimes.** Measured 2026-09-26: `steamLoginSecure` (access, `aud=["web"]`) lasts exactly **24 h**; `steamRefresh_steam` (`aud=["web","renew","derive"]`) lasts **~210 days**. Never delete the jar just because the access token expired — that throws away a seven-month credential over a one-day one, and was the reason for daily re-logins.
- **Renewal** (`refresh_steam_session()`): POST `/jwt/ajaxrefresh` with `redir` + `sessionid` → `{login_url, nonce, auth, steamID}`, then POST that `login_url` → fresh `steamLoginSecure`. **Browser headers are mandatory** (`Origin`, `Referer`, `X-Requested-With`); without them Steam answers 403. `GenerateAccessTokenForApp` does *not* work for these web-scoped refresh tokens — it returns `x-eresult: 15` (AccessDenied).
- The `transfer_info` POSTs in `_finalize_steam_login()` return `{"result":8}` (InvalidParam) and set no token; `steamLoginSecure` comes from the `access_token`, not from them. They are decorative — don't build renewal on them.
- `.steam_session` lives **next to the config** (`_session_file()`, same derivation as `.secret_key`). It used to sit next to the source, i.e. `/app` in the container — Docker's writable layer, discarded on every container recreate, so any stack change logged the user out. `_migrate_legacy_session()` moves an existing file once.
- A network error must never clear the session — only a refused *and* unrenewable session may.
- **QR login**: `BeginAuthSessionViaQR` + poll. Do **not** send `device_details` — it is a protobuf submessage and form-encoding it as a JSON string returns HTTP 400 with an HTML error page. The SteamID is not returned; read it from the JWT `sub` claim (pad the base64 first). Steam rotates the challenge URL while polling — pass the new one through and let the UI redraw.
