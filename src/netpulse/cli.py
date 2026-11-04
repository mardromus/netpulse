"""netpulse command line."""

from __future__ import annotations

import json
import re
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .model import severity_rank
from .store import Store

app = typer.Typer(add_completion=False, no_args_is_help=True, rich_markup_mode="rich",
                  help="AI-generated analytics and anomaly reports from network counters and metrics.")
console = Console()
SEV_STYLE = {"critical": "bold white on red", "high": "bold red", "medium": "yellow", "low": "cyan"}


def _duration(text: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd]?)", text.strip())
    if not m:
        raise typer.BadParameter(f"bad duration {text!r} (use 30s, 10m, 2h)")
    n, unit = float(m.group(1)), m.group(2) or "s"
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def _incident_table(result) -> Table:
    t = Table(title=f"{len(result.incidents)} incidents", show_lines=False, header_style="bold")
    for col in ("id", "severity", "kind", "title", "start (IST)", "min"):
        t.add_column(col, overflow="fold")
    for i in sorted(result.incidents, key=lambda x: (-severity_rank(x.severity), x.start)):
        t.add_row(i.id, f"[{SEV_STYLE.get(i.severity, '')}]{i.severity}[/]", i.kind.replace("_", " "), i.title,
                  i.start.tz_convert("Asia/Kolkata").strftime("%a %d %b %H:%M"), f"{i.duration_min:.0f}")
    return t


def _eval_table(ev) -> Table:
    d = ev.to_dict()
    t = Table(title=f"detection vs ground truth: precision {d['precision']}  recall {d['recall']}  "
                    f"F1 {d['f1']}  kind accuracy {d['kind_accuracy']}", header_style="bold")
    for col in ("injected event", "entity", "incident", "classified as", "delay min"):
        t.add_column(col)
    for m in d["matches"]:
        ok = "[green]" if m["kind_ok"] else "[red]"
        t.add_row(m["event"].replace("_", " "), m["entity"], m["incident"] or "[red]missed[/]",
                  f"{ok}{(m['incident_kind'] or '-').replace('_', ' ')}[/]", str(m["delay_min"]))
    return t


LAST_HELP = "only analyse the final window of the data, e.g. 24h or 2d"


def _analyze(db: Path, sensitivity: float = 1.0, last: str | None = None):
    import pandas as pd

    from .detect import DetectOptions
    from .pipeline import analyze_store

    if not db.exists():
        raise typer.BadParameter(f"{db} does not exist")
    window = pd.Timedelta(seconds=_duration(last)) if last else None
    with console.status("analysing"):
        return analyze_store(Store(db), DetectOptions(sensitivity=sensitivity), window)


def _write_reports(result, html: Path | None, md: Path | None, js: Path | None, ai: bool, inline: bool):
    from .report import build_report, render_html, render_json, render_markdown

    with console.status("writing the narrative" + (" with the LLM" if ai else "")):
        rep = build_report(result, use_ai=ai)
    if html:
        html.parent.mkdir(parents=True, exist_ok=True)
        html.write_text(render_html(rep, inline_plotly=inline), encoding="utf-8")
        console.print(f"[green]wrote[/] {html}")
    if md:
        md.write_text(render_markdown(rep), encoding="utf-8")
        console.print(f"[green]wrote[/] {md}")
    if js:
        js.write_text(render_json(rep), encoding="utf-8")
        console.print(f"[green]wrote[/] {js}")
    n = rep.narrative
    badge = "verified" if n.verified else f"{n.removed_sentences} sentence(s) removed"
    console.print(f"\n[bold]{n.headline}[/]  [dim]({n.engine}, {badge})[/]")
    if n.notes:
        console.print(f"[dim]{n.notes}[/]")
    return rep


@app.command()
def version():
    """Print the version."""
    console.print(f"netpulse {__version__}")


@app.command()
def generate(
    out: Path = typer.Option(Path("data.db"), "-o", "--out", help="SQLite file to write"),
    days: int = typer.Option(7, help="days of data"),
    step: int = typer.Option(300, help="seconds between samples"),
    seed: int = typer.Option(7),
    quiet: bool = typer.Option(False, "--quiet", help="no injected incidents"),
):
    """Generate a realistic week of network telemetry with labelled incidents."""
    from .generate import GenConfig
    from .generate import generate as gen

    g = gen(GenConfig(days=days, step_s=step, seed=seed, incidents=not quiet))
    if out.exists():
        out.unlink()
    s = Store(out)
    s.write_entities(g.entities)
    n = s.write_samples(g.samples)
    s.write_truth(g.truth)
    s.set_meta("source", "synthetic")
    console.print(f"[green]wrote[/] {n:,} samples, {len(g.entities)} entities, {len(g.truth)} labelled "
                  f"incidents to {out}")


@app.command()
def collect(
    out: Path = typer.Option(Path("live.db"), "-o", "--out"),
    interval: float = typer.Option(5.0, help="seconds between polls"),
    duration: str = typer.Option("10m", help="how long to collect (30s, 10m, 2h)"),
    probe: list[str] = typer.Option([], "--probe", "-p",
                                    help="tcp://host:port, http(s)://url or dns://name (repeatable)"),
    system: bool = typer.Option(True, "--system/--no-system", help="local interfaces and host counters"),
):
    """Collect live counters from this machine and probes into a store."""
    from .collect import run
    from .collect.probes import probe_for
    from .collect.system import HostCollector, InterfaceCollector

    collectors = [InterfaceCollector(), HostCollector()] if system else []
    collectors += [probe_for(p) for p in probe]
    if not collectors:
        raise typer.BadParameter("nothing to collect: add --probe or enable --system")
    secs = _duration(duration)
    console.print(f"collecting every {interval:g}s for {duration} from {len(collectors)} collector(s) "
                  f"into {out} (Ctrl-C to stop early)")
    n = run(collectors, Store(out), interval_s=interval, duration_s=secs)
    console.print(f"[green]wrote[/] {n:,} samples")


@app.command("import")
def import_(
    file: Path = typer.Argument(..., help="CSV or Prometheus text file (or an http(s) /metrics URL)"),
    out: Path = typer.Option(Path("data.db"), "-o", "--out"),
    fmt: str = typer.Option("auto", "--format", help="csv | prom | auto"),
    entity: str = typer.Option(None, help="entity name for wide CSVs without an entity column"),
):
    """Import existing data (CSV long/wide, SNMP column names, Prometheus exposition)."""
    from .collect.importers import parse_prometheus, read_csv, scrape_prometheus

    src = str(file)
    if src.startswith(("http://", "https://")):
        df = scrape_prometheus(src)
    elif fmt == "prom" or (fmt == "auto" and file.suffix in (".prom", ".txt")):
        df = parse_prometheus(file.read_text())
    else:
        df = read_csv(file, entity)
    n = Store(out).write_samples(df)
    console.print(f"[green]imported[/] {n:,} samples ({df['entity'].nunique()} entities, "
                  f"{df['metric'].nunique()} metrics) into {out}")


@app.command()
def detect(
    db: Path = typer.Argument(..., exists=True),
    as_json: bool = typer.Option(False, "--json"),
    sensitivity: float = typer.Option(1.0, help=">1 flags smaller deviations, <1 only larger ones"),
    last: str = typer.Option(None, help=LAST_HELP),
):
    """Detect anomalies and print incidents."""
    r = _analyze(db, sensitivity, last)
    if as_json:
        print(json.dumps([i.to_dict() for i in r.incidents], indent=2))
        return
    console.print(_incident_table(r))
    for i in sorted(r.incidents, key=lambda x: -severity_rank(x.severity))[:3]:
        console.print(f"\n[bold]{i.id}[/] {i.probable_cause}")
    if r.evaluation:
        console.print()
        console.print(_eval_table(r.evaluation))
    raise typer.Exit(1 if any(i.severity in ("critical", "high") for i in r.incidents) else 0)


@app.command()
def report(
    db: Path = typer.Argument(..., exists=True),
    out: Path = typer.Option(Path("netpulse-report.html"), "-o", "--out"),
    md: Path = typer.Option(None, "--md", help="also write Markdown"),
    js: Path = typer.Option(None, "--json", help="also write JSON"),
    ai: bool = typer.Option(False, "--ai", help="write the narrative with an LLM (verified)"),
    inline: bool = typer.Option(False, "--inline", help="embed plotly.js for offline viewing"),
    sensitivity: float = typer.Option(1.0, help="detector sensitivity multiplier"),
    last: str = typer.Option(None, help=LAST_HELP),
):
    """Build the analytics and anomaly report."""
    r = _analyze(db, sensitivity, last)
    _write_reports(r, out, md, js, ai, inline)


@app.command()
def ask(
    db: Path = typer.Argument(..., exists=True),
    question: str = typer.Argument(..., help='e.g. "what happened to payments-svc?"'),
    ai: bool = typer.Option(False, "--ai", help="answer with the LLM (numbers verified)"),
):
    """Ask a question about the analysis in plain language."""
    from .ai.ask import ask as ask_fn
    from .ai.facts import build_facts

    r = _analyze(db)
    a = ask_fn(question, build_facts(r), use_ai=ai)
    console.print(a.answer)
    meta = f"{a.engine}, confidence {a.confidence:.0%}" + ("" if a.verified else ", some sentences removed")
    if a.incidents:
        meta += ", incidents " + ", ".join(a.incidents)
    console.print(f"[dim]({meta}){' ' + a.notes if a.notes else ''}[/]")


@app.command("eval")
def eval_(db: Path = typer.Argument(..., exists=True)):
    """Score detection against the ground truth stored with synthetic data."""
    r = _analyze(db)
    if not r.evaluation:
        console.print("[yellow]no ground truth in this store[/] (generate data with `netpulse generate`)")
        raise typer.Exit(2)
    console.print(_eval_table(r.evaluation))


@app.command()
def demo(
    out: Path = typer.Option(Path("out"), "--out"),
    ai: bool = typer.Option(False, "--ai"),
    seed: int = typer.Option(7),
    inline: bool = typer.Option(True, "--inline/--cdn"),
):
    """Generate a week of data, detect, evaluate and write the full report in one go."""
    out.mkdir(parents=True, exist_ok=True)
    db = out / "demo.db"
    generate(out=db, days=7, step=300, seed=seed, quiet=False)
    r = _analyze(db)
    console.print(_incident_table(r))
    if r.evaluation:
        console.print(_eval_table(r.evaluation))
    _write_reports(r, out / "netpulse-report.html", out / "netpulse-report.md", out / "netpulse-report.json",
                   ai, inline)
    console.print(f"\nopen {out / 'netpulse-report.html'}   |   dashboard: netpulse serve {db}")


@app.command()
def serve(
    db: Path = typer.Argument(None, help="store to serve (omit with --live)"),
    live: bool = typer.Option(False, "--live", help="collect from this machine while serving"),
    interval: float = typer.Option(5.0),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8050),
    probe: list[str] = typer.Option([], "--probe", "-p"),
):
    """Run the web dashboard (API + live view)."""
    try:
        import uvicorn

        from .server.app import create_app
    except ImportError as exc:
        console.print("[red]the dashboard needs extras:[/] pip install 'netpulse[server]'")
        raise typer.Exit(2) from exc
    path = db or Path("live.db")
    application = create_app(path, live=live, interval=interval, probes=probe)
    console.print(f"netpulse dashboard on http://{host}:{port}  ({'live' if live else path})")
    uvicorn.run(application, host=host, port=port, log_level="warning")


def main():  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()

