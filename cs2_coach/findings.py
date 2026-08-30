"""Strukturierte Coach-Befunde und deren Nachverfolgung ueber die Zeit.

Der Coach-Report (coach.py) erzeugt Prosa. Prosa laesst sich nicht
vergleichen: man kann nicht sagen, ob eine Empfehlung von vor drei Wochen
befolgt wurde. Dieses Modul erzeugt dieselbe Bewertung noch einmal in
maschinenlesbarer Form - Metrik, Schweregrad, Richtung - sodass sich die
Befunde ueber Matches hinweg verfolgen lassen.

Die Befunde werden aus dem bereits exportierten Spieler-JSON abgeleitet,
nicht aus dem Report-Text. Dadurch funktionieren sie rueckwirkend fuer
alle vorhandenen Exports, ohne eine einzige Demo neu zu parsen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

# Schweregrade, absteigend nach Dringlichkeit
CRITICAL = "critical"
WARNING = "warning"
OK = "ok"

SEVERITY_ORDER = {CRITICAL: 0, WARNING: 1, OK: 2}


@dataclass
class Finding:
    """Eine Bewertung einer Metrik fuer ein einzelnes Match."""

    key: str
    label: str
    severity: str
    value: float
    unit: str = ""
    advice: str = ""

    @property
    def is_issue(self) -> bool:
        return self.severity != OK


@dataclass
class Rule:
    """Schwellenwert-Regel fuer eine Metrik.

    ``lower_is_better`` steuert die Vergleichsrichtung. ``critical`` und
    ``warning`` sind die Grenzen; bei lower_is_better gilt "schlechter als",
    sonst "schlechter als" in die andere Richtung.
    """

    key: str
    label: str
    path: tuple[str, ...]
    critical: float
    warning: float
    lower_is_better: bool
    unit: str = ""
    advice: str = ""
    min_sample_path: tuple[str, ...] | None = None
    min_sample: float = 0.0

    def extract(self, player: dict) -> float | None:
        node = player
        for part in self.path:
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        if isinstance(node, bool) or not isinstance(node, (int, float)):
            return None
        return float(node)

    def has_enough_data(self, player: dict) -> bool:
        if self.min_sample_path is None:
            return True
        node = player
        for part in self.min_sample_path:
            if not isinstance(node, dict) or part not in node:
                return False
            node = node[part]
        try:
            return float(node) >= self.min_sample
        except (TypeError, ValueError):
            return False

    def severity_for(self, value: float) -> str:
        if self.lower_is_better:
            if value > self.critical:
                return CRITICAL
            if value > self.warning:
                return WARNING
            return OK
        if value < self.critical:
            return CRITICAL
        if value < self.warning:
            return WARNING
        return OK


# Die Schwellen spiegeln die Bewertung in coach.py wider, damit Report und
# Nachverfolgung dieselbe Sprache sprechen.
RULES: list[Rule] = [
    Rule(
        key="crosshair_placement",
        label="Crosshair Placement",
        path=("crosshair_placement", "avg_degrees"),
        critical=15.0, warning=8.0, lower_is_better=True, unit="°",
        advice="Fadenkreuz auf Kopfhoehe und auf den naechsten erwarteten Angle.",
        min_sample_path=("crosshair_placement", "kills_analyzed"), min_sample=1,
    ),
    Rule(
        key="counter_strafe",
        label="Counter-Strafing",
        path=("counter_strafe_pct",),
        critical=80.0, warning=90.0, lower_is_better=False, unit="%",
        advice="Gegenrichtung antippen, erst dann schiessen.",
    ),
    Rule(
        key="adr",
        label="ADR",
        path=("adr",),
        critical=65.0, warning=80.0, lower_is_better=False,
        advice="Mehr Kontakt in der Rundenmitte suchen, nicht nur Entry oder Rest.",
    ),
    Rule(
        key="kd",
        label="K/D",
        path=("kd",),
        critical=0.9, warning=1.1, lower_is_better=False,
        advice="Weniger unnoetige Duelle ohne Trade-Absicherung.",
    ),
    Rule(
        key="kast",
        label="KAST",
        path=("kast_pct",),
        critical=65.0, warning=72.0, lower_is_better=False, unit="%",
        advice="Jede Runde einen Beitrag: Kill, Assist, Trade oder ueberleben.",
    ),
    Rule(
        key="utility",
        label="Utility pro Runde",
        path=("utility_per_round",),
        critical=1.0, warning=1.5, lower_is_better=False,
        advice="Zwei bis drei Standard-Lineups pro Map konsequent werfen.",
    ),
    Rule(
        key="survival",
        label="Survival",
        path=("survival_rate",),
        critical=30.0, warning=40.0, lower_is_better=False, unit="%",
        advice="Weniger Positionen ohne Rueckzugsweg halten.",
    ),
    Rule(
        key="accuracy",
        label="Accuracy",
        path=("accuracy",),
        critical=15.0, warning=20.0, lower_is_better=False, unit="%",
        advice="Erste Kugel zaehlt: kurze Bursts statt Dauerfeuer.",
    ),
]

RULES_BY_KEY = {r.key: r for r in RULES}


def evaluate(player: dict) -> list[Finding]:
    """Alle Regeln auf ein Spieler-Dict (Export-JSON-Schema) anwenden."""
    out: list[Finding] = []
    for rule in RULES:
        if not rule.has_enough_data(player):
            continue
        value = rule.extract(player)
        if value is None:
            continue
        out.append(Finding(
            key=rule.key,
            label=rule.label,
            severity=rule.severity_for(value),
            value=round(value, 2),
            unit=rule.unit,
            advice=rule.advice,
        ))
    return out


def issues(player: dict) -> list[Finding]:
    """Nur die Befunde mit Handlungsbedarf, dringendste zuerst."""
    found = [f for f in evaluate(player) if f.is_issue]
    found.sort(key=lambda f: SEVERITY_ORDER[f.severity])
    return found


# ── Nachverfolgung ueber mehrere Matches ────────────────────────────────

# Ein Befund gilt als "chronisch", wenn er in diesem Anteil der juengsten
# Matches auftritt.
RECENT_WINDOW = 5
CHRONIC_RATIO = 0.6

NEW = "new"
CHRONIC = "chronic"
IMPROVING = "improving"
RESOLVED = "resolved"
STABLE = "stable"


@dataclass
class Track:
    """Verlauf eines Befunds ueber alle ausgewerteten Matches."""

    key: str
    label: str
    unit: str = ""
    advice: str = ""
    total_matches: int = 0
    issue_count: int = 0
    recent_issue_count: int = 0
    current_streak: int = 0
    status: str = STABLE
    first_seen: str = ""
    last_seen: str = ""
    first_value: float = 0.0
    recent_value: float = 0.0
    history: list[dict] = field(default_factory=list)

    @property
    def delta(self) -> float:
        return round(self.recent_value - self.first_value, 2)

    @property
    def improved(self) -> bool:
        rule = RULES_BY_KEY.get(self.key)
        if rule is None or self.first_value == self.recent_value:
            return False
        return (self.delta < 0) if rule.lower_is_better else (self.delta > 0)


def build_tracks(matches: list[dict]) -> list[Track]:
    """Befunde ueber eine Match-Liste verfolgen.

    ``matches`` ist chronologisch aufsteigend (aeltestes zuerst) und enthaelt
    je Eintrag ``date``, ``map`` und ``player`` (Export-JSON-Schema).
    Zurueck kommen die Verlaeufe, dringendste zuerst.
    """
    if not matches:
        return []

    per_key: dict[str, Track] = {}

    for m in matches:
        player = m.get("player") or {}
        date = m.get("date", "")
        for f in evaluate(player):
            t = per_key.get(f.key)
            if t is None:
                t = Track(key=f.key, label=f.label, unit=f.unit, advice=f.advice)
                t.first_value = f.value
                per_key[f.key] = t
            t.total_matches += 1
            t.recent_value = f.value
            t.history.append({
                "date": date,
                "map": m.get("map", ""),
                "value": f.value,
                "severity": f.severity,
            })
            if f.is_issue:
                t.issue_count += 1
                if not t.first_seen:
                    t.first_seen = date
                t.last_seen = date

    for t in per_key.values():
        recent = t.history[-RECENT_WINDOW:]
        t.recent_issue_count = sum(1 for h in recent if h["severity"] != OK)

        # Aktuelle Serie: wie viele der juengsten Matches in Folge auffaellig
        streak = 0
        for h in reversed(t.history):
            if h["severity"] == OK:
                break
            streak += 1
        t.current_streak = streak

        # Median statt Mittelwert: ein einzelnes Ausnahme-Match (etwa 21/0 in
        # einer abgebrochenen Partie) wuerde den Mittelwert des Fensters
        # vollstaendig dominieren und einen Verlauf vortaeuschen.
        head = t.history[:RECENT_WINDOW]
        t.first_value = round(median(h["value"] for h in head), 2)
        t.recent_value = round(median(h["value"] for h in recent), 2)

        t.status = _status_for(t, recent)

    order = {CHRONIC: 0, NEW: 1, STABLE: 2, IMPROVING: 3, RESOLVED: 4}
    return sorted(
        per_key.values(),
        key=lambda t: (order.get(t.status, 9), -t.current_streak, -t.issue_count),
    )


def _status_for(t: Track, recent: list[dict]) -> str:
    if t.issue_count == 0:
        return STABLE

    recent_ratio = t.recent_issue_count / len(recent) if recent else 0

    # Aktuell unauffaellig, frueher problematisch
    if t.current_streak == 0:
        return RESOLVED if recent_ratio == 0 else IMPROVING

    if recent_ratio >= CHRONIC_RATIO:
        # Trat der Befund nur in den juengsten Matches auf, ist er neu
        older_issues = t.issue_count - t.recent_issue_count
        return NEW if older_issues == 0 and t.total_matches > len(recent) else CHRONIC

    return STABLE
