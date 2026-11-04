"""Regenerate the files in examples/ (run from the repository root)."""

from pathlib import Path

from netpulse.generate import FIREWALL, GenConfig, generate
from netpulse.pipeline import analyze
from netpulse.report import build_report, render_html, render_markdown

HERE = Path(__file__).parent


def main():
    g = generate(GenConfig())
    # 1) a wide, SNMP-style CSV for one interface (what many NMS tools export)
    fw = g.samples[g.samples["entity"] == FIREWALL]
    wide = fw.pivot_table(index="ts", columns="metric", values="value").reset_index()
    wide = wide.rename(columns={"ts": "timestamp", "rx_bytes": "ifHCInOctets", "tx_bytes": "ifHCOutOctets",
                                "rx_packets": "ifHCInUcastPkts", "tx_packets": "ifHCOutUcastPkts",
                                "rx_errors": "ifInErrors", "rx_drops": "ifInDiscards", "tx_drops": "ifOutDiscards"})
    wide.insert(1, "interface", FIREWALL)
    wide.to_csv(HERE / "edge-firewall-snmp.csv", index=False, float_format="%.0f")
    # 2) the reference report
    r = analyze(g.samples, {e.name: e for e in g.entities}, g.truth)
    rep = build_report(r)
    (HERE / "sample-report.html").write_text(render_html(rep), encoding="utf-8")
    (HERE / "sample-report.md").write_text(render_markdown(rep), encoding="utf-8")


if __name__ == "__main__":
    main()
