"""Anmelden/Registrieren mit Steam (OpenID 2.0, kein API-Schluessel noetig).

Steam bestaetigt die steamID64; danach:
- Admin-Steam-ID -> Admin, Steam-ID eines Mates -> dieser Mate,
- unbekannt -> Registrierungsanfrage (users.request_access ohne Passwort),
  die der Admin unter /benutzer annimmt. Danach meldet er sich wieder per Steam an.
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

OPENID = "https://steamcommunity.com/openid/login"
NS = "http://specs.openid.net/auth/2.0"
CLAIMED_RE = re.compile(r"^https://steamcommunity\.com/openid/id/(7656\d{13})$")


def auth_url(return_to: str, realm: str) -> str:
    return OPENID + "?" + urlencode({
        "openid.ns": NS, "openid.mode": "checkid_setup", "openid.return_to": return_to, "openid.realm": realm,
        "openid.identity": NS + "/identifier_select", "openid.claimed_id": NS + "/identifier_select"})


def verify(args, return_to: str, post=None) -> str | None:
    """Antwort von Steam bei Steam selbst gegenpruefen (check_authentication). steamID64 oder None."""
    if args.get("openid.mode") != "id_res" or not (args.get("openid.return_to") or "").startswith(return_to):
        return None
    m = CLAIMED_RE.match(args.get("openid.claimed_id", ""))
    if not m:
        return None
    if post is None:
        import requests
        post = requests.post
    data = {k: v for k, v in args.items() if k.startswith("openid.")}
    data["openid.mode"] = "check_authentication"
    try:
        r = post(OPENID, data=data, timeout=10)
        return m.group(1) if "is_valid:true" in r.text else None
    except Exception:  # noqa: BLE001 - Steam nicht erreichbar
        return None


def create_blueprint(base_config, read_base) -> Blueprint:
    from .. import users
    bp = Blueprint("steam_openid", __name__)

    def base_url() -> str:
        cfg = read_base()
        url = (cfg.get("public_url") or "").strip().rstrip("/")
        if url:
            return url
        proto = request.headers.get("X-Forwarded-Proto", request.scheme)
        return f"{proto}://{request.host}"

    @bp.route("/steam-anmelden")
    def start():
        root = base_url()
        return redirect(auth_url(root + url_for("steam_openid.back"), root + "/"))

    @bp.route("/steam-anmelden/zurueck")
    def back():
        from pathlib import Path
        base = Path(base_config())
        sid = verify(request.args, base_url() + url_for("steam_openid.back"))
        if not sid:
            flash("Steam-Anmeldung fehlgeschlagen.", "error")
            return redirect(url_for("login"))
        name = None
        if sid != str(read_base().get("steam_id") or ""):
            name = next((n for n, m in users.load_users(base).items() if m.get("steam_id") == sid), None)
            if name is None:
                pend = next((n for n, p in users.load_pending(base).items() if p.get("steam_id") == sid), None)
                if pend is None:
                    try:
                        pend = users.request_access(base, f"steam{sid[-6:]}", None, sid, "")
                        from .users_page import notify_admin
                        notify_admin(read_base(), f"**CS2 Coach** - neue Registrierung per Steam: **{pend}** "
                                     f"(https://steamcommunity.com/profiles/{sid}) wartet auf Freigabe.")
                    except ValueError as e:
                        flash(str(e), "error")
                        return redirect(url_for("login"))
                return render_template("register.html", done=True)
        session.clear()
        session["authenticated"] = True
        session["user"] = name
        session.permanent = True
        return redirect(url_for("index"))

    return bp
