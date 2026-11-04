import json

import pytest
from typer.testing import CliRunner

from netpulse.cli import _duration, app

runner = CliRunner()


def test_duration_parser():
    assert _duration("30s") == 30
    assert _duration("10m") == 600
    assert _duration("2h") == 7200
    assert _duration("5") == 5


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("cli") / "d.db"
    res = runner.invoke(app, ["generate", "-o", str(path), "--days", "7"])
    assert res.exit_code == 0, res.output
    return path


def test_generate_detect_eval(db):
    res = runner.invoke(app, ["detect", str(db)])
    assert res.exit_code == 1  # critical incidents present
    assert "INC-01" in res.output and "outage" in res.output
    res = runner.invoke(app, ["detect", str(db), "--json"])
    incidents = json.loads(res.output)
    assert len(incidents) == 6
    res = runner.invoke(app, ["eval", str(db)])
    assert res.exit_code == 0 and "F1 1.0" in res.output


def test_report_command_writes_files(db, tmp_path):
    html, md, js = tmp_path / "r.html", tmp_path / "r.md", tmp_path / "r.json"
    res = runner.invoke(app, ["report", str(db), "-o", str(html), "--md", str(md), "--json", str(js)])
    assert res.exit_code == 0, res.output
    assert html.read_text().startswith("<!DOCTYPE html>")
    assert md.read_text().startswith("# ")
    assert json.loads(js.read_text())["facts"]["counts"]["incidents"] == 6


def test_import_csv_and_quiet_generate(tmp_path):
    csv = tmp_path / "x.csv"
    csv.write_text("ts,entity,metric,value\n2026-11-04T00:00:00Z,r1,rx_bytes,10\n"
                   "2026-11-04T00:05:00Z,r1,rx_bytes,20\n")
    res = runner.invoke(app, ["import", str(csv), "-o", str(tmp_path / "i.db")])
    assert res.exit_code == 0 and "imported 2 samples" in res.output
    res = runner.invoke(app, ["eval", str(tmp_path / "i.db")])
    assert res.exit_code != 0


def test_ask_command(db):
    res = runner.invoke(app, ["ask", str(db), "which link needs a capacity upgrade?"])
    assert res.exit_code == 0 and "core-rtr-1/xe-0/0/1" in res.output
