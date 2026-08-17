"""AI Coach Chat — Gemini and Ollama backends for CS2 coaching conversations."""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Generator


def build_player_context(exports: list[dict], player_name: str = "") -> str:
    """Build a concise player stats summary as LLM context.

    Takes the flattened exports list from _get_exports() and produces
    a text block the LLM can reference when answering questions.
    """
    if not exports:
        return "Keine Spieldaten vorhanden."

    n = len(exports)
    last5 = exports[:5]

    # Averages
    avg = lambda key: round(sum(e.get(key, 0) for e in exports) / n, 2)
    avg5 = lambda key: round(sum(e.get(key, 0) for e in last5) / len(last5), 2) if last5 else 0

    total_kills = sum(e.get("kills", 0) for e in exports)
    total_deaths = sum(e.get("deaths", 0) for e in exports)
    wins = sum(1 for e in exports if e.get("result") == "Sieg")
    losses = sum(1 for e in exports if e.get("result") == "Niederlage")
    draws = n - wins - losses
    wr = round(wins / n * 100, 1)

    # Per-map stats
    by_map: dict[str, list] = {}
    for e in exports:
        by_map.setdefault(e.get("map", "?"), []).append(e)

    map_lines = []
    for m, matches in sorted(by_map.items(), key=lambda x: -len(x[1])):
        mn = len(matches)
        mw = sum(1 for e in matches if e.get("result") == "Sieg")
        mr = round(sum(e.get("rating", 0) for e in matches) / mn, 2)
        mk = round(sum(e.get("kd", 0) for e in matches) / mn, 2)
        map_lines.append(f"  {m}: {mn} Spiele, WR {round(mw/mn*100)}%, Rating {mr}, K/D {mk}")

    # Recent form (last 5)
    recent_results = [e.get("result", "?")[0] for e in last5]  # S, N, U

    # Weapon breakdown from last matches
    weapon_kills: dict[str, int] = {}
    for e in exports:
        for w, count in e.get("weapon_kills", {}).items():
            weapon_kills[w] = weapon_kills.get(w, 0) + count

    top_weapons = sorted(weapon_kills.items(), key=lambda x: -x[1])[:5]
    wpn_line = ", ".join(f"{w}: {c} Kills" for w, c in top_weapons) if top_weapons else "keine Daten"

    # Streak
    streak = 0
    streak_type = ""
    for e in exports:
        r = e.get("result", "")
        if streak == 0:
            streak_type = r
            streak = 1
        elif r == streak_type:
            streak += 1
        else:
            break

    ctx = f"""=== CS2 SPIELER-PROFIL: {player_name or 'Unbekannt'} ===

GESAMT ({n} Spiele):
  Bilanz: {wins}W / {losses}L / {draws}D (Win-Rate: {wr}%)
  Kills/Deaths: {total_kills}/{total_deaths} (K/D: {round(total_kills/max(total_deaths,1), 2)})
  Avg Rating: {avg('rating')}, Avg ADR: {avg('adr')}, Avg HS%: {avg('hs_pct')}%
  KAST: {avg('kast')}%, Counter-Strafe: {avg('counter_strafe')}%
  Crosshair Placement: {avg('crosshair_placement')} Grad
  Utility/Runde: {avg('utility_per_round')}
  Survival Rate: {avg('survival_rate')}%
  Opening Duels: {sum(e.get('opening_kills',0) for e in exports)}W / {sum(e.get('opening_deaths',0) for e in exports)}L

LETZTE 5 SPIELE:
  Ergebnisse: {' '.join(recent_results)}
  Rating: {avg5('rating')}, ADR: {avg5('adr')}, K/D: {avg5('kd')}
  HS%: {avg5('hs_pct')}%, KAST: {avg5('kast')}%
  Aktuelle Serie: {streak}x {streak_type}

MAP-STATS:
{chr(10).join(map_lines)}

TOP-WAFFEN: {wpn_line}
"""
    return ctx.strip()


SYSTEM_PROMPT = """Du bist ein erfahrener CS2 Coach. Du analysierst die Spieldaten des Spielers und gibst konkrete, actionable Tipps.

Regeln:
- Antworte auf Deutsch
- Sei direkt und konkret — keine generischen Tipps
- Beziehe dich immer auf die tatsaechlichen Zahlen des Spielers
- Wenn etwas gut ist, sage das. Wenn etwas schlecht ist, sage konkret was zu tun ist
- Halte Antworten kurz (max 200 Woerter) wenn nicht nach Details gefragt wird
- Nutze CS2-spezifische Terminologie (Peek, Trade, KAST, ADR, etc.)
- Du kannst nur analysieren was in den Daten steht — sage ehrlich wenn du etwas nicht weisst

Der Spieler kann dir Fragen stellen wie:
- "Warum sterbe ich so oft?"
- "Was soll ich trainieren?"
- "Wie ist meine Performance auf Mirage?"
- "Bin ich besser geworden?"
"""


def stream_gemini(
    messages: list[dict],
    context: str,
    api_key: str,
    model: str = "gemini-2.0-flash",
) -> Generator[str, None, None]:
    """Stream a response from Google Gemini API.

    messages: list of {"role": "user"|"assistant", "content": "..."}
    """
    # Build Gemini request
    contents = []

    # System instruction + context as first user message context
    system_text = SYSTEM_PROMPT + "\n\n" + context

    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({
            "role": role,
            "parts": [{"text": msg["content"]}],
        })

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
        f":streamGenerateContent?alt=sse&key={api_key}"
    )

    payload = json.dumps({
        "system_instruction": {"parts": [{"text": system_text}]},
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 4096,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    candidates = data.get("candidates", [])
                    if candidates:
                        candidate = candidates[0]
                        parts = candidate.get("content", {}).get("parts", [])
                        for part in parts:
                            text = part.get("text", "")
                            if text:
                                yield text
                        # Check if response was truncated
                        finish = candidate.get("finishReason", "")
                        if finish == "MAX_TOKENS":
                            yield "\n\n*(Antwort war zu lang und wurde gekuerzt. Frag 'weiter' fuer den Rest.)*"
                except json.JSONDecodeError:
                    continue
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        yield f"\n\n**Fehler:** Gemini API {e.code} — {body[:200]}"
    except Exception as e:
        yield f"\n\n**Fehler:** {e}"


def stream_ollama(
    messages: list[dict],
    context: str,
    ollama_url: str = "http://192.168.188.71:11434",
    model: str = "llama3.1:8b",
) -> Generator[str, None, None]:
    """Stream a response from Ollama API.

    messages: list of {"role": "user"|"assistant", "content": "..."}
    """
    # Build Ollama chat request with system prompt + context
    ollama_messages = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + context},
    ]
    for msg in messages:
        ollama_messages.append({
            "role": msg["role"],
            "content": msg["content"],
        })

    url = f"{ollama_url.rstrip('/')}/api/chat"

    payload = json.dumps({
        "model": model,
        "messages": ollama_messages,
        "stream": True,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    text = data.get("message", {}).get("content", "")
                    if text:
                        yield text
                    if data.get("done", False):
                        break
                except json.JSONDecodeError:
                    continue
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # Model not found — check what's available
            status = check_ollama_status(ollama_url)
            available = status.get("models", [])
            if available:
                model_list = ", ".join(f"`{m}`" for m in available[:5])
                yield (f"\n\n**Fehler:** Modell `{model}` nicht gefunden. "
                       f"Verfuegbare Modelle: {model_list}. "
                       f"Aendere das Modell in den Einstellungen oder "
                       f"installiere es mit `ollama pull {model}`.")
            else:
                yield (f"\n\n**Fehler:** Modell `{model}` nicht gefunden. "
                       f"Installiere es mit: `ollama pull {model}`")
        else:
            body = e.read().decode("utf-8", errors="replace")[:200]
            yield f"\n\n**Fehler:** Ollama API {e.code} — {body}"
    except urllib.error.URLError as e:
        yield f"\n\n**Fehler:** Ollama nicht erreichbar ({ollama_url}) — {e.reason}"
    except Exception as e:
        yield f"\n\n**Fehler:** {e}"


def check_ollama_status(ollama_url: str = "http://192.168.188.71:11434") -> dict:
    """Check if Ollama is running and list available models."""
    url = f"{ollama_url.rstrip('/')}/api/tags"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CS2Coach/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = [m.get("name", "") for m in data.get("models", [])]
            return {"online": True, "models": models}
    except Exception:
        return {"online": False, "models": []}
