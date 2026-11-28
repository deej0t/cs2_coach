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
