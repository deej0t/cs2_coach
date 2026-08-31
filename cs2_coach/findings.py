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
    # Aeltere Exports kennen den bevorzugten Schluessel noch nicht.
    fallback_path: tuple[str, ...] | None = None
    # True, wenn der Wert auch dann steigt, wenn schlicht die Runde gewonnen
    # wurde. Solche Metriken korrelieren zwangslaeufig mit dem Ergebnis und
    # taugen nicht als Ursache - wer Runden gewinnt, ueberlebt sie auch.
    outcome_driven: bool = False

    def extract(self, player: dict) -> float | None:
        for path in (self.path, self.fallback_path):
            if path is None:
                continue
            node = player
            for part in path:
                if not isinstance(node, dict) or part not in node:
                    node = None
                    break
                node = node[part]
            if node is None or isinstance(node, bool):
                continue
            if isinstance(node, (int, float)):
                return float(node)
        return None

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
        path=("crosshair_placement", "median_degrees"),
        fallback_path=("crosshair_placement", "avg_degrees"),
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
        outcome_driven=True,
        label="ADR",
        path=("adr",),
        critical=65.0, warning=80.0, lower_is_better=False,
        advice="Mehr Kontakt in der Rundenmitte suchen, nicht nur Entry oder Rest.",
    ),
    Rule(
        key="kd",
        outcome_driven=True,
        label="K/D",
        path=("kd",),
        critical=0.9, warning=1.1, lower_is_better=False,
        advice="Weniger unnoetige Duelle ohne Trade-Absicherung.",
    ),
    Rule(
        key="kast",
        outcome_driven=True,
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
        outcome_driven=True,
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


# ── Persoenliche Baseline ───────────────────────────────────────────────

# Ab so vielen Matches ist eine persoenliche Verteilung aussagekraeftig
# genug, um Perzentile darauf zu stuetzen.
MIN_MATCHES_FOR_BASELINE = 10


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    i = int(len(sorted_vals) * p / 100)
    return sorted_vals[min(i, len(sorted_vals) - 1)]


@dataclass
class Baseline:
    """Verteilung einer Metrik ueber die eigene Historie.

    Die absoluten Schwellen in RULES beantworten "ist das nach
    CS2-Massstab gut?". Sie sagen aber nichts darueber, ob ein Match fuer
    diesen Spieler ungewoehnlich war. Bei einem Spieler, dessen bestes
    Zehntel unter der Warnschwelle liegt, feuert die Regel in nahezu jedem
    Match und trennt gute nicht mehr von schlechten Tagen.
    """

    key: str
    label: str
    unit: str = ""
    n: int = 0
    p10: float = 0.0
    p25: float = 0.0
    p50: float = 0.0
    p75: float = 0.0
    p90: float = 0.0
    always_fires: bool = False
    never_fires: bool = False

    @property
    def is_uninformative(self) -> bool:
        """Regel, die praktisch immer oder nie ausloest, ordnet nichts ein."""
        return self.always_fires or self.never_fires

    def percentile_of(self, value: float) -> int:
        """Wo liegt *value* in der eigenen Historie? 0 = schlechtester Wert."""
        rule = RULES_BY_KEY.get(self.key)
        if rule is None or self.n == 0:
            return 50
        better = sum(1 for v in self._vals if (v > value) == rule.lower_is_better)
        return round(better / self.n * 100)

    _vals: list[float] = field(default_factory=list)


def build_baselines(matches: list[dict]) -> dict[str, Baseline]:
    """Persoenliche Verteilung je Metrik aus der eigenen Historie."""
    vals: dict[str, list[float]] = {}
    for m in matches:
        for f in evaluate(m.get("player") or {}):
            vals.setdefault(f.key, []).append(f.value)

    out: dict[str, Baseline] = {}
    for key, v in vals.items():
        if len(v) < MIN_MATCHES_FOR_BASELINE:
            continue
        rule = RULES_BY_KEY[key]
        sv = sorted(v)
        hits = sum(1 for x in v if rule.severity_for(x) != OK)
        b = Baseline(
            key=key, label=rule.label, unit=rule.unit, n=len(v),
            p10=round(_percentile(sv, 10), 2), p25=round(_percentile(sv, 25), 2),
            p50=round(_percentile(sv, 50), 2), p75=round(_percentile(sv, 75), 2),
            p90=round(_percentile(sv, 90), 2),
            always_fires=hits >= len(v) * 0.9,
            never_fires=hits <= len(v) * 0.05,
        )
        b._vals = sv
        out[key] = b
    return out


# ── Zusammenhang mit dem Spielausgang ───────────────────────────────────

MIN_MATCHES_PER_OUTCOME = 8


@dataclass
class Relevance:
    """Wie stark trennt eine Metrik Siege von Niederlagen?"""

    key: str
    label: str
    unit: str = ""
    mean_win: float = 0.0
    mean_loss: float = 0.0
    effect: float = 0.0          # Cohens d, auf "hoeher ist besser" normiert
    outcome_driven: bool = False

    @property
    def strength(self) -> str:
        a = abs(self.effect)
        if a >= 0.8:
            return "gross"
        if a >= 0.5:
            return "mittel"
        if a >= 0.2:
            return "klein"
        return "keiner"


def build_relevance(matches: list[dict]) -> list[Relevance]:
    """Effektstaerke je Metrik zwischen Siegen und Niederlagen.

    Wichtig bei der Deutung: Metriken mit outcome_driven=True steigen auch
    dann, wenn schlicht die Runde gewonnen wurde. Ihr Zusammenhang mit dem
    Ergebnis ist teilweise ein Zirkelschluss und keine Ursache. Verlaesslich
    interpretierbar sind vor allem die uebrigen, weil sie Verhalten
    abbilden, das unabhaengig vom Rundenausgang gesteuert wird.
    """
    wins: dict[str, list[float]] = {}
    losses: dict[str, list[float]] = {}

    for m in matches:
        result = m.get("result", "")
        if result not in ("Sieg", "Niederlage"):
            continue
        target = wins if result == "Sieg" else losses
        for f in evaluate(m.get("player") or {}):
            target.setdefault(f.key, []).append(f.value)

    out: list[Relevance] = []
    for key, w in wins.items():
        l = losses.get(key, [])
        if len(w) < MIN_MATCHES_PER_OUTCOME or len(l) < MIN_MATCHES_PER_OUTCOME:
            continue
        rule = RULES_BY_KEY[key]
        mw, ml = sum(w) / len(w), sum(l) / len(l)
        pooled = _stdev(w + l)
        effect = (mw - ml) / pooled if pooled else 0.0
        if rule.lower_is_better:
            effect = -effect
        out.append(Relevance(
            key=key, label=rule.label, unit=rule.unit,
            mean_win=round(mw, 2), mean_loss=round(ml, 2),
            effect=round(effect, 2), outcome_driven=rule.outcome_driven,
        ))

    out.sort(key=lambda r: -abs(r.effect))
    return out


def _stdev(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    return (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5
