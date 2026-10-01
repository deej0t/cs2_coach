"""Wie belastbar ist ein Unterschied zwischen zwei Gruppen?

Mehrere Seiten geben Ratschlaege auf Grundlage eines Vergleichs: Rating
nach Siegen gegen Rating nach Niederlagen (Tilt), fruehe gegen spaete
Matches einer Session (Ermuedung), CT- gegen T-Pistolrunden. Bisher
verglichen sie zwei Mittelwerte mit einer festen Schwelle - "Tilt-Effekt
erkannt, mach eine Pause", sobald die Differenz 0.08 ueberstieg. Wie
viele Matches dahinterstehen und ob die Differenz ueberhaupt von Rauschen
zu unterscheiden ist, spielte keine Rolle.

Wie leicht das schiefgeht, hat die Utility-Auswertung gezeigt: der
Unterschied sah bei 58 Matches ueberzeugend aus und war statistisch
unentschieden.

Dieses Modul ist die eine Stelle, an der so ein Vergleich gerechnet
wird. findings.py und utility_analysis.py nutzen es ebenso wie die
Seiten in web/app.py.

Zwei Arten von Vergleichen:

``compare_groups``
    Mittelwerte zweier Gruppen von Messwerten (Rating, ADR, ...).
    Effektstaerke Cohens d.

``compare_rates``
    Zwei Quoten aus Zaehlungen (Pistol-Winrate CT gegen T).
    Effektstaerke Cohens h, die fuer Anteile gedachte Entsprechung zu d
    mit denselben Konventionen (0.2 klein, 0.5 mittel, 0.8 gross).

Beide liefern denselben Vergleichstyp mit Vertrauensbereich und Urteil.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

#: Unter dieser Gruppengroesse wird gar nichts behauptet.
MIN_PER_GROUP = 8

#: Ab dieser Effektstaerke gilt ein Zusammenhang als praktisch bedeutsam
#: (Cohens Konvention fuer "klein").
MEANINGFUL_EFFECT = 0.2

SOLID_POSITIVE = "solid_positive"
SOLID_NEGATIVE = "solid_negative"
SOLID_NEGLIGIBLE = "solid_negligible"
UNDECIDED = "undecided"

_Z95 = 1.96


def stdev(vals: list[float]) -> float:
    """Standardabweichung (Grundgesamtheit), 0 bei weniger als zwei Werten."""
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    return (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5


def _z_for(comparisons: int) -> float:
    """z-Wert fuer 95 %, bei mehreren Vergleichen nach Bonferroni verschaerft.

    Wer aus sieben Wochentagen den besten heraussucht und dann prueft, ob
    er sich abhebt, findet fast immer etwas. Je mehr Kandidaten, desto
    breiter muss der Bereich sein.
    """
    if comparisons <= 1:
        return _Z95
    return NormalDist().inv_cdf(1 - 0.05 / (2 * comparisons))


def effect_ci(d: float, n1: int, n2: int, z: float = _Z95) -> tuple[float, float]:
    """Vertrauensbereich fuer Cohens d (Hedges/Olkin-Naeherung)."""
    if n1 < 2 or n2 < 2:
        return (d, d)
    se = math.sqrt((n1 + n2) / (n1 * n2) + d * d / (2 * (n1 + n2)))
    return (round(d - z * se, 2), round(d + z * se, 2))


def verdict_for(lo: float, hi: float) -> str:
    if lo > MEANINGFUL_EFFECT:
        return SOLID_POSITIVE
    if hi < -MEANINGFUL_EFFECT:
        return SOLID_NEGATIVE
    if lo > -MEANINGFUL_EFFECT and hi < MEANINGFUL_EFFECT:
        return SOLID_NEGLIGIBLE
    return UNDECIDED


def matches_needed(d: float) -> int:
    """Wie viele Faelle insgesamt, bis der Bereich die Schwelle verlaesst?

    Null, wenn der Effekt so klein ist, dass auch beliebig viele Faelle
    keine bedeutsame Aussage ergeben wuerden.
    """
    a = abs(d)
    if a <= MEANINGFUL_EFFECT:
        return 0
    se_needed = (a - MEANINGFUL_EFFECT) / _Z95
    per_group = (2 + a * a / 2) / (se_needed ** 2)
    return math.ceil(per_group * 2)


@dataclass
class Comparison:
    """Ergebnis eines Vergleichs zweier Gruppen A und B.

    ``effect`` ist so normiert, dass ein positiver Wert "A ist besser als
    B" bedeutet - bei Metriken, bei denen weniger besser ist, also bereits
    umgedreht.
    """

    n_a: int
    n_b: int
    mean_a: float
    mean_b: float
    effect: float
    ci_low: float
    ci_high: float
    verdict: str
    n_needed: int = 0            # 0 = nicht sinnvoll erreichbar oder unnoetig

    @property
    def a_better(self) -> bool:
        """A ist belastbar besser als B."""
        return self.verdict == SOLID_POSITIVE

    @property
    def b_better(self) -> bool:
        """B ist belastbar besser als A."""
        return self.verdict == SOLID_NEGATIVE

    @property
    def no_difference(self) -> bool:
        """Belegt, dass es keinen nennenswerten Unterschied gibt.

        Das ist etwas anderes als "kein Unterschied gemessen": dafuer muss
        der ganze Bereich innerhalb der Bedeutungsschwelle liegen.
        """
        return self.verdict == SOLID_NEGLIGIBLE

    @property
    def undecided(self) -> bool:
        return self.verdict == UNDECIDED

    def note(self, unit: str = "Matches") -> str:
        """Kurzer Zusatz fuer einen Hinweistext, auf Deutsch."""
        ci = f"d={self.effect} [{self.ci_low} … {self.ci_high}]"
        if not self.undecided:
            return f"belastbar, {ci}"
        tail = f", ab ca. {self.n_needed} {unit} entscheidbar" if self.n_needed else ""
        return (f"noch nicht belastbar bei {self.n_a} gegen {self.n_b} {unit}, "
                f"{ci}{tail}")


def compare_groups(a: list[float], b: list[float], *,
                   higher_is_better: bool = True,
                   min_n: int = MIN_PER_GROUP,
                   comparisons: int = 1) -> Comparison | None:
    """Mittelwerte zweier Gruppen. None, wenn eine Gruppe zu klein ist.

    Die Streuung ist die der beiden Gruppen zusammen - so rechnete
    findings.build_relevance() schon immer, und die dort dokumentierten
    Werte sollen sich durch dieses Modul nicht verschieben.
    """
    if len(a) < min_n or len(b) < min_n:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    sd = stdev(list(a) + list(b))
    effect = (ma - mb) / sd if sd else 0.0
    if not higher_is_better:
        effect = -effect
    effect = round(effect, 2)
    lo, hi = effect_ci(effect, len(a), len(b), _z_for(comparisons))
    verdict = verdict_for(lo, hi)
    return Comparison(
        n_a=len(a), n_b=len(b), mean_a=round(ma, 2), mean_b=round(mb, 2),
        effect=effect, ci_low=lo, ci_high=hi, verdict=verdict,
        n_needed=matches_needed(effect) if verdict == UNDECIDED else 0,
    )


def compare_rates(k_a: int, n_a: int, k_b: int, n_b: int, *,
                  min_n: int = MIN_PER_GROUP,
                  comparisons: int = 1) -> Comparison | None:
    """Zwei Quoten k/n, etwa gewonnene von gespielten Pistolrunden.

    Cohens h = 2*asin(sqrt(p_a)) - 2*asin(sqrt(p_b)), Standardfehler
    sqrt(1/n_a + 1/n_b). mean_a und mean_b sind die Quoten in Prozent.
    """
    if n_a < min_n or n_b < min_n:
        return None
    pa, pb = k_a / n_a, k_b / n_b
    h = round(2 * math.asin(math.sqrt(pa)) - 2 * math.asin(math.sqrt(pb)), 2)
    z = _z_for(comparisons)
    se = math.sqrt(1 / n_a + 1 / n_b)
    lo, hi = round(h - z * se, 2), round(h + z * se, 2)
    verdict = verdict_for(lo, hi)
    needed = 0
    if verdict == UNDECIDED and abs(h) > MEANINGFUL_EFFECT:
        se_needed = (abs(h) - MEANINGFUL_EFFECT) / _Z95
        needed = math.ceil(2 / se_needed ** 2) * 2
    return Comparison(
        n_a=n_a, n_b=n_b, mean_a=round(pa * 100, 1), mean_b=round(pb * 100, 1),
        effect=h, ci_low=lo, ci_high=hi, verdict=verdict, n_needed=needed,
    )
