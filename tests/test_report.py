import json

import pytest

from netpulse.generate import GenConfig, generate
from netpulse.pipeline import analyze
from netpulse.report import build_report, render_html, render_json, render_markdown


@pytest.fixture(scope="module")
def report():
    g = generate(GenConfig())
    r = analyze(g.samples, {e.name: e for e in g.entities}, g.truth)
    return build_report(r)


def test_html_report_has_all_sections(report):
    html = render_html(report)
    for needle in ("Incident timeline", "INC-01", "Recommended actions", "Capacity outlook",
                   "Data quality", "Detection quality vs ground truth", "cdn.plot.ly",
                   "every figure verified"):
        assert needle in html, needle
    assert html.count('class="inc"') == len(report.facts["incidents"])
    assert "ch-INC-01" in html and '"data"' in html


def test_html_can_inline_plotly(report):
    html = render_html(report, inline_plotly=True)
    assert '<script src="https://cdn.plot.ly' not in html and len(html) > 1_000_000


def test_markdown_and_json(report):
    md = render_markdown(report)
    assert md.startswith("# ")
    assert "## Incidents" in md and "| metric |" in md
    data = json.loads(render_json(report))
    assert data["narrative"]["engine"] == "template"
    assert len(data["incidents"]) == len(report.facts["incidents"])
    assert data["evaluation"]["f1"] == 1.0


def test_report_on_quiet_data_says_healthy():
    g = generate(GenConfig(days=3, incidents=False))
    r = analyze(g.samples, {e.name: e for e in g.entities})
    rep = build_report(r)
    assert "healthy" in rep.headline.lower()
    assert "No incidents" in render_html(rep)
