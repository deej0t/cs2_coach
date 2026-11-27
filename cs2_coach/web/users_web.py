"""Mehrbenutzer in der WebUI: wer ist angemeldet, welche Config gilt, was darf er.

app.py arbeitet an ~370 Stellen mit einem gemeinsamen ``cfg``-Dict. Statt das
alles umzuschreiben, ist ``cfg`` ein ``ConfigProxy``: in einer Anfrage eines
Mates zeigt er auf dessen zusammengesetzte Config (users.merged_config), sonst
auf die Admin-Config wie bisher. ``config_path()`` liefert entsprechend den
Pfad, neben dem die Zustandsdateien des Spielers liegen.

Mates sehen und schreiben nie die Admin-Config: ``write_config`` speichert fuer
sie nur ihre eigenen Schluessel (users.save_overlay).
"""

from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path

from flask import g, has_request_context, session

from .. import users

#: Nur der Admin: Einstellungen der Instanz, Server, Training, Status, Benutzerverwaltung
ADMIN_ONLY_PREFIXES = ("/settings", "/status", "/benutzer", "/training", "/live", "/server/reset",
                       "/api/steam-gcpd-debug", "/api/scan-folder", "/analyze-path", "/watch", "/api/watch")
# /api/steam-qr-*, /api/steam-status, /api/steam-logout: erlaubt - jeder verbindet sein eigenes Konto
ALLOWED_FOR_MATES = ("/settings/konto",)


def current_user() -> str | None:
    """Angemeldeter Mate oder None (Admin bzw. Auth aus)."""
    if not has_request_context():
        return None
    return session.get("user") or None


def is_admin() -> bool:
    return current_user() is None


# ── Fremde Sicht auf ein gemeinsames Match (?als=<name|admin>) ───────────────
#: Seiten, auf denen eine fremde Sicht erlaubt ist (Match-Seite, ihre Szenen und ihr Replay)
VIEW_AS_ENDPOINTS = ("view_export", "api_replay", "moments_page.clip", "moments_page.moment_list")
_ADMIN = "admin"


def effective_user() -> str | None:
    """Wessen Daten diese Anfrage zeigt: die angefragte fremde Sicht oder der Angemeldete."""
    if has_request_context() and g.get("_view_as") is not None:
        v = g._view_as
        return None if v == _ADMIN else v
    return current_user()


def resolve_view_as(base: Path, base_cfg: dict, endpoint: str | None, args, view_args) -> str | None:
    """``?als=`` pruefen und fuer diese Anfrage setzen. Rueckgabe: Fehlertext oder None.

    Admin darf jede Sicht; ein Mate nur auf Matches, die er selbst gespielt hat
    (er hat einen eigenen Export zur selben Demo).
    """
    want = (args.get("als") or "").strip().lower()
    if not want:
        return None
    if endpoint not in VIEW_AS_ENDPOINTS:
        return "Fremde Sicht nur auf der Match-Seite"
    target = None if want == _ADMIN else want
    if target is not None and target not in users.load_users(base):
        return "Unbekannter Spieler"
    if target == current_user():
        return None
    filename = (view_args or {}).get("filename", "")
    tcfg = users.merged_config(base_cfg, base, target) if target else base_cfg
    demo = next((d for d, f in users.demo_index(tcfg).items() if f == filename), None)
    if demo is None:
        return "Export nicht gefunden"
    if not is_admin():
        own = users.merged_config(base_cfg, base, current_user())
        if demo not in users.demo_index(own):
            return "Nur Matches, die du selbst gespielt hast"
    g._view_as = want if target else _ADMIN
    return None


def config_path(base: Path) -> Path:
    name = effective_user()
    return users.user_config_path(base, name) if name else Path(base)


class ConfigProxy(MutableMapping):
    """``cfg`` fuer app.py: je Anfrage die Config des angemeldeten Spielers."""

    def __init__(self, base: dict, base_path):
        self._base = base
        self._base_path = base_path      # Callable -> Path der Admin-Config

    def _target(self) -> dict:
        name = effective_user()
        if name is None:
            return self._base
        cached = g.get("_user_cfg")
        if cached is None or cached.get("user") != name:
            cached = users.merged_config(self._base, Path(self._base_path()), name)
            g._user_cfg = cached
        return cached

    def __getitem__(self, k):
        return self._target()[k]

    def __setitem__(self, k, v):
        self._target()[k] = v

    def __delitem__(self, k):
        del self._target()[k]

    def __iter__(self):
        return iter(self._target())

    def __len__(self):
        return len(self._target())

    def copy(self) -> dict:
        return dict(self._target())

    @property
    def base(self) -> dict:
        """Die Admin-Config selbst (fuer Hintergrundlaeufe ueber alle Spieler)."""
        return self._base


def load(base: Path, read_base) -> dict:
    """``load_config()`` fuer die aktuelle Anfrage: Admin-Config oder die des Mates."""
    data = read_base()
    name = effective_user()
    return users.merged_config(data, Path(base), name) if name else data


def write(base: Path, data: dict, write_base) -> None:
    """Config speichern: Admin wie bisher, Mate nur seine eigenen Schluessel."""
    name = current_user()
    if name is None:
        write_base(data)
    else:
        users.save_overlay(Path(base), name, dict(data))


def blocked_for_mate(path: str) -> bool:
    if is_admin():
        return False
    if any(path.startswith(p) for p in ALLOWED_FOR_MATES):
        return False
    return any(path.startswith(p) for p in ADMIN_ONLY_PREFIXES)


def guard(base: Path, base_cfg: dict, request):
    """Alle Pruefungen eines angemeldeten Aufrufs: Fehlertext oder None.

    Mates: keine Admin-Bereiche. ``?als=``: fremde Sicht nur erlaubt wie in
    resolve_view_as, und dabei nur lesen.
    """
    if blocked_for_mate(request.path):
        return "Nur fuer den Admin"
    err = resolve_view_as(base, base_cfg, request.endpoint, request.args, request.view_args)
    if err:
        return err
    if g.get("_view_as") is not None and request.method != "GET":
        return "Fremde Sicht: nur ansehen"
    return None
