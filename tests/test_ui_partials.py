"""Designsystem: shared macros (_partials/ui.html), coach.css, no hard-coded semantic hex in the theater."""
import re
from pathlib import Path

import pytest

TPL = Path(__file__).resolve().parents[1] / "cs2_coach" / "web" / "templates"


@pytest.fixture(scope="module")
def client():
    from cs2_coach.web.app import create_app
    app = create_app()
    app.config["PROPAGATE_EXCEPTIONS"] = False
    return app.test_client()


@pytest.fixture
def ui(client):
    app = client.application
    from flask import render_template_string   # applies the context processors (i18n "_")
    with app.test_request_context():
        yield lambda src, **kw: render_template_string("{% import '_partials/ui.html' as ui with context %}" + src, **kw)


def test_verdict_maps_each_evidence_verdict(ui):
    assert "badge--pos" in ui("{{ ui.verdict('solid_positive') }}")
    neg = ui("{{ ui.verdict('solid_negative') }}")
    assert "badge--neg" in neg and "badge--pos" not in neg          # levers.html used to show this green
    und = ui("{{ ui.verdict('undecided', 40) }}")
    assert "badge--undecided" in und and "40" in und               # grey dashed, not yellow
    assert "badge--negligible" in ui("{{ ui.verdict('solid_negligible') }}")


def test_ci_bar_and_side(ui):
    out = ui("{{ ui.ci_bar(0.4, -0.2, 9, 'undecided') }}")
    assert "ci-bar undecided" in out and "width:" in out
    assert "side--t" in ui("{{ ui.side(2) }}") and "side--ct" in ui("{{ ui.side('CT') }}")
    assert "state--error" in ui("{{ ui.state('error', 'x') }}")


def test_coach_css_is_served_and_linked(client):
    css = client.get("/static/coach.css")
    assert css.status_code == 200
    body = css.get_data(as_text=True)
    for tok in ("--c-neg", "--c-pos", "--c-focus", "--c-info", "--c-util", "--c-ct", "--c-t", ".badge--undecided"):
        assert tok in body
    assert "coach.css" in (TPL / "base.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["moment_theater.html", "moment_story.html", "moment_timeline.html",
                                  "replay_scene_js.html", "aim_trace_js.html"])
def test_semantic_colours_come_from_css(name):
    """Theater JS reads semantic colours via uiColor()/var(); hex only as fallback inside uiColor()."""
    src = (TPL / "_partials" / name).read_text(encoding="utf-8")
    stripped = re.sub(r"uiColor\('[a-z0-9]+', '#[0-9a-fA-F]{6}'\)", "", src)
    for hexv in ("#f87171", "#4ade80", "#fbbf24", "#c084fc"):
        assert hexv not in stripped.lower(), f"{name}: {hexv} hart codiert"


def test_old_violet_accent_is_gone():
    """Violett ist nur noch Utility (--c-util); der alte Akzent #9184d9 darf nicht zurueckkommen."""
    hits = [p.name for p in TPL.rglob("*.html") if re.search(r"#9184d9|145,\s*132,\s*217", p.read_text(encoding="utf-8"), re.I)]
    assert not hits, hits


def test_no_thick_left_colour_bars():
    """Designsystem: Ton = 1px getoenter Rahmen (.callout/.focus-card), keine dicken linken Balken mehr."""
    bad = [f"{p.name}: {m.group(0)}" for p in TPL.rglob("*.html") if not p.name.startswith("moment_")
           for m in re.finditer(r"border-left:\s*[2-9]px", p.read_text(encoding="utf-8"))]
    assert not bad, bad


def test_round_strip_stat_and_mission_card(ui):
    out = ui("{{ ui.round_strip(r) }}", r=[{"won": True, "side": "CT", "gap": False, "kills": 2, "title": "R1"},
                                           {"won": False, "side": "T", "gap": True, "kills": 0, "title": "R2"}])
    assert "rs--won rs--ct" in out and "rs--lost rs--t rs--gap" in out and "side--ct" in out
    st = ui("{{ ui.stat('Ziel', 42, ' %', tone='focus', id='soll') }}")
    assert 'id="soll"' in st and "stat--focus" in st and "42" in st
    g = {"recent": 55.0, "fill": 80, "unit": "%", "target": 60.0, "label": "Frühe Granate", "early": True,
         "checks": [{"ok": True, "map": "Mirage", "k": 4, "n": 6}], "hits": 1, "verdict": "solid_positive", "since": "1.11."}
    html = ui("{% include '_partials/mission_card.html' %}", g=g)
    assert "Deine Mission" in html and "var(--c-focus)" in html and "Mirage 4/6" in html
    assert "Deine Mission" in ui("{% include '_partials/mission_card.html' %}", g=None)


def test_old_palette_hexes_only_as_uicolor_fallback():
    """Alte Bedeutungs-Hexwerte nur noch als Ersatz in uiColor(); CT/T nie im Info-Blau/Fokus-Amber-Orange."""
    old = re.compile(r"#(4ade80|22c55e|f87171|ef4444|fbbf24|facc15|60a5fa|93c5fd|c084fc|7eaadb|deb23e|e0a33a)\b", re.I)
    bad = []
    for p in TPL.rglob("*.html"):
        src = re.sub(r"uiColor\('[a-z0-9]+', '#[0-9a-fA-F]{6}'\)", "", p.read_text(encoding="utf-8"))
        bad += [f"{p.name}: {m.group(0)}" for m in old.finditer(src)]
    assert not bad, bad[:10]
    sides = (TPL / "sides.html").read_text(encoding="utf-8")
    assert "#f59e0b" not in sides.lower() and "#5da9f2" not in sides.lower()


def test_story_lines_link_into_theater(ui):
    out = ui("{{ ui.story_lines(s, 'x_coach.json') }}",
             s=[{"round": 7, "tick": 900, "kind": "death", "verdict": "Hauptgrund: Allein", "tone": "bad", "trigger": "t"}])
    assert "story-line--bad" in out and "#momente?r=7&t=900&k=death" in out
    assert ui("{{ ui.story_lines([], 'x') }}").strip() == ""


def test_tooltips_are_positioned_by_script():
    """abbr-Tooltips: kein CSS-::after mehr (wurde am Kartenrand abgeschnitten), sondern .ui-tip aus base.html."""
    css = (TPL.parent / "static" / "coach.css").read_text(encoding="utf-8")
    assert "abbr[title]:hover::after" not in css and ".ui-tip" in css
    assert "ui-tip" in (TPL / "base.html").read_text(encoding="utf-8")


def test_death_is_visible_in_3d():
    """3D: Tote fallen (corpse) statt zu verschwinden, Ego-Sicht bekommt Todeskamera + Treffer-Rot."""
    js = (TPL / "_partials" / "moment_3d_js.html").read_text(encoding="utf-8")
    for name in ("function buildDeaths", "function corpse", "function deathCam", "function hurtOverlay", "buildDeaths();"):
        assert name in js, name
    assert 'id="km-hurt"' in (TPL / "_partials" / "moment_theater.html").read_text(encoding="utf-8")
