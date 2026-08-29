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


def call_gemini(
    prompt: str,
    context: str,
    api_key: str,
    model: str = "gemini-2.0-flash",
) -> str:
    """Non-streaming Gemini call. Returns full response text."""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
        f":generateContent?key={api_key}"
    )
    payload = json.dumps({
        "system_instruction": {"parts": [{"text": context}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 4096},
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts)
    except Exception as e:
        return f"**Fehler:** {e}"


def call_ollama(
    prompt: str,
    context: str,
    ollama_url: str = "http://192.168.188.71:11434",
    model: str = "llama3.1:8b",
) -> str:
    """Non-streaming Ollama call. Returns full response text."""
    url = f"{ollama_url.rstrip('/')}/api/chat"
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": context},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
            return data.get("message", {}).get("content", "")
    except Exception as e:
        return f"**Fehler:** {e}"


PRACTICE_PLAN_SYSTEM = """Du bist ein erfahrener CS2 Coach. Du analysierst Spielerdaten und passt die Practice-Server-Configs individuell an.

Du bekommst die Spielerdaten (K/D pro Map, Death-Spots mit Häufigkeit, Waffen, HS%, etc.).
Deine Aufgabe: Für JEDE Map konkrete Config-Anpassungen vorschlagen, die der Practice-Server übernimmt.

WICHTIG — Antworte EXAKT in diesem JSON-Format (kein Markdown, kein Code-Block, nur reines JSON):
{
  "analysis": "2-3 Sätze: Was sind die größten Schwächen?",
  "map_overrides": {
    "MapName": {
      "prefire": {"bot_count": 5, "bot_difficulty": 3, "priority_spots": [1, 3, 2]},
      "retake": {"bot_count": 4, "bot_difficulty": 2},
      "spray":  {"bot_count": 5, "spray_spacing": 100},
      "challenge": {"bot_count": 6, "bot_difficulty": 3, "challenge_time": 45}
    }
  },
  "focus_mode": {"MapName": "prefire"},
  "routine": [
    {"step": 1, "duration": "X Min", "activity": "Name", "mode": "prefire/retake/spray/challenge/utility/warmup", "map": "MapName oder null", "desc": "Was genau tun"}
  ],
  "total_time": "XX Min",
  "tips": ["Tipp 1", "Tipp 2", "Tipp 3"]
}

Config-Parameter pro Modus:
- prefire:   bot_count (1-10), bot_difficulty (0-3), priority_spots (1-indexed Liste der wichtigsten Spots)
- retake:    bot_count (1-8), bot_difficulty (0-3), priority_spots
- spray:     bot_count (3-10, default 5), spray_spacing (50-200 Units, default 100)
- challenge: bot_count (1-8), bot_difficulty (0-3), challenge_time (30-90 Sekunden)

bot_difficulty: 0=Leicht, 1=Mittel, 2=Schwer, 3=Experte
priority_spots: Sortiert nach Wichtigkeit, 1-indexed. Z.B. [3, 1, 5] = Spot 3 zuerst üben

Regeln:
- map_overrides: NUR für Maps die in den Daten vorkommen
- Schwache Maps (K/D < 1.0): mehr Bots, niedrigere Difficulty zum Üben
- Starke Maps: weniger Bots, höhere Difficulty zum Feinschliff
- Spots mit vielen Deaths (>= 5x) als priority_spots markieren
- challenge_time kürzer bei guten Spielern, länger bei schwachen
- spray_spacing enger (70-80) für Anfänger, weiter (120-150) für Fortgeschrittene
- routine: 5-7 Schritte, 30-45 Min total
- tips: genau 3 konkrete Tipps
- Antworte auf Deutsch
- NUR JSON, keine anderen Zeichen davor oder danach"""


def build_practice_context(maps_data: list[dict]) -> str:
    """Build LLM context string from practice map data."""
    lines = ["=== PRACTICE-DATEN DES SPIELERS ===\n"]

    total_k = sum(m["kills"] for m in maps_data)
    total_d = sum(m["deaths"] for m in maps_data)
    lines.append(f"GESAMT: {total_k} Kills, {total_d} Deaths, K/D {round(total_k / max(total_d, 1), 2)}")
    lines.append(f"Maps analysiert: {len(maps_data)}\n")

    for m in maps_data:
        lines.append(f"--- {m['name']} ---")
        lines.append(f"  K/D: {m['kd']}, Kills: {m['kills']}, Deaths: {m['deaths']}")
        lines.append(f"  HS-Death%: {m['hs_death_pct']}%, Hotspots: {m['hotspots']}")

        if m.get("spots"):
            lines.append(f"  Bot-Spots ({len(m['spots'])} Stück):")
            for i, s in enumerate(m["spots"][:5]):
                lines.append(
                    f"    {i+1}. {s['count']}x deaths — Gegner: {s['enemy']}, "
                    f"Waffe: {s['weapon']}, HS%: {s['hs_pct']}%"
                )

        if m.get("top_death_weapons"):
            wpns = ", ".join(f"{w['name']}({w['count']}x)" for w in m["top_death_weapons"])
            lines.append(f"  Tod durch: {wpns}")

        if m.get("top_enemies"):
            enemies = ", ".join(f"{e['name']}({e['count']}x)" for e in m["top_enemies"])
            lines.append(f"  Häufigste Gegner: {enemies}")

        lines.append("")

    lines.append("VERFÜGBARE PRACTICE-MODI:")
    lines.append("  prefire  — Bots eingefroren an Death-Spots")
    lines.append("  retake   — Bots an Hold-Positionen, retake die Site")
    lines.append("  spray    — 5 Bots in Reihe, Spray-Transfer")
    lines.append("  challenge — 45 Sek Timer, Bots schiessen zurück")
    lines.append("  utility  — Granaten-Trajectories, keine Bots")
    lines.append("  warmup   — Aufwärmen mit beweglichen Bots")

    return "\n".join(lines)


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
