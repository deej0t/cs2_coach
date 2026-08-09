# CS2 Coach — Demo-Analyse mit Obsidian-Integration

## Installation

```bash
pip install -r requirements.txt
```

## Konfiguration

Bearbeite `config.yaml`:

```yaml
# Pfad zu deinem Obsidian-Vault
obsidian_vault_path: "C:/Users/Home/Desktop/cs2-coach"

# Unterordner im Vault
coach_subfolder: "CS2-Coach"

# Dein Steam-Name oder SteamID64
player_name: "DeinName"
steam_id: ""
```

## Nutzung

### Demo analysieren

```bash
python -m cs2_coach analyze pfad/zur/demo.dem
```

### Mit bestimmtem Spieler

```bash
python -m cs2_coach analyze demo.dem --player "DeinName"
python -m cs2_coach analyze demo.dem --steamid "76561198xxxxx"
```

### Ohne Obsidian-Export

```bash
python -m cs2_coach analyze demo.dem --no-export
```

### Wissensgraph indexieren (Graphify)

```bash
python -m cs2_coach graph
```

### Setup prüfen

```bash
python -m cs2_coach setup
```

## Obsidian-Vault-Struktur

Nach der ersten Analyse wird folgende Struktur erstellt:

```
CS2-Coach/
├── 2024-01-15_Mirage_Match_1430.md    # Match-Analysen
├── Konzepte/
│   ├── Counter-Strafing.md
│   ├── Opening Duels.md
│   ├── Positionierung.md
│   ├── Clutch-Disziplin.md
│   ├── Teamplay.md
│   ├── Trockener Peek.md
│   ├── AWP.md
│   └── Rifle.md
└── Maps/
    ├── Mirage.md
    ├── Inferno.md
    ├── Dust2.md
    └── ...
```

Alle Notizen sind über Wikilinks (`[[...]]`) vernetzt und bilden einen Wissensgraphen in Obsidian.

## Graphify-Integration

Nach mehreren Analysen kannst du mit `python -m cs2_coach graph` den gesamten Vault als Wissensgraph indexieren. Das erzeugt:

- `_graph/cs2_graph.json` — Graph-Daten
- `_graph/cs2_graph.html` — Interaktive Visualisierung
- `_graph/cs2_graph.canvas` — Obsidian Canvas
- `_graph/Graph-Analyse.md` — Zentrale Konzepte & Cluster-Analyse
