"""Versand per Telegram-Bot.

Der Nutzer traegt nur das Bot-Token ein (von @BotFather) und schickt dem
Bot einmal /start. Die Chat-ID muss er nicht heraussuchen: sie steht in
getUpdates und wird in .telegram_chat.json neben der Config gemerkt.

Bewusst nicht in config.yaml: settings_save() schreibt die Config aus dem
Speicher der WebUI zurueck und haette eine nachtraeglich eingetragene
Chat-ID beim naechsten Speichern wieder entfernt.

Telegram kennt kein Discord-Markdown. Die Texte gehen deshalb als HTML
(parse_mode=HTML) raus; **fett** und `code` werden umgesetzt.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

API = "https://api.telegram.org/bot{token}/{method}"

#: Telegram erlaubt 4096 Zeichen je Nachricht; Luft fuer die HTML-Tags.
LIMIT = 3800


def _state_file(config_path: Path) -> Path:
    return Path(config_path).parent / ".telegram_chat.json"


def to_html(text: str) -> str:
    """Discord-Markdown in Telegram-HTML."""
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", out)
    # Kursiv nur fuer ganze Zeilen: "_..._" mitten im Text trifft sonst
    # Namen wie de_mirage.
    out = re.sub(r"(?m)^_([^_\n].*)_$", r"<i>\1</i>", out)
    return out


def find_chat_id(token: str, config_path: Path, get=None) -> str:
    """Gemerkte Chat-ID, sonst aus der letzten Nachricht an den Bot. "" wenn keine."""
    state = _state_file(config_path)
    try:
        saved = json.loads(state.read_text(encoding="utf-8"))
        if saved.get("token_tail") == token[-6:] and saved.get("chat_id"):
            return str(saved["chat_id"])
    except (OSError, ValueError):
        pass

    if get is None:
        import requests
        get = requests.get
    try:
        r = get(API.format(token=token, method="getUpdates"), timeout=15)
        updates = r.json().get("result") or []
    except Exception:  # noqa: BLE001 - Netzfehler: beim naechsten Mal erneut
        return ""
    for u in reversed(updates):
        msg = u.get("message") or u.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            state.write_text(json.dumps({"chat_id": chat["id"], "token_tail": token[-6:]}),
                             encoding="utf-8")
            return str(chat["id"])
    return ""


def _chunks(text: str, size: int = LIMIT) -> list[str]:
    out, cur = [], ""
    for para in text.split("\n\n"):
        if len(cur) + len(para) + 2 > size and cur:
            out.append(cur)
            cur = ""
        cur = (cur + "\n\n" + para) if cur else para
    if cur:
        out.append(cur)
    return [c[:size] for c in out]


def send(token: str, chat_id: str, text: str, post=None) -> bool:
    """Text in Teilen schicken. True, wenn alle Teile angenommen wurden."""
    if post is None:
        import requests
        post = requests.post
    for part in _chunks(text):
        try:
            r = post(API.format(token=token, method="sendMessage"), timeout=15,
                     json={"chat_id": chat_id, "text": to_html(part), "parse_mode": "HTML",
                           "disable_web_page_preview": True})
        except Exception:  # noqa: BLE001
            return False
        if getattr(r, "status_code", 200) >= 400:
            return False
    return True
