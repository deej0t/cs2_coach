"""WebUI: Mates verwalten (/benutzer, nur Admin) und das eigene Konto (/settings/konto, Mates).

Der Admin legt einen Mate mit Name, Passwort und Steam-ID an. Der Mate pflegt
danach selbst, was ihm gehoert (users.MATE_EDITABLE): Steam-Sync (Auth-Code,
letzter Share-Code), Discord/Telegram, KI-Anbieter und -Schluessel, Sprache,
und sein Passwort. Die Schluessel des Admins sieht er nie.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import users
from . import users_web

#: Formularfelder der Kontoseite: (Schluessel, Beschriftung, geheim?)
ACCOUNT_FIELDS = (("player_name", "Anzeigename", False),
                  ("cs2_auth_token", "Steam-Authentifizierungscode (Match-Verlauf)", True),
                  ("last_sharecode", "Letzter Share-Code (ab hier wird geladen)", False),
                  ("discord_webhook", "Discord-Webhook", True),
                  ("briefing_webhook", "Discord-Webhook für das Tages-Briefing", True),
                  ("telegram_bot_token", "Telegram-Bot-Token", True),
                  ("ai_provider", "KI-Anbieter (gemini oder ollama)", False),
                  ("ai_model", "KI-Modell", False),
                  ("gemini_api_key", "Gemini-API-Schlüssel", True),
                  ("ollama_url", "Ollama-Adresse", False),
                  ("language", "Sprache (de/en)", False))


def notify_admin(admin_cfg: dict, text: str) -> None:
    """Neue Registrierung an den Discord-Webhook des Admins melden (falls eingerichtet)."""
    hook = (admin_cfg.get("discord_webhook") or "").strip()
    if not hook:
        return
    try:
        import requests
        requests.post(hook, json={"content": text}, timeout=10)
    except Exception:  # noqa: BLE001 - Registrierung klappt auch ohne Nachricht
        pass


def create_blueprint(base_config) -> Blueprint:
    """``base_config``: Callable -> Pfad der Admin-Config."""
    bp = Blueprint("users_page", __name__)

    @bp.route("/benutzer", methods=["GET", "POST"])
    def admin():
        base = Path(base_config())
        if request.method == "POST":
            action = request.form.get("action")
            name = request.form.get("name", "")
            try:
                if action in ("accept", "reject"):
                    users.decide(base, name, action == "accept")
                    flash(f"{name} " + ("freigeschaltet." if action == "accept" else "abgelehnt."), "success")
                elif action == "delete":
                    users.remove_user(base, name)
                    flash(f"Login von {name} entfernt (seine Daten bleiben unter users/{name}/).", "success")
                else:
                    users.add_user(base, name, request.form.get("password", ""),
                                   request.form.get("steam_id", ""), request.form.get("display", ""))
                    flash(f"Mate {name.strip().lower()} gespeichert.", "success")
            except ValueError as e:
                flash(str(e), "error")
            return redirect(url_for("users_page.admin"))
        mates = users.load_users(base)
        rows = []
        for name, m in sorted(mates.items()):
            exports = users.user_dir(base, name) / "vault" / "CS2-Coach" / "exports"
            over = users.load_overlay(base, name)
            rows.append({"name": name, "display": m.get("display", name), "steam_id": m.get("steam_id", ""),
                         "exports": len(list(exports.glob("*_coach.json"))) if exports.exists() else 0,
                         "sync": bool(over.get("cs2_auth_token") and over.get("last_sharecode"))})
        from . import auth
        return render_template("users_admin.html", rows=rows, pending=users.load_pending(base), config=users_web_cfg(),
                               auth_on=auth.is_enabled(base))

    @bp.route("/settings/konto", methods=["GET", "POST"])
    def account():
        base = Path(base_config())
        name = users_web.current_user()
        if name is None:
            return redirect(url_for("settings"))
        over = users.load_overlay(base, name)
        if request.method == "POST":
            for key, _, secret in ACCOUNT_FIELDS:
                val = request.form.get(key, "").strip()
                if secret and not val:
                    continue        # leer gelassen = unveraendert (Geheimes wird nicht angezeigt)
                over[key] = val
            users.save_overlay(base, name, over)
            pw = request.form.get("new_password", "")
            if pw:
                try:
                    m = users.load_users(base).get(name, {})
                    users.add_user(base, name, pw, m.get("steam_id", ""), m.get("display", ""))
                except ValueError as e:
                    flash(str(e), "error")
                    return redirect(url_for("users_page.account"))
            flash("Gespeichert.", "success")
            return redirect(url_for("users_page.account"))
        fields = [{"key": k, "label": label, "secret": secret, "value": "" if secret else over.get(k, ""),
                   "set": bool(over.get(k))} for k, label, secret in ACCOUNT_FIELDS]
        return render_template("users_account.html", name=name, steam_id=over.get("steam_id", ""),
                               fields=fields, config=users_web_cfg())

    @bp.route("/registrieren", methods=["GET", "POST"])
    def register():
        """Oeffentlich: Mate-Zugang beantragen. Der Admin nimmt unter /benutzer an oder lehnt ab."""
        from . import auth
        base = Path(base_config())
        if not auth.is_enabled(base):
            return redirect(url_for("index"))
        done = False
        if request.method == "POST":
            ip = request.remote_addr or "?"
            if auth.lockout_remaining(ip):
                flash("Zu viele Versuche. Bitte spaeter erneut.", "error")
            elif request.form.get("website"):        # Koeder-Feld: nur Bots fuellen es aus
                done = True
            else:
                auth.record_failure(ip)               # zaehlt jede Anfrage: max. 5 je IP in 5 min
                try:
                    name = users.request_access(base, request.form.get("name", ""), request.form.get("password", ""),
                                                request.form.get("steam_id", ""), request.form.get("display", ""))
                    from .app import _read_base_config
                    notify_admin(_read_base_config(), f"**CS2 Coach** - neue Registrierung: **{name}** wartet auf Freigabe (Mates).")
                    done = True
                except ValueError as e:
                    flash(str(e), "error")
        return render_template("register.html", done=done)


    @bp.route("/spieler")
    def players():
        """Alle Spieler der Instanz: Profil (Dashboard) und Aim-Profil jedes Einzelnen ansehen."""
        from .app import _read_base_config
        base = Path(base_config())
        rows = []
        for name, cfg, _ in users.all_players(_read_base_config(), base):
            rows.append({"als": name or "admin", "display": (users.load_users(base).get(name, {}).get("display")
                                                            if name else None) or cfg.get("player_name") or name or "Admin",
                         "matches": len(users.demo_index(cfg)), "me": name == users_web.current_user()})
        from .. import duo
        duos = duo.duo_table(_read_base_config(), base, users_web.current_user())
        trades = duo.trade_table(_read_base_config(), base, users_web.current_user())
        return render_template("players.html", rows=rows, duos=duos, trades=trades, config=users_web_cfg())

    @bp.route("/profil")
    def profile():
        """Profilseite des (angezeigten) Spielers: Kopf, Kennzahlen, Maps, letzte Matches, Sprünge.

        Folgt ?als= wie Dashboard/Aim-Profil. Reine Beschreibung - keine Hebel (die kommen aus evidence)."""
        from .app import _get_exports
        cfg = users_web_cfg()
        ex = _get_exports(cfg)
        n = len(ex)
        won = sum(1 for e in ex if e.get("result") == "Sieg")
        lost = sum(1 for e in ex if e.get("result") == "Niederlage")

        def avg(k):
            v = [e[k] for e in ex if isinstance(e.get(k), (int, float))]
            return round(sum(v) / len(v), 2) if v else None
        maps: dict[str, dict] = {}
        for e in ex:
            m = maps.setdefault(e.get("map") or "?", {"map": e.get("map") or "?", "n": 0, "w": 0})
            m["n"] += 1
            m["w"] += e.get("result") == "Sieg"
        top = sorted(maps.values(), key=lambda m: -m["n"])[:6]
        for m in top:
            m["pct"] = round(100 * m["w"] / m["n"]) if m["n"] else 0
        sid = str(cfg.get("steam_id") or "")
        stats = {"n": n, "won": won, "lost": lost, "wr": round(100 * won / n) if n else None,
                 "rating": avg("rating"), "kd": avg("kd"), "adr": avg("adr"), "hs": avg("hs_pct")}
        return render_template("profile.html", stats=stats, maps=top, recent=ex[:10], steam_id=sid,
                               own=not request.args.get("als"), config=cfg)

    @bp.app_template_global("match_views")
    def match_views(data: dict) -> list[dict]:
        """Wer hat dieses Match auch ausgewertet? Fuer die Match-Seite: Links auf die anderen Sichten.

        Nur sichtbar, wenn die eigene Sicht dabei ist (Mates: nur selbst gespielte Matches)."""
        if not data or not users.load_users(Path(base_config())):
            return []
        from flask import request as _rq
        from .app import _read_base_config
        demo = ((data.get("match") or {}).get("demo_file")) or ""
        views = users.views_of(_read_base_config(), Path(base_config()), demo) if demo else []
        me = users_web.current_user()
        if not users_web.is_admin() and not any(v["user"] == me for v in views):
            return []
        shown = users_web.effective_user()
        for v in views:
            v["current"] = v["user"] == shown
            v["als"] = "" if v["user"] == me else (v["user"] or "admin")
        return views if len(views) > 1 or _rq.args.get("als") else []

    def users_web_cfg() -> dict:
        from .app import load_config
        return load_config()

    return bp
