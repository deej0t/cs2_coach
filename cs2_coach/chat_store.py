"""Speicherung der KI-Chat-Sitzungen im Obsidian-Vault.

Der Chat war bisher zustandslos: der Verlauf lebte in einer Variable im
Browser und war nach einem Reload verloren. Damit ging jede Erkenntnis aus
einem Coaching-Gespraech verloren, sobald der Tab zuging.

Sitzungen liegen als JSON unter <vault>/<subfolder>/chats/ und zusaetzlich
als Markdown daneben, damit sie in Obsidian lesbar und durchsuchbar sind -
das ist der Zweck des Vaults.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path

# Laengenbegrenzung fuer den abgeleiteten Titel in der Uebersicht.
TITLE_MAX = 60

# Aeltere Sitzungen bleiben erhalten; die Liste wird nur fuer die Anzeige
# begrenzt, damit die Uebersicht bei vielen Gespraechen nutzbar bleibt.
LIST_LIMIT = 200

_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")


def chats_dir(vault_path: str, subfolder: str = "CS2-Coach") -> Path | None:
    if not vault_path:
        return None
    return Path(vault_path) / subfolder / "chats"


def _safe_path(vault_path: str, subfolder: str, session_id: str) -> Path | None:
    """Pfad zu einer Sitzung, oder None bei ungueltiger ID.

    Die ID kommt aus einer HTTP-Anfrage; ohne Pruefung liesse sich ueber
    Pfadanteile aus dem Verzeichnis ausbrechen.
    """
    if not _ID_RE.match(session_id or ""):
        return None
    d = chats_dir(vault_path, subfolder)
    return (d / f"{session_id}.json") if d else None


def derive_title(messages: list[dict]) -> str:
    """Titel aus der ersten Nutzerfrage ableiten."""
    for m in messages:
        if m.get("role") == "user":
            text = " ".join((m.get("content") or "").split())
            if text:
                return text[:TITLE_MAX] + ("…" if len(text) > TITLE_MAX else "")
    return "Ohne Titel"


def save_session(vault_path: str, subfolder: str, messages: list[dict],
                 session_id: str | None = None) -> dict | None:
    """Sitzung anlegen oder aktualisieren. Gibt die Metadaten zurueck."""
    d = chats_dir(vault_path, subfolder)
    if d is None or not messages:
        return None
    d.mkdir(parents=True, exist_ok=True)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    path = _safe_path(vault_path, subfolder, session_id) if session_id else None
    created = now
    if path is not None and path.exists():
        try:
            created = json.loads(path.read_text(encoding="utf-8")).get("created", now)
        except Exception:
            pass
    else:
        session_id = uuid.uuid4().hex[:12]
        path = d / f"{session_id}.json"

    record = {
        "id": session_id,
        "title": derive_title(messages),
        "created": created,
        "updated": now,
        "messages": messages,
    }
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    _write_markdown(d, record)

    return {k: record[k] for k in ("id", "title", "created", "updated")} | {
        "message_count": len(messages)
    }


def _write_markdown(d: Path, record: dict) -> None:
    """Lesbare Fassung fuer Obsidian - der Vault soll durchsuchbar bleiben."""
    lines = [
        "---",
        f"title: {json.dumps(record['title'], ensure_ascii=False)}",
        f"created: {record['created']}",
        f"updated: {record['updated']}",
        "tags: [cs2-coach, ki-chat]",
        "---",
        "",
        f"# {record['title']}",
        "",
    ]
    for m in record["messages"]:
        who = "Du" if m.get("role") == "user" else "Coach"
        lines.append(f"**{who}:**")
        lines.append("")
        lines.append((m.get("content") or "").strip())
        lines.append("")
    try:
        (d / f"{record['id']}.md").write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass  # Markdown ist Beiwerk; die JSON-Fassung zaehlt


def list_sessions(vault_path: str, subfolder: str = "CS2-Coach") -> list[dict]:
    """Alle Sitzungen, neueste zuerst."""
    d = chats_dir(vault_path, subfolder)
    if d is None or not d.exists():
        return []

    out = []
    for f in d.glob("*.json"):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "id": r.get("id", f.stem),
            "title": r.get("title", "Ohne Titel"),
            "created": r.get("created", ""),
            "updated": r.get("updated", ""),
            "message_count": len(r.get("messages", [])),
        })
    out.sort(key=lambda s: s.get("updated", ""), reverse=True)
    return out[:LIST_LIMIT]


def load_session(vault_path: str, subfolder: str, session_id: str) -> dict | None:
    path = _safe_path(vault_path, subfolder, session_id)
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def delete_session(vault_path: str, subfolder: str, session_id: str) -> bool:
    path = _safe_path(vault_path, subfolder, session_id)
    if path is None or not path.exists():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    md = path.with_suffix(".md")
    if md.exists():
        try:
            md.unlink()
        except OSError:
            pass
    return True
