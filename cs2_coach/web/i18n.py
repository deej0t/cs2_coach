"""Lightweight i18n for CS2 Coach — gettext-style with German as source language."""

from __future__ import annotations

# German text is the key, English translation is the value.
# If a string is not in the dict, the German original is returned.
EN: dict[str, str] = {
    # ── Sidebar group labels ──
    "Analyse": "Analysis",
    "Performance": "Performance",
    "Coaching": "Coaching",
    "System": "System",

    # ── Sidebar nav items ──
    "Batch-Analyse": "Batch Analysis",
    "Share-Code": "Share Code",
    "Kalender": "Calendar",
    "Waffen": "Weapons",
    "Gegner": "Opponents",
    "Duelle": "Duels",
    "Gegner-Scout": "Opponent Scout",
    "Team-Analyse": "Team Analysis",
    "Fortschritt": "Progress",
    "Mechanik": "Mechanics",
    "Muskelgedaechtnis": "Muscle Memory",
    "Death-Analyse": "Death Analysis",
    "Positions-Zonen": "Position Zones",
    "Rundenverlauf": "Round Timeline",
    "Ziele": "Goals",
    "Inventar": "Inventory",
    "Wissensgraph": "Knowledge Graph",
    "CSV-Export": "CSV Export",
    "Einstellungen": "Settings",
    "Replay-Bookmarks": "Replay Bookmarks",

    # ── Common game terms ──
    "Sieg": "Win",
    "Niederlage": "Loss",
    "Unentschieden": "Draw",
    "Runden": "Rounds",
    "Runde": "Round",
    "Spieler": "Player",
    "Kills": "Kills",
    "Tode": "Deaths",
    "Spiele": "Matches",
    "Siege": "Wins",
    "Niederlagen": "Losses",
    "Kopftreffer": "Headshots",
    "Ueberlebensrate": "Survival Rate",
    "Treffergenauigkeit": "Accuracy",
    "Kampfdistanz": "Engagement Distance",
    "Ergebnis": "Result",
    "Bilanz": "Record",
    "Gesamt": "Total",
    "Durchschnitt": "Average",
    "Letzte": "Last",
    "Beste": "Best",
    "Trend": "Trend",
    "Zurueck": "Back",

    # ── Card kickers / section labels ──
    "Aktuelle Form": "Current Form",
    "Skill-Benchmarks": "Skill Benchmarks",
    "Skill-Profil": "Skill Profile",
    "Deine Rolle": "Your Role",
    "Naechster Fokus": "Next Focus",
    "Letzte Session": "Last Session",
    "Staerken": "Strengths",
    "Schwaechen": "Weaknesses",
    "Formkurve": "Form Curve",
    "Map-Performance": "Map Performance",
    "Konfiguration": "Configuration",
    "Trainingsplan": "Training Plan",
    "Coach-Report": "Coach Report",
    "Coach-Analyse": "Coach Analysis",
    "Empfehlungen": "Recommendations",
    "Fokus-Bereiche": "Focus Areas",
    "Gegner-Duelle": "Opponent Duels",
    "Alle Gegner-Duelle": "All Opponent Duels",
    "Waffen-Duelle": "Weapon Duels",
    "Opening Duels": "Opening Duels",
    "Kampf": "Combat",
    "Kampf-Timing": "Combat Timing",
    "Situationen": "Situations",
    "Gesamt K/D": "Overall K/D",
    "K/D Ratio": "K/D Ratio",
    "HS-Quote": "HS Rate",
    "HS-Tod Quote": "HS Death Rate",
    "Win-Rate": "Win Rate",
    "Avg Rating": "Avg Rating",
    "Avg Distanz": "Avg Distance",
    "Matches Gesamt": "Total Matches",
    "Spieltage": "Play Days",
    "Laengste Serie": "Longest Streak",
    "Bester Tag": "Best Day",
    "Streak": "Streak",
    "Utility": "Utility",
    "Flash-Effektivitaet": "Flash Effectiveness",
    "Flash-Trefferquote": "Flash Hit Rate",
    "Blind-Dauer": "Blind Duration",
    "Team-Flash": "Team Flash",
    "Granaten-Verteilung": "Grenade Distribution",
    "Utility/Runde": "Utility/Round",
    "Utility pro Map": "Utility per Map",
    "Utility-Impact auf Performance": "Utility Impact on Performance",
    "Wann stirbst du?": "When Do You Die?",
    "Womit wirst du getoetet?": "What Kills You?",
    "Wer toetet dich am meisten?": "Who Kills You Most?",
    "Tod-Kontext": "Death Context",
    "Auf welcher Seite?": "Which Side?",
    "Monats-Vergleich": "Monthly Comparison",
    "Tageszeit-Analyse": "Time of Day Analysis",
    "Wochentag-Analyse": "Day of Week Analysis",
    "Monatliche Entwicklung": "Monthly Progress",
    "Jahreskalender": "Year Calendar",
    "CT Waffen": "CT Weapons",
    "T Waffen": "T Weapons",
    "Lokal": "Local",
    "Ordner": "Folder",
    "Upload": "Upload",
    "Letzte Demos": "Recent Demos",
    "Demo-Dateien": "Demo Files",
    "Graph-Analyse": "Graph Analysis",
    "Direktvergleich": "Direct Comparison",
    "Gemeinsame Matches": "Shared Matches",
    "Dominierte Gegner": "Dominated Opponents",
    "Beste Duos": "Best Duos",
    "Duo Win-Rate Vergleich": "Duo Win Rate Comparison",
    "Nemesis-Spieler": "Nemesis Players",
    "Map auswaehlen": "Select Map",
    "Motor-Skills im Detail": "Motor Skills in Detail",
    "Multi-Kills (Du)": "Multi-Kills (You)",
    "Naechste Meilensteine": "Next Milestones",
    "Gegner-Staerke": "Opponent Strength",
    "Top-Knoten (nach Verbindungen)": "Top Nodes (by Connections)",
    "Wirtschafts-Performance": "Economy Performance",
    "Maps — Practice Configs": "Maps — Practice Configs",
    "Runden-Timeline": "Round Timeline",
    "Runden-Highlights": "Round Highlights",
    "Scoreboard anzeigen": "Show Scoreboard",
    "Death-Details anzeigen": "Show Death Details",
    "Death-Analyse": "Death Analysis",

    # ── Match detail ──
    "Teilen": "Share",
    "KI-Analyse": "AI Analysis",
    "KI Match-Analyse": "AI Match Analysis",
    "KI-Runden-Analyse": "AI Round Analysis",
    "Analyse wird generiert...": "Generating analysis...",
    "Match-Analyse wird generiert...": "Generating match analysis...",
    "Neu generieren": "Regenerate",
    "Seitenwechsel": "Side Switch",

    # ── Economy ──
    "Pistole": "Pistol",
    "Eco": "Eco",
    "Force": "Force",
    "Fullbuy": "Full Buy",
    "Keine Runden": "No Rounds",

    # ── Death categories ──
    "Hauptproblem": "Main Problem",
    "Vermeidbare Tode": "Avoidable Deaths",
    "Produktive Tode": "Productive Deaths",

    # ── Round highlights ──
    "Hero (3K+)": "Hero (3K+)",
    "Frueh gestorben, 0 Impact": "Died Early, 0 Impact",
    "0K Death, Runde verloren": "0K Death, Round Lost",

    # ── Settings ──
    "Spieler-Name": "Player Name",
    "Obsidian Vault Pfad": "Obsidian Vault Path",
    "Coach-Unterordner": "Coach Subfolder",
    "Demo-Ordner": "Demo Folder",
    "Sprache": "Language",
    "Discord Webhook URL": "Discord Webhook URL",
    "Steam Web API Key": "Steam Web API Key",
    "CS2 Auth Token": "CS2 Auth Token",
    "AI-Anbieter": "AI Provider",
    "Ollama Server URL": "Ollama Server URL",
    "Gemini API Key": "Gemini API Key",
    "AI-Modell": "AI Model",
    "Speichern": "Save",
    "Wartung": "Maintenance",
    "Cache & Daten": "Cache & Data",
    "Python-Cache loeschen": "Clear Python Cache",
    "Export-JSONs loeschen": "Delete Export JSONs",
    "Obsidian-Notizen loeschen": "Delete Obsidian Notes",
    "Komplett-Reset": "Full Reset",

    # ── Flash messages ──
    "Einstellungen gespeichert.": "Settings saved.",
    "Export nicht gefunden.": "Export not found.",
    "Analyse abgeschlossen.": "Analysis complete.",
    "Fehler": "Error",

    # ── Common UI ──
    "Keine Daten": "No Data",
    "Keine Daten vorhanden.": "No data available.",
    "Keine Spieldaten vorhanden.": "No match data available.",
    "Keine Auffaelligkeiten.": "No anomalies.",
    "Lade...": "Loading...",
    "Analysiere...": "Analyzing...",
    "Datei": "File",
    "Spieler": "Player",
    "Waffe": "Weapon",
    "Map": "Map",
    "Datum": "Date",

    # ── Table headers ──
    "Name": "Name",
    "Gegner": "Opponent",
    "Seite": "Side",
    "Gewonnen": "Won",
    "Verloren": "Lost",
    "Nah": "Close",
    "Mittel": "Mid",
    "Fern": "Long",
    "Frueh": "Early",
    "Spaet": "Late",
    "Gesamt": "Total",
    "Runden": "Rounds",
    "Anteil": "Share",

    # ── Trends / Sessions ──
    "Verbesserung": "Improvement",
    "Verschlechterung": "Decline",
    "Stabil": "Stable",
    "Starke Verbesserung": "Strong Improvement",
    "Achtung": "Warning",
    "Tilt-Gefahr": "Tilt Risk",
    "Optimale Spielzeit": "Optimal Play Time",
    "Session-Zusammenfassung": "Session Summary",

    # ── Goals / Achievements ──
    "Ziel erreicht!": "Goal reached!",
    "Aktive Ziele": "Active Goals",
    "Abgeschlossene Ziele": "Completed Goals",
    "Neues Ziel": "New Goal",
    "Ziel hinzufuegen": "Add Goal",
    "Freigeschaltet": "Unlocked",
    "Gesperrt": "Locked",

    # ── Digest ──
    "Woechentlicher Digest": "Weekly Digest",
    "Monatlicher Digest": "Monthly Digest",
    "Diese Woche": "This Week",
    "Dieser Monat": "This Month",
    "Letzter Monat": "Last Month",
    "An Discord senden": "Send to Discord",

    # ── Compare ──
    "Zeitraum-Vergleich": "Period Comparison",
    "Persoenliche Rekorde": "Personal Records",
    "Bester Monat": "Best Month",

    # ── Briefing / Warmup ──
    "Pre-Match Briefing": "Pre-Match Briefing",
    "Warm-up Protokoll": "Warm-up Protocol",
    "Schwaechen auf dieser Map": "Weaknesses on this Map",
    "Deine Routine": "Your Routine",
    "Starten": "Start",
    "Fertig": "Done",

    # ── Chat ──
    "Nachricht eingeben...": "Type a message...",
    "Senden": "Send",

    # ── Watch / Sharecode ──
    "Demo-Ordner ueberwachen": "Watch Demo Folder",
    "Neue Demos gefunden": "New Demos Found",
    "Alle analysieren": "Analyze All",
    "Share-Code eingeben": "Enter Share Code",

    # ── Batch ──
    "Demos hochladen": "Upload Demos",
    "oder Dateien hierher ziehen": "or drag files here",
    "Analyse starten": "Start Analysis",

    # ── Share card ──
    "Bild herunterladen": "Download Image",
    "Link kopieren": "Copy Link",

    # ── Sides ──
    "Map-spezifische Seiten-Performance": "Map-specific Side Performance",
    "Dominante Seite": "Dominant Side",

    # ── Calendar ──
    "Schlechtester Tag": "Worst Day",
    "Legende": "Legend",

    # ── Clutches ──
    "Clutch-Situationen": "Clutch Situations",
    "Clutch gewonnen": "Clutch Won",
    "Clutch verloren": "Clutch Lost",

    # ── Momentum ──
    "Momentum-Analyse": "Momentum Analysis",
    "Siegesserie": "Win Streak",
    "Niederlagenserie": "Loss Streak",
    "Nach Niederlage": "After Loss",
    "Nach Sieg": "After Win",

    # ── Teammates ──
    "Deine Teammates": "Your Teammates",
    "Zusammen gespielt": "Played Together",

    # ── Leaderboard ──
    "Cross-Match Leaderboard": "Cross-Match Leaderboard",

    # ── Inventory ──
    "Dein Inventar": "Your Inventory",

    # ── Practice ──
    "Practice Server starten": "Start Practice Server",
    "Befehle kopieren": "Copy Commands",

    # ── Bookmarks ──
    "Lesezeichen": "Bookmarks",
    "Tick": "Tick",
    "Beschreibung": "Description",

    # ── Viewer ──
    "2D Viewer": "2D Viewer",
    "Kill-Positionen": "Kill Positions",
    "Death-Positionen": "Death Positions",

    # ── Motor ──
    "Muskelgedaechtnis-Tracker": "Muscle Memory Tracker",
    "Konsistenz": "Consistency",
    "Verbesserung": "Improvement",

    # ── Zones ──
    "Zonen-Heatmap": "Zone Heatmap",
    "Zonen-Effizienz": "Zone Efficiency",

    # ── Mechanics ──
    "Mechanik-Analyse": "Mechanics Analysis",
    "Spray-Control": "Spray Control",
    "Crosshair Placement": "Crosshair Placement",

    # ── Scout ──
    "Gegner-Scout": "Opponent Scout",
    "Spieler suchen": "Search Player",

    # ── Graph ──
    "Wissensgraph": "Knowledge Graph",

    # ── Abbr tooltips ──
    "Kill/Death-Verhaeltnis — ueber 1.0 = mehr Kills als Deaths": "Kill/Death ratio — above 1.0 = more kills than deaths",
    "Average Damage per Round — durchschnittlicher Schaden pro Runde": "Average Damage per Round",
    "Gesamtbewertung basierend auf Kills, Deaths, Assists, ADR und Impact": "Overall rating based on kills, deaths, assists, ADR and impact",
    "Kill, Assist, Survived, Traded — Prozent der Runden mit positivem Beitrag": "Kill, Assist, Survived, Traded — percentage of rounds with positive contribution",
    "Headshot-Prozent — Anteil der Kills durch Kopftreffer": "Headshot percentage — share of kills via headshots",
    "Anteil der Schuesse im Stillstand — gutes Counter-Strafing bedeutet vor dem Schuss stehen bleiben": "Share of shots while stationary — good counter-strafing means stopping before shooting",
    "Erstes Duell der Runde — gewonnen (W) oder verloren (L)": "First duel of the round — won (W) or lost (L)",
    "Kill eines Gegners innerhalb von 3s nachdem er einen Teammate getoetet hat": "Killing an opponent within 3s after they killed a teammate",
    "Treffergenauigkeit — Prozent der Schuesse die getroffen haben": "Accuracy — percentage of shots that hit",
    "Ueberlebensrate — Prozent der Runden ueberlebt": "Survival rate — percentage of rounds survived",
    "Durchschnittliche Schussnummer im Magazin beim Kill — niedrig = Burst, hoch = Spray": "Average shot number in magazine at kill — low = burst, high = spray",
    "Burst (1-3 Schuesse) vs. Spray (4+) Kills": "Burst (1-3 shots) vs. Spray (4+) kills",
    "N=Nah (<500u), M=Mittel (500-1500u), W=Weit (>1500u)": "C=Close (<500u), M=Mid (500-1500u), L=Long (>1500u)",
    "1vX Situationen — gewonnen/versucht": "1vX situations — won/attempted",
    "Wann in der Runde du stirbst — Early (erstes Drittel), Mid, Late": "When in the round you die — Early (first third), Mid, Late",
    "Kategorisierung deiner Deaths — zeigt wo die meisten vermeidbaren Tode passieren": "Categorization of your deaths — shows where most avoidable deaths occur",
    "Klassifizierung jeder Runde nach deinem Impact": "Classification of each round by your impact",
    "Crosshair Placement — Grad-Abweichung des Fadenkreuzes zur Gegnerposition 0.5s vor dem Kill. Niedriger = besser": "Crosshair Placement — degree deviation of crosshair to enemy position 0.5s before kill. Lower = better",
    "Average Damage per Round": "Average Damage per Round",
    "Durchschnittlicher Schaden pro Runde": "Average damage per round",
    "Durchschnittliches Kill/Death-Verhaeltnis": "Average Kill/Death ratio",
    "Durchschnittliches Rating auf dieser Map": "Average rating on this map",
    "Gesamtbewertung": "Overall Rating",
    "Gesamtbewertung basierend auf Impact-Metriken": "Overall rating based on impact metrics",
    "Headshot-Prozent": "Headshot Percentage",
    "Kill, Assist, Survived, Traded": "Kill, Assist, Survived, Traded",
    "Kill, Assist, Survived, Traded — Runden mit positivem Beitrag": "Kill, Assist, Survived, Traded — rounds with positive contribution",
    "Kill, Assist, Survived, Traded — durchschnittliche Rundenbeteiligung": "Kill, Assist, Survived, Traded — average round contribution",
    "Kill/Death-Verhaeltnis": "Kill/Death Ratio",
    "Kills / Deaths / Assists": "Kills / Deaths / Assists",
    "Kills innerhalb 3s nach einem Teammate-Tod": "Kills within 3s after a teammate death",
    "Anteil der Schuesse im Stillstand (inacc = Movement Inaccuracy)": "Share of shots while stationary (inacc = Movement Inaccuracy)",
    "Opening Duel Winrate": "Opening Duel Win Rate",
    "Trade Kills — Kill eines Gegners innerhalb von 3 Sekunden nachdem er einen Teammate getoetet hat": "Trade Kills — killing an opponent within 3 seconds after they killed a teammate",
    "Trade-Kills pro Runde": "Trade kills per round",
    "Treffergenauigkeit — Prozent der abgefeuerten Schuesse die getroffen haben": "Accuracy — percentage of fired shots that hit",
    "Ueberlebensrate — Prozent der Runden in denen du ueberlebt hast": "Survival rate — percentage of rounds you survived",
    "Erstes Duell der Runde — gewonnen oder verloren": "First duel of the round — won or lost",
    "Kampfdistanz — N=Nah (<500u), M=Mittel (500-1500u), W=Weit (>1500u). Zeigt dein bevorzugtes Engagement": "Engagement distance — C=Close (<500u), M=Mid (500-1500u), L=Long (>1500u). Shows your preferred engagement",
    "Nur eigene Utility anzeigen": "Show only own utility",
    "Grad-Abweichung des Fadenkreuzes zur Gegnerposition 0.5s vor dem Kill. Niedriger = besser": "Degree deviation of crosshair to enemy position 0.5s before kill. Lower = better",
    "Burst vs. Spray — Verhaeltnis von Kills mit 1-3 Schuessen (Burst) vs. 4+ (Spray)": "Burst vs. Spray — ratio of kills with 1-3 shots (burst) vs. 4+ (spray)",
    "Nemesis — Gegner die dich dominiert haben": "Nemesis — opponents who dominated you",
    "Gegner-Staerke — Deine Performance nach Gegner-Level": "Opponent Strength — your performance by opponent level",
}


def make_translator(lang: str = "de"):
    """Return a translation function for the given language code.

    Usage in Jinja2 templates: {{ _('German text') }}
    If lang == 'de' or the string is not found, the original is returned.
    """
    if lang != "en":
        # For German (or any unsupported lang), return identity
        def _(text: str) -> str:
            return text
        return _

    def _(text: str) -> str:
        return EN.get(text, text)
    return _
