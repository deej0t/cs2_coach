# CS2 Coach — Roadmap

> Ziel: Das beste CS2-Coaching-Tool, das es je gab. Nicht nur Zahlen zeigen —
> dem Spieler sagen WAS er trainieren soll, WIE, und ob es WIRKT.

---

## Phase 1 — Dashboard & Benchmarks
*Sofort sichtbarer Mehrwert. Der erste Eindruck entscheidet.*

### 1.1 Dashboard-Startseite
Die Index-Seite wird zum persoenlichen Cockpit:
- **Aktuelle Form** — Letzte 5 Spiele als Ampel (gruen/gelb/rot) mit Trendpfeil
- **Spider-Chart** — Radar-Diagramm mit 6 Achsen: Aim (HS%), Positioning (Survival), Utility, Opening Duels, Consistency (KAST), Impact (ADR)
- **Staerken & Schwaechen** — Top 2 Staerken, Top 2 Schwaechen automatisch erkannt
- **Naechster Fokus** — Ein konkreter Satz: "Fokus diese Woche: Mehr Utility einsetzen (aktuell 1.2/Runde)"
- **Quick-Stats** — Win-Rate, Avg Rating, beste/schlechteste Map, laengste Siegesserie
- **Letzte Session** — Zusammenfassung des letzten Spieltags

### 1.2 Skill-Benchmarks mit Levels
Eigene Stats gegen Referenzwerte einordnen:
```
Dein ADR:      78.4  ████████░░  Gold Nova
Faceit 5:      80+   ████████▓░
Faceit 8:      90+   █████████▓
Pro Average:   95+   ██████████
```
Metriken mit Benchmarks:
- ADR, HS%, KAST%, Rating, Counter-Strafe%, Utility/Runde
- Crosshair Placement (Grad), Opening Duel Winrate, Trade Rate
- Benchmarks fuer: Silber, Gold Nova, MG, DMG/LE, Faceit 1-3, Faceit 4-6, Faceit 7-10, Pro

### 1.3 Map-Performance-Karten
Pro Map eine visuelle Karte statt nur Tabelle:
- Winrate-Ring, Avg Rating, K/D, Trend (letzter Monat vs. Gesamtschnitt)
- Empfehlung: "Deine beste Map — weiter spielen" / "Meiden oder gezielt trainieren"
- Map-Veto-Helper: "Banne Ancient (0.64 Rating), Picke Nuke (0.92 Rating)"

---

## Phase 2 — Smart Coaching
*Vom Daten-Tool zum echten Coach. DAS Alleinstellungsmerkmal.*

### 2.1 Automatischer Trainingsplan
Basierend auf den Schwaechen der letzten 5-10 Demos:
- **Taegliche Routine** (15 Minuten):
  - Aim schwach? → "5min Aim Botz, 5min Recoil Master AK, 5min Prefire Mirage"
  - Counter-Strafe schwach? → "10min YPRAC Movement Map"
  - Utility schwach? → "5min Smoke Lineup Practice, 5min Yprac Utility"
- **Woechentlicher Fokus**: Ein Thema pro Woche (z.B. "Woche 23: Opening Duels verbessern")
- **Workshop-Map-Links**: Direkte Steam-Workshop-Links zu empfohlenen Maps
- **Schwierigkeitsgrad**: Anfaenger/Fortgeschritten/Profi-Uebungen
- Trainingsplan aktualisiert sich automatisch nach jeder neuen Demo

### 2.2 Rollen-Erkennung
Automatisch erkennen welche Rolle der Spieler spielt:
- **Entry Fragger** — Hohe Opening Duel Rate, aggressive Positionen, stirbt oft als Erster
- **Support** — Hohe Utility/Runde, viele Assists, Trade Kills
- **AWPer** — AWP-Kill-Anteil > 40%, lange Kampfdistanz
- **Lurker** — Spaete Kills, wenig Trades, hohes Survival
- **Anchor** — CT-fokussiert, wenig Rotation, Site-Hold-Kills

Feedback: "Du spielst wie ein Entry Fragger (66% Opening Duels, 1.8 Erste-Kills/Spiel) aber dein Crosshair Placement ist auf Support-Level → Trainiere Aim oder spiele passiver"

### 2.3 Trend-Alerts & Warnungen
Proaktive Benachrichtigungen statt passiver Charts:
- "Dein HS% ist in den letzten 3 Spielen von 52% auf 38% gefallen"
- "Deine CT-Side wird schwaecher: -3 Kills/Match im Vergleich zu vor 2 Wochen"
- "Starke Verbesserung! Counter-Strafe Score +8% ueber 5 Matches"
- "Achtung: Du stirbst in 4 von 5 Spielen oefter als Erster auf T-Side"
- Alerts als Banner auf dem Dashboard + optional im Coach-Report

### 2.4 Economy-IQ
Wirtschaftliche Entscheidungen bewerten:
- **Buy-Analyse**: Hat der Spieler korrekt gekauft? (Eco nach Niederlage, Full-Buy bei genug Geld)
- **Force-Buy Winrate**: "Du forcest auf 2800$ — deine Force-Buy Winrate ist nur 18%. Spare lieber."
- **Eco-Effizienz**: Kills pro 1000$ investiert
- **Equipment Value vs. Output**: Teure Runden (5000$+) vs. billige Runden — wo ist der ROI besser?
- **Team-Economy-Sync**: Kaufst du synchron mit dem Team?

---

## Phase 3 — Deep Analysis
*Hier wird der Coach richtig smart. Versteht nicht nur WAS passiert, sondern WARUM.*

### 3.1 Death-Analyse — "Warum stirbst du?"
Die wertvollste Information fuer Improvement. Jeder Tod wird kategorisiert:
- **Positioning Death** — Zu exposed, kein Cover, falsche Angle
- **Timing Death** — Zu frueh/spaet gepeekt (korreliert mit death_timing)
- **Aim Death** — Duell verloren trotz guter Position (hohes Crosshair Placement Delta)
- **Utility Death** — Ohne Flash/Smoke gepeekt (keine eigene Utility vorher geworfen)
- **Nummern-Death** — Ueberrannt (2v1 oder 3v1 Situation beim Tod)
- **Trade-faehig?** — War ein Teammate in Naehe zum Traden? Wurde getradet?
- **Info-Death** — Tod brachte wertvolle Info fuers Team (akzeptabler Tod)

Visualisierung:
- Tortendiagramm der Tod-Kategorien
- Auf der Kill-Map als eigene Layer (Farben pro Kategorie)
- Trend: "Deine Positioning-Deaths sind um 20% gesunken seit letztem Monat"

### 3.2 Runden-Highlight-System
Jede Runde automatisch klassifizieren:
- **Hero Round** — 3K+, Clutch-Win, Ace
- **Impact Round** — Opening Kill + Round Win
- **Invisible Round** — 0 Kills, 0 Assists, keine Utility-Wirkung, kein Trade
- **Throw Round** — Team hatte Vorteil (3v2+), Runde verloren, Spieler starb als Erster
- **Space Maker** — Utility hat Gegner bewegt/geblindet, Team hat daraufhin Kills geholt
- **Trade Bait** — Spieler starb, wurde aber in <5s getradet (gutes Teamplay)
- **Eco Hero** — Kill(s) in Eco-Runde mit Pistole/SMG

Ansicht: Timeline mit farbigen Badges pro Runde, klickbar fuer Details

### 3.3 AI-Runden-Erzaehler
Natuerlichsprachliche Zusammenfassung wichtiger Runden:
```
Runde 7 — Eco als CT auf Mirage
Du hast dich in B-Apps versteckt und mit der Deagle 2 Kills geholt.
Im 1v2 Clutch-Versuch wurdest du von Jungle gekillt — du hattest
keinen Smoke fuer die Rotation. Naechstes Mal: Smoke Jungle vor
dem Retake.
```
- Automatisch fuer Clutch-Runden, Highlight-Runden, Throw-Runden
- Konkreter Verbesserungsvorschlag pro Runde
- Kann als "Hausaufgabe" zum Nachschauen im Demo-Viewer genutzt werden

### 3.4 Positions-Zonen & Map-Control
Koordinaten in benannte Zonen uebersetzen:
- Kill/Death-Positionen zu Callouts mappen (A-Site, B-Apps, Mid, Jungle, etc.)
- **Zonen-Heatmap**: "Auf Mirage stirbst du 70% deiner CT-Tode auf A-Site"
- **Zonen-Effizienz**: K/D-Ratio pro Zone pro Map
- **Empfehlung**: "Trainiere A-Site Retakes auf Mirage — dort ist dein K/D 0.4"
- **Rotation-Analyse**: Wie schnell rotierst du auf CT-Side?
- **Positionsvielfalt**: Spielst du immer die gleiche Position? Bist du vorhersehbar?

### 3.5 Gegner-Analyse
Nicht nur eigene Stats, sondern auch den Gegner verstehen:
- **Nemesis**: Wer hat dich am meisten getoetet? Wo? Wie?
- **Duel-Heatmap**: Visuelle Darstellung aller Duelle mit einem bestimmten Gegner
- **Gegner-Staerke**: Wie gut waren die Gegner? (Avg Rating des gegnerischen Teams)
- **Performance nach Gegner-Level**: "Gegen starke Gegner (Rating >1.0) faellt dein ADR um 25%"

### 3.6 Waffen-Analyse
Detaillierte Statistiken pro Waffe:
- K/D-Ratio pro Waffe (AK, M4, AWP, Deagle, Pistolen)
- HS% pro Waffe
- Durchschnittliche Kampfdistanz pro Waffe
- Spray-Effizienz pro Waffe (Recoil Index)
- Empfehlung: "Deine Deagle hat 15% HS — ueberleg ob du lieber USP/P250 spielst"
- Waffen-Trends: Welche Waffen werden besser/schlechter?

---

## Phase 4 — Progress & Motivation
*Der Spieler soll dranbleiben. Verbesserung sichtbar machen.*

### 4.1 Ziele & Fortschritt
Spieler kann sich Ziele setzen:
- "ADR ueber 80 bringen" — Fortschrittsbalken mit aktuellem Stand
- "Opening Duel Winrate > 55%" — Deadline + Trend
- "KAST ueber 70% halten fuer 10 Spiele"
- Automatisch vorgeschlagene Ziele basierend auf Schwaechen
- Meilensteine: "Ziel erreicht! Dein ADR ist jetzt konstant ueber 80."

### 4.2 Achievement-System
Motivierende Abzeichen fuer Meilensteine:
- **Erste Schritte**: "Erstes Spiel analysiert", "5 Spiele analysiert", "50 Spiele"
- **Aim**: "Erste 30-Bombe", "40-Bombe", "Headshot-Maschine (60% HS in einem Spiel)"
- **Consistency**: "5 Spiele in Folge KAST > 70%", "10 Spiele Rating > 0.8"
- **Clutch**: "Clutch-Koenig: 3 Clutch-Wins in einem Match", "Ace!"
- **Improvement**: "Counter-Strafe um 10% verbessert", "ADR-Rekord gebrochen"
- **Grind**: "7 Tage in Folge trainiert", "100 Demos analysiert"
- Badge-Galerie auf dem Dashboard

### 4.3 Habit-Tracker
Spezifische Gewohnheiten identifizieren und tracken:
- "Du peekst zu 73% ohne Utility" → Wird das besser?
- "Du stirbst in 60% der Runden als Erster" → Opening Death Rate ueber Zeit
- "Du nutzt nur 1.2 Utility/Runde" → Trend nach oben?
- "Dein erster Schuss sitzt selten (Crosshair Placement 18°)" → Verbesserung?
- Gewohnheits-Chart: Linie pro Habit ueber die letzten 20 Spiele
- "Gute Nachricht: Dein Utility-Einsatz ist von 1.2 auf 1.8/Runde gestiegen!"

### 4.4 Session-Zusammenfassung
Nach einem Abend Gaming — Ueberblick der Session:
- Bilanz, Trend innerhalb der Session (besser/schlechter geworden?)
- "Match 3 war dein bestes — nach Match 2 (Niederlage) hast du dich mental erholt"
- **Tilt-Detection**: Performance-Einbruch nach Niederlagen erkennen
  - "Nach 2 Niederlagen in Folge faellt dein Rating um 0.15 — mach eine Pause"
- **Optimale Spielzeit**: "Deine beste Performance ist zwischen 18-21 Uhr"
- **Fatigue-Detection**: "Nach 4+ Spielen sinkt dein ADR um 12% — spiel nicht mehr als 4"

### 4.5 Woechentlicher/Monatlicher Digest
Automatische Zusammenfassung als Report:
- "Diese Woche: 12 Spiele, 58% Winrate"
- Highlight der Woche (bestes Spiel)
- Groesste Verbesserung + hartnaeickigste Schwaeche
- Vergleich: Diesen Monat vs. letzten Monat
- Rolling Averages (7-Tage, 30-Tage) fuer alle Key-Metriken
- Optional als Obsidian-Notiz exportieren

### 4.6 Vergleich mit dir selbst
- **Zeitraum-Vergleich**: "August vs. Juli" nebeneinander
- **Bester Monat**: "Dein bester Monat war Juli: Rating 0.98 avg"
- **Persoenliche Rekorde**: Bester ADR, meiste Kills, hoechstes Rating, laengste Siegesserie
- **Formkurve**: Letztes Spiel → Letzte 5 → Letzte 20 → All-Time

---

## Phase 5 — Social & Team
*CS2 ist ein Teamspiel. Gemeinsam besser werden.*

### 5.1 Team-Analyse (5-Stack)
Wenn mehrere Demos mit dem gleichen Team analysiert werden:
- **Team-Synergien**: "Du und Max zusammen: 72% Winrate. Du und Tim: 45%"
- **Rollen-Verteilung**: Wer spielt welche Rolle? Gibt es Luecken?
- **Trade-Effizienz**: Wer tradet wen? Welche Duos traden am besten?
- **Utility-Koordination**: Werden Utility-Combos ausgefuehrt?
- **Team-Spider-Chart**: Wo ist das Team stark/schwach als Einheit?

### 5.2 Freundes-Vergleich
- Vergleiche dich mit Freunden die auch das Tool nutzen
- Leaderboard innerhalb der Gruppe
- "Wer hat diese Woche am meisten verbessert?"
- Motiviert durch gesunden Wettbewerb

### 5.3 Shareable Reports
- Match-Report als Link teilen (z.B. localhost-basierte HTML-Seite)
- Screenshot-optimierte Zusammenfassung
- "Mein bestes Spiel" als teilbares Bild generieren

---

## Phase 6 — Integration & Polish
*Das Tool in den Alltag integrieren.*

### 6.1 FACEIT/Matchmaking Integration
- FACEIT API: Rank-Verlauf, ELO-Tracking
- Steam API: Inventory, Rank, Spielstunden
- Automatisch neue Demos erkennen (Folder-Watch)

### 6.2 Discord-Integration
- Webhook: Post-Match-Zusammenfassung automatisch in Discord-Channel
- "deej0t hat gerade Mirage 13:6 gewonnen — Rating 1.17, 21K/9D"
- Woechentlicher Digest als Discord-Embed

### 6.3 Auto-Demo-Download
- Neues CS2 Match erkannt → Demo automatisch herunterladen
- Automatische Analyse im Hintergrund
- Benachrichtigung wenn fertig: "Neue Analyse verfuegbar"

### 6.4 Folder-Watch Daemon
- Hintergrund-Service der den Replay-Ordner ueberwacht
- Neue .dem Datei erkannt → automatisch analysieren und exportieren
- Null Aufwand fuer den Spieler — einfach spielen, Analyse kommt von allein

### 6.5 Mobile-Responsive
- Alle Seiten auf dem Handy nutzbar
- Quick-Check zwischen Spielen: "Wie war mein letztes Match?"
- Push-Benachrichtigungen (wenn als PWA)

### 6.6 Lokalisierung
- Deutsch (bereits vorhanden)
- Englisch
- Language-Setting im Config endlich funktional machen

### 6.7 Export & Reporting
- PDF-Export fuer Coach-Reports
- CSV-Export fuer eigene Analyse
- Obsidian-Graph optimieren mit graphify

---

## Phase 7 — Advanced AI Features
*Die Zukunft des Coachings.*

### 7.1 AI-Coach-Chat
Ein Chat-Interface im Tool:
- "Warum sterbe ich so oft auf A-Site Mirage?"
- "Was soll ich heute trainieren?"
- "Vergleiche mein letztes Spiel mit meinem Durchschnitt"
- AI hat Zugriff auf alle analysierten Demos und kann kontextbezogen antworten

### 7.2 Replay-Bookmarks
Automatische Lesezeichen fuer wichtige Momente:
- "Schau dir diese 5 Runden an um aus deinen Fehlern zu lernen"
- Demo-Tick + Beschreibung + Verbesserungsvorschlag
- Exportierbar als Liste fuer die Demo-Review-Session

### 7.3 Gegner-Vorhersage
Wenn gegen bekannte Gegner gespielt wird:
- "Spieler X spielt 80% AWP, peekt immer Mid zuerst"
- "Spieler Y hat schwache Counter-Strafe — peek ihn aggressiv"
- Aus frueheren Demos lernen

### 7.4 Muskelgedaechtnis-Tracker
Motorische Faehigkeiten ueber Zeit verfolgen:
- Spray-Pattern-Konsistenz (Recoil Index Varianz)
- Counter-Strafe-Verbesserung
- Crosshair Placement Trend (Grad-Genauigkeit)
- "Dein AK-Spray ist 15% konsistenter als vor einem Monat"

### 7.5 Warm-up Protocol Generator
Vor jedem Spiel ein personalisiertes Aufwaermprogramm:
- Basierend auf den Schwaechen der letzten Demos
- "Dein Spray ist schwach → 5min Recoil Master AK/M4"
- "Dein Crosshair Placement ist 18° → 5min Prefire Map deiner naechsten Map"
- "Counter-Strafe 72% → 5min YPRAC Movement"
- Timer + Checkliste im UI

### 7.6 Pre-Match Briefing
Vor einem Match auf einer bestimmten Map:
- Reminder der eigenen Schwaechen auf dieser Map
- "Mirage CT: Denke an A-Site Retake Smokes"
- "Deine schwaechste Position ist Window — spiel lieber B"
- "Letzte 3 Spiele auf Mirage: Opening Death Rate 45% — peek nicht als Erster"

### 7.7 VOD/Clip-Integration
- Highlights automatisch als Clips markieren
- Integration mit OBS/Medal/Outplayed Clips
- "Dieser Clip zeigt deinen besten Clutch diese Woche"

---

## Priorisierung nach Impact

| Feature | Impact | Aufwand | Prioritaet |
|---------|--------|---------|------------|
| Dashboard-Startseite | Sehr hoch | Mittel | 1 |
| Skill-Benchmarks | Sehr hoch | Niedrig | 1 |
| Trainingsplan-Generator | Sehr hoch | Mittel | 1 |
| Trend-Alerts | Hoch | Niedrig | 2 |
| Death-Analyse | Sehr hoch | Hoch | 2 |
| Runden-Highlights | Hoch | Mittel | 2 |
| Rollen-Erkennung | Hoch | Mittel | 2 |
| Session-Zusammenfassung | Hoch | Niedrig | 3 |
| Ziele & Fortschritt | Hoch | Mittel | 3 |
| Achievement-System | Mittel | Mittel | 3 |
| Habit-Tracker | Hoch | Mittel | 3 |
| Economy-IQ | Mittel | Hoch | 4 |
| Positions-Zonen | Hoch | Hoch | 4 |
| Waffen-Analyse | Mittel | Niedrig | 4 |
| Gegner-Analyse | Mittel | Mittel | 4 |
| Team-Analyse | Mittel | Hoch | 5 |
| Folder-Watch | Hoch | Mittel | 5 |
| AI-Coach-Chat | Sehr hoch | Sehr hoch | 6 |
| Discord-Integration | Mittel | Niedrig | 6 |
| Mobile-Responsive | Mittel | Mittel | 6 |

---

## Design-Prinzipien

1. **Actionable > Informativ** — Jede Zahl muss eine Empfehlung haben
2. **Weniger ist mehr** — Nicht 50 Metriken zeigen, sondern die 3 wichtigsten
3. **Fortschritt sichtbar machen** — Der Spieler muss SEHEN dass er besser wird
4. **Kein Blame** — Nie "du bist schlecht", immer "hier kannst du dich verbessern"
5. **Offline-First** — Alles lokal, keine Cloud-Abhaengigkeit, keine Subscription
6. **Zero-Effort** — So wenig manuelle Arbeit wie moeglich fuer den Spieler
