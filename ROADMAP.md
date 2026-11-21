# CS2 Coach — Roadmap

> Ziel: Das beste CS2-Coaching-Tool, das es je gab. Nicht nur Zahlen zeigen —
> dem Spieler sagen WAS er trainieren soll, WIE, und ob es WIRKT.

Stand: 2026-11-21. Die erste Fassung (August 2026, Phasen 1–8) steht in der Git-Historie; fast alles daraus ist gebaut oder bewusst verworfen.

---

## Leitregeln

1. **Rat nur mit Beleg** — jede Empfehlung kommt aus `evidence.py` (Effektgröße + Konfidenzintervall + Verdict). Ist der Vergleich unentschieden, wird die Zahl gezeigt und nichts daraus abgeleitet. *(Ersetzt das alte „Jede Zahl muss eine Empfehlung haben“ — das erzeugte Rat aus Rauschen.)*
2. **Ergebnis ist kein Hebel** — Survival, K/D, ADR, KAST, Siegquote zeigen, *wie* es lief, nicht *was* zu ändern ist.
3. **Ein Fokus statt fünfzig Metriken** — Startseite zeigt Mission + nächsten Befehl.
4. **Fortschritt sichtbar machen** — aber nur, wenn er belastbar ist.
5. **Kein Blame** — „hier kannst du ansetzen“, nie „du bist schlecht“.
6. **Zero-Effort** — Demos, Analyse, Nachbesprechung und Briefing kommen von allein.
7. **Selbst gehostet, keine Subscription.**

---

## Gebaut

| Bereich | Was | Wo |
|---|---|---|
| Kern-Schleife | Auto-Sync per Share-Code/GCPD → Analyse → KI-Nachbesprechung → Discord/Telegram; Folder-Watch | `sync`, `sharecode`, `report_ai`, `/watch` |
| Startseite | Premier-Rating, Mission, letztes Server-Training + nächster Befehl, letzte Matches | `/` |
| Tages-Briefing & Ziele | Ziel mit Vorher/Nachher, tägliche Nachricht | `/daily`, `/goals` |
| Hebel-Suche | misst bei jedem Sync, was Siege von Niederlagen trennt — auch Duelle und Tode gegen die Lobby an derselben Stelle | `/hebel` |
| Rundenwert | Siegwahrscheinlichkeit je Rundenzustand, Duellwert, teuerste Tode mit Sprungmarke | `winprob`, `duel_value`, `costly_moments` |
| Live-Coach | GSI: frühe Granate im Full Buy, Match-Plan | `/live` |
| Server-Training | `!training` (Duelle, Situation, Retake, Halten; Start 3 s vor dem Tod), `!granaten`, `!eichen`, Bot-Level 0–5, eigene Lineups, Trainingseffekt | `/training`, `/lineups` |
| Server-Bestenliste | Elo, Rekorde, Ingame-Befehle, Spieler der Woche | `/server` |
| Wochenrückblick | letzte Woche gegen die 4 davor | `weekly` |
| Analyse-Seiten | Deaths, Duelle, Clutches, Zonen, Waffen, Utility, Mechanik, Muskelgedächtnis, Economy, Pistol, CT/T, Maps, Rollen, Tilt, Sessions, Gegner/Nemesis/Scout/Prognose, Team/Teammates, Highlights, Habits, Digest, Achievements, Bookmarks, Map-Vorbereitung | Sidebar |
| Werkzeuge | KI-Chat, 2D-Viewer, Warm-up, Practice-Server-Configs, CSV-Export, Share-Links, Systemstatus | Sidebar |

---

## Bewusst verworfen

- **Skill-Benchmarks gegen Ränge** („Gold Nova“, FACEIT-Level) — feste Referenzwerte ohne Bezug zum eigenen Spiel; von der Startseite entfernt.
- **Trend-Alerts mit festen Schwellen** — ersetzt durch die Hebel-Suche, die nur belastbare Änderungen meldet.
- **Tipps aus Schwellen auf Analyse-Seiten** — im November 2026 entfernt bzw. auf `evidence.py` umgestellt (`d8e34f0`, `e1889e8`).
- **`/calendar`, `/momentum`** — doppelt mit `/sessions` und `/tilt`.
- **Obsidian-Graph mit graphify** — 2026-10-14 entfernt.

---

## Offen

| Was | Warum | Aufwand |
|---|---|---|
| **app.py weiter zerlegen** | ~9.850 Zeilen; 39 tote Variablen laut pyflakes. Jede Seite, die angefasst wird, in ein eigenes Modul ziehen. | laufend |
| **Mehr Matches** | Viele Vergleiche sind „noch nicht entscheidbar“ (Counter-Strafe: ab ca. 27 Matches). Kein Code — nur Spielen und Sync laufen lassen. | – |
| **FACEIT-API** | Elo-Verlauf und FACEIT-Demos (die enthalten meist Voice, Valve-MM-Demos nie). | mittel |
| **Clip-Integration** (Medal/Outplayed/OBS) | teuerste Tode und Highlights als Clip statt nur Demo-Tick. | hoch |
| **PDF-Export** | Nachbesprechung als Datei. Geringer Nutzen, solange Discord/Telegram/Vault laufen. | niedrig |
| **PWA / Push** | weitgehend durch Telegram/Discord abgedeckt. | niedrig |
| **Englische UI** | 710 Übersetzungen vorhanden; zurückgestellt, bis ausdrücklich gewünscht. | mittel |
