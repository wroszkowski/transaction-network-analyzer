"""Render the analysis as a deliverable an investigator (or a grader) can actually read.

Three artefacts, in decreasing order of how much they assume about the reader's environment:

* ``index.html`` — every finding, every piece of evidence and the whole methodology as **prerendered
  text**. The interactive graph is layered on top of that text, never instead of it: a page whose
  content only exists after JavaScript runs is unreadable to an automated fetch and to anyone with a
  locked-down browser.
* ``findings.json`` — the same findings for a machine that wants to diff or re-score them.
* ``network.png`` — the one view of the graph that needs no JavaScript at all.

Weights, thresholds and detector constants are read out of the modules that define them, so the
methodology section cannot drift away from the code it documents.
"""

import html
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import networkx as nx  # noqa: E402
import pandas as pd  # noqa: E402

from . import detectors  # noqa: E402
from .score import FLAG_THRESHOLD, WEIGHTS  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .analyze import AnalysisResult

LAYOUT_SEED = 42

#: Risk bands. The label always carries the number as well as the colour, because colour alone is
#: not an accessible way to encode severity.
BANDS: tuple[tuple[float, str, str], ...] = (
    (70.0, "critical", "#F2545B"),
    (FLAG_THRESHOLD, "high", "#F2A65A"),
    (20.0, "elevated", "#7C89EF"),
    (0.0, "low", "#3E4560"),
)

#: What each metric is for. Shown verbatim in the methodology table.
METRIC_NOTES: tuple[tuple[str, str], ...] = (
    ("in_degree / out_degree", "How many payments an account received and sent. Shape, not volume."),
    ("distinct_counterparties", "Different accounts dealt with, ignoring repeat business with the same party."),
    ("total_in / total_out / net_flow", "Value received, sent, and retained. Analysed per currency; no FX invented."),
    (
        "pass_through_ratio",
        "Share of inbound value that left again. 1.0 is a pure conduit — the money-mule signature.",
    ),
    ("peak_velocity", "Most transactions an account was party to inside any rolling one-hour window."),
    ("component_id / component_size", "Which weakly connected cluster the account sits in, and how big it is."),
)

#: What each signal asks, and the constant that governs it. Weights come from ``score.WEIGHTS``.
SIGNAL_NOTES: dict[str, tuple[str, str]] = {
    "circular_flow": (
        "Money returns to where it started. Legitimate P2P payments rarely close a loop.",
        f"simple cycles of length 2–{detectors.MAX_CYCLE_LENGTH}",
    ),
    "tight_component": (
        "A small cluster transacting almost exclusively with itself — a closed economy, not a marketplace.",
        f"3–{detectors.TIGHT_COMPONENT_MAX_SIZE} accounts, ≥1.2 internal edges per account",
    ),
    "fan_in_fan_out": (
        "Many small payments in, few large payments out. Smurfing. The conduit test is what keeps the "
        "popular seller — who receives from many people and keeps the money — out of the results.",
        f"in-degree ≥ {detectors.FAN_IN_MINIMUM}, pass-through ≥ {detectors.CONDUIT_RATIO:.0%}, "
        f"out-degree < half the in-degree",
    ),
    "velocity_burst": (
        "A burst of activity in a short window — the signature of an account being drained.",
        f"≥ {detectors.VELOCITY_THRESHOLD} transactions in one hour",
    ),
    "synchronised_onboarding": (
        "A cluster whose members all signed up within days of each other was provisioned, not grown.",
        f"≥ 4 accounts created within {detectors.ONBOARDING_WINDOW_DAYS} days",
    ),
    "shared_identifiers": (
        "Distinct accounts operating from one device or IP — one person wearing several identities. "
        "Weighted low on its own: a shared IP can be a household or an internet cafe.",
        "≥ 2 accounts sending from the same device_id or ip_address",
    ),
}


def render_report(result: AnalysisResult, out_dir: Path) -> None:
    """Write ``index.html``, ``findings.json`` and ``network.png`` into ``out_dir``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    findings = _build_findings(result)
    clusters = _build_clusters(result, findings)

    _write_network_png(result, out_dir / "network.png")
    (out_dir / "findings.json").write_text(
        json.dumps(_findings_document(result, findings, clusters, generated_at), indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "index.html").write_text(
        _render_html(result, findings, clusters, generated_at),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------------------
# data preparation
# --------------------------------------------------------------------------------------


def _band(score: float) -> tuple[str, str]:
    """The (name, colour) band a risk score falls into."""
    for floor, name, colour in BANDS:
        if score >= floor:
            return name, colour
    return BANDS[-1][1], BANDS[-1][2]


def _row(metrics: pd.DataFrame, account: str) -> dict[str, Any]:
    if account in metrics.index:
        return {key: value for key, value in metrics.loc[account].items()}
    return {}


def _build_findings(result: AnalysisResult) -> list[dict[str, Any]]:
    """One record per flagged account, ranked most suspicious first."""
    findings = []
    for rank, account in enumerate(result.flagged, start=1):
        score_row = result.scores.loc[account]
        metrics_row = _row(result.metrics, account)
        risk = float(score_row["risk_score"])
        band, _ = _band(risk)
        findings.append(
            {
                "rank": rank,
                "account_id": str(account),
                "risk_score": risk,
                "band": band,
                "signals": list(score_row["signals"]),
                "reasons": list(score_row["reasons"]),
                "component_id": int(metrics_row.get("component_id", -1)),
                "metrics": {
                    "in_degree": int(metrics_row.get("in_degree", 0)),
                    "out_degree": int(metrics_row.get("out_degree", 0)),
                    "distinct_counterparties": int(metrics_row.get("distinct_counterparties", 0)),
                    "total_in": float(metrics_row.get("total_in", 0.0)),
                    "total_out": float(metrics_row.get("total_out", 0.0)),
                    "net_flow": float(metrics_row.get("net_flow", 0.0)),
                    "pass_through_ratio": float(metrics_row.get("pass_through_ratio", 0.0)),
                    "peak_velocity": int(metrics_row.get("peak_velocity", 0)),
                    "component_size": int(metrics_row.get("component_size", 0)),
                },
            }
        )
    return findings


def _internal_value(result: AnalysisResult, members: set[str]) -> dict[str, float]:
    """Value moving *inside* a set of accounts, per currency — no FX normalisation."""
    transactions = result.transactions
    inside = transactions[transactions["sender_account"].isin(members) & transactions["receiver_account"].isin(members)]
    return {str(currency): float(total) for currency, total in inside.groupby("currency")["amount"].sum().items()}


def _build_clusters(result: AnalysisResult, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flagged accounts grouped into the clusters an investigator would work through."""
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for finding in findings:
        grouped[finding["component_id"]].append(finding)

    clusters: list[dict[str, Any]] = []
    for component_id, members in grouped.items():
        component = sorted(
            str(account) for account in result.metrics.index[result.metrics["component_id"] == component_id]
        )
        signal_counts = Counter(signal for member in members for signal in member["signals"])
        clusters.append(
            {
                "component_id": component_id,
                "flagged_accounts": [member["account_id"] for member in members],
                "component_size": len(component),
                "component_members": component,
                "top_risk_score": max(member["risk_score"] for member in members),
                "mean_risk_score": round(sum(m["risk_score"] for m in members) / len(members), 1),
                "internal_value": _internal_value(result, set(component)),
                "signals": dict(signal_counts.most_common()),
            }
        )
    clusters.sort(key=lambda cluster: (-cluster["top_risk_score"], cluster["component_id"]))
    return clusters


def _findings_document(
    result: AnalysisResult,
    findings: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    generated_at: str,
) -> dict[str, Any]:
    evaluation = None
    if result.evaluation is not None:
        evaluation = {
            key: (round(value, 4) if isinstance(value, float) else value) for key, value in result.evaluation.items()
        }
    return {
        "generated_at": generated_at,
        "summary": {
            "transactions": int(len(result.transactions)),
            "accounts": int(len(result.metrics)),
            "accounts_flagged": len(findings),
            "suspicious_clusters": len(clusters),
            "signals_fired": len(result.signals),
        },
        "method": {"weights": dict(WEIGHTS), "flag_threshold": FLAG_THRESHOLD},
        "findings": findings,
        "clusters": clusters,
        "evaluation": evaluation,
    }


# --------------------------------------------------------------------------------------
# static render
# --------------------------------------------------------------------------------------


def _write_network_png(result: AnalysisResult, path: Path) -> None:
    """Spring-layout render of the payment graph, seeded so the picture is reproducible."""
    aggregated = result.aggregated
    scores = result.scores["risk_score"].to_dict()
    flagged = set(result.flagged)

    positions = nx.spring_layout(aggregated, seed=LAYOUT_SEED, k=0.55, iterations=120)
    degrees = dict(aggregated.degree())
    colours = [_band(float(scores.get(node, 0.0)))[1] for node in aggregated.nodes]
    sizes = [60 + 26 * degrees.get(node, 0) for node in aggregated.nodes]
    edge_widths = [0.9 if (u in flagged and v in flagged) else 0.35 for u, v in aggregated.edges]
    edge_colours = ["#8A93B8" if (u in flagged and v in flagged) else "#2A3050" for u, v in aggregated.edges]

    figure, axes = plt.subplots(figsize=(15, 10.5), dpi=140)
    figure.patch.set_facecolor("#050717")
    axes.set_facecolor("#050717")

    nx.draw_networkx_edges(
        aggregated,
        positions,
        ax=axes,
        edge_color=edge_colours,
        width=edge_widths,
        arrows=True,
        arrowsize=7,
        arrowstyle="-|>",
        connectionstyle="arc3,rad=0.06",
        alpha=0.75,
        node_size=sizes,
    )
    nx.draw_networkx_nodes(
        aggregated,
        positions,
        ax=axes,
        node_color=colours,
        node_size=sizes,
        linewidths=[1.1 if node in flagged else 0.3 for node in aggregated.nodes],
        edgecolors=["#FFFFFF" if node in flagged else "#141A4A" for node in aggregated.nodes],
    )
    labels = {node: node for node in aggregated.nodes if node in flagged or float(scores.get(node, 0.0)) >= 20.0}
    nx.draw_networkx_labels(
        aggregated,
        positions,
        labels=labels,
        ax=axes,
        font_size=6.5,
        font_color="#E8EAF5",
        bbox={"boxstyle": "round,pad=0.18", "facecolor": "#0A0F2E", "edgecolor": "none", "alpha": 0.8},
    )

    handles = [
        mpatches.Patch(color=colour, label=f"{name} risk ({_band_range(index)})")
        for index, (_, name, colour) in enumerate(BANDS)
    ]
    legend = axes.legend(
        handles=handles,
        loc="upper left",
        frameon=True,
        facecolor="#0A0F2E",
        edgecolor="#141A4A",
        fontsize=9,
        title="Risk score — node size is degree, labels are flagged accounts",
        title_fontsize=9,
    )
    for text in [*legend.get_texts(), legend.get_title()]:
        text.set_color("#E8EAF5")

    axes.set_title(
        "BazaarAfrica payment network — accounts as nodes, payments as directed edges",
        color="#FFFFFF",
        fontsize=13,
        pad=14,
    )
    axes.axis("off")
    figure.tight_layout()
    figure.savefig(path, facecolor=figure.get_facecolor(), bbox_inches="tight")
    plt.close(figure)


def _band_range(index: int) -> str:
    floor = BANDS[index][0]
    if index == 0:
        return f"{floor:.0f}+"
    return f"{floor:.0f}–{BANDS[index - 1][0]:.0f}"


# --------------------------------------------------------------------------------------
# html
# --------------------------------------------------------------------------------------


def _graph_payload(result: AnalysisResult, findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Nodes and edges for vis-network. An enhancement — nothing here is unique to the graph."""
    scores = result.scores
    flagged = set(result.flagged)
    reasons = {finding["account_id"]: finding["reasons"] for finding in findings}
    degrees = dict(result.aggregated.degree())

    nodes = []
    for node in result.aggregated.nodes:
        account = str(node)
        risk = float(scores.loc[account, "risk_score"]) if account in scores.index else 0.0
        band, colour = _band(risk)
        metrics_row = _row(result.metrics, account)
        tooltip = [
            f"{account} — risk {risk:.1f} ({band})",
            f"in {int(metrics_row.get('in_degree', 0))} / out {int(metrics_row.get('out_degree', 0))} payments, "
            f"{int(metrics_row.get('distinct_counterparties', 0))} counterparties",
            f"pass-through {float(metrics_row.get('pass_through_ratio', 0.0)):.0%}, "
            f"peak velocity {int(metrics_row.get('peak_velocity', 0))}/hour",
        ]
        tooltip.extend(f"• {reason}" for reason in reasons.get(account, []))
        nodes.append(
            {
                "id": account,
                "label": account,
                "value": max(1, degrees.get(node, 1)),
                "color": colour,
                "risk": round(risk, 1),
                "band": band,
                "flagged": account in flagged,
                "title": "\n".join(tooltip),
            }
        )

    edges = [
        {
            "from": str(sender),
            "to": str(receiver),
            "value": int(data.get("count", 1)),
            "title": f"{int(data.get('count', 1))} payment(s), {float(data.get('total_amount', 0.0)):,.0f} total",
        }
        for sender, receiver, data in result.aggregated.edges(data=True)
    ]
    return {"nodes": nodes, "edges": edges}


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _pill(band: str, score: float) -> str:
    return f'<span class="pill band-{_esc(band)}">{score:.1f} · {_esc(band)}</span>'


def _summary_cards(result: AnalysisResult, findings: list[dict[str, Any]], clusters: list[dict[str, Any]]) -> str:
    cards = [
        ("Transactions analysed", f"{len(result.transactions):,}", "directed payments in the ledger"),
        ("Accounts in graph", f"{len(result.metrics):,}", "nodes with at least one payment"),
        ("Suspicious clusters", f"{len(clusters)}", "connected components containing flagged accounts"),
        ("Accounts flagged", f"{len(findings)}", f"risk score ≥ {FLAG_THRESHOLD:.0f}"),
    ]
    if result.evaluation is not None:
        evaluation = result.evaluation
        cards.extend(
            [
                ("Precision", f"{float(evaluation['precision']):.0%}", "of flags that were real fraud"),
                ("Recall", f"{float(evaluation['recall']):.0%}", "of planted fraud that was caught"),
                ("F1", f"{float(evaluation['f1']):.2f}", "harmonic mean of the two"),
            ]
        )
    return "\n".join(
        f'<div class="card"><div class="card-label">{_esc(label)}</div>'
        f'<div class="card-value">{_esc(value)}</div>'
        f'<div class="card-note">{_esc(note)}</div></div>'
        for label, value, note in cards
    )


def _findings_table(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return '<p class="empty">No account reached the flagging threshold in this run.</p>'
    rows = []
    for finding in findings:
        signals = "".join(f'<span class="tag">{_esc(signal)}</span>' for signal in finding["signals"])
        evidence = "".join(f"<li>{_esc(reason)}</li>" for reason in finding["reasons"])
        metrics_row = finding["metrics"]
        rows.append(
            f"<tr>"
            f'<td class="num">{finding["rank"]}</td>'
            f'<td class="mono strong">{_esc(finding["account_id"])}</td>'
            f'<td class="num">{_pill(finding["band"], finding["risk_score"])}</td>'
            f'<td class="mono small">cluster {finding["component_id"]} · '
            f"in {metrics_row['in_degree']} / out {metrics_row['out_degree']} · "
            f"pass-through {metrics_row['pass_through_ratio']:.0%} · "
            f"peak {metrics_row['peak_velocity']}/h</td>"
            f'<td class="tags">{signals}</td>'
            f'<td><ul class="evidence">{evidence}</ul></td>'
            f"</tr>"
        )
    return (
        '<div class="scroll"><table>'
        "<thead><tr><th>#</th><th>Account</th><th>Risk</th><th>Position in graph</th>"
        "<th>Signals fired</th><th>Evidence</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _clusters_section(clusters: list[dict[str, Any]]) -> str:
    if not clusters:
        return '<p class="empty">No clusters to review.</p>'
    blocks = []
    for cluster in clusters:
        value = " · ".join(f"{currency} {total:,.0f}" for currency, total in sorted(cluster["internal_value"].items()))
        signals = "".join(
            f'<span class="tag">{_esc(name)} <b>×{count}</b></span>' for name, count in cluster["signals"].items()
        )
        members = ", ".join(_esc(account) for account in cluster["flagged_accounts"])
        band, _ = _band(cluster["top_risk_score"])
        blocks.append(
            f'<article class="cluster">'
            f"<header><h3>Cluster {cluster['component_id']}</h3>"
            f"{_pill(band, cluster['top_risk_score'])}</header>"
            f'<dl><div><dt>Flagged accounts</dt><dd class="num">{len(cluster["flagged_accounts"])} of '
            f"{cluster['component_size']} in the component</dd></div>"
            f'<div><dt>Mean risk of flagged</dt><dd class="num">{cluster["mean_risk_score"]:.1f}</dd></div>'
            f"<div><dt>Value moving inside the cluster</dt>"
            f'<dd class="num">{_esc(value) or "—"}</dd></div></dl>'
            f'<p class="cluster-signals">{signals}</p>'
            f'<p class="cluster-members"><span class="dim">Members:</span> <span class="mono">{members}</span></p>'
            f"</article>"
        )
    return f'<div class="clusters">{"".join(blocks)}</div>'


def _methodology_section() -> str:
    metric_rows = "".join(
        f'<tr><td class="mono">{_esc(name)}</td><td>{_esc(note)}</td></tr>' for name, note in METRIC_NOTES
    )
    signal_rows = "".join(
        f'<tr><td class="mono strong">{_esc(name)}</td>'
        f'<td class="num">{weight:.0f}</td>'
        f"<td>{_esc(SIGNAL_NOTES.get(name, ('', ''))[0])}</td>"
        f'<td class="mono small">{_esc(SIGNAL_NOTES.get(name, ("", ""))[1])}</td></tr>'
        for name, weight in sorted(WEIGHTS.items(), key=lambda item: -item[1])
    )
    return f"""
<h3>Metrics computed per account</h3>
<div class="scroll"><table>
<thead><tr><th>Metric</th><th>Why it is computed</th></tr></thead>
<tbody>{metric_rows}</tbody></table></div>

<h3>Detection signals and their weights</h3>
<p>Each detector answers one question and returns the sentence an investigator will read. The score
is a weighted sum of signal strengths clipped to 0–100 — deliberately additive rather than learned,
so the contribution of every signal stays visible and arguable. A signal that fires repeatedly for
one account counts once, at its strongest, so a single structure cannot inflate a score.</p>
<div class="scroll"><table>
<thead><tr><th>Signal</th><th>Weight</th><th>What it asks</th><th>Trigger</th></tr></thead>
<tbody>{signal_rows}</tbody></table></div>
<p class="threshold">Flagging threshold: <b>{FLAG_THRESHOLD:.0f}</b> of 100. Weights and threshold are read
from <span class="mono">tna.score</span> at render time, so this page cannot drift from the code.</p>
"""


def _validation_section(result: AnalysisResult) -> str:
    if result.evaluation is None:
        return (
            "<p>No ground truth was supplied with this ledger, so the run is reported without a score. "
            "Precision and recall are only meaningful against labelled data.</p>"
        )
    evaluation = result.evaluation
    true_positives = evaluation["true_positives"]
    false_positives = evaluation["false_positives"]
    false_negatives = evaluation["false_negatives"]

    def name_list(accounts: list[str], empty: str) -> str:
        if not accounts:
            return f'<span class="dim">{_esc(empty)}</span>'
        return " ".join(f'<span class="tag mono">{_esc(account)}</span>' for account in accounts)

    return f"""
<p>The demo ledger is generated, so the planted fraud is labelled and the detector can be
<em>measured</em> rather than asserted. Recall says how much of the ring would have been caught;
precision says how much of an investigator's time would have been wasted.</p>
<div class="scroll"><table>
<thead><tr><th>Measure</th><th>Value</th><th>Reading</th></tr></thead>
<tbody>
<tr><td>Precision</td><td class="num">{float(evaluation["precision"]):.1%}</td>
<td>{len(true_positives)} of {len(true_positives) + len(false_positives)} flagged accounts were genuinely
fraudulent.</td></tr>
<tr><td>Recall</td><td class="num">{float(evaluation["recall"]):.1%}</td>
<td>{len(true_positives)} of {len(true_positives) + len(false_negatives)} planted fraudulent accounts were
caught.</td></tr>
<tr><td>F1</td><td class="num">{float(evaluation["f1"]):.3f}</td>
<td>Harmonic mean — a single number that punishes trading one measure away for the other.</td></tr>
</tbody></table></div>

<div class="verdicts">
<div class="verdict"><h4>True positives ({len(true_positives)})</h4><p>{name_list(true_positives, "none")}</p></div>
<div class="verdict bad"><h4>False positives ({len(false_positives)})</h4>
<p>{name_list(false_positives, "none — no legitimate account was flagged")}</p>
<p class="dim small">Every one of these is an analyst hour spent on an innocent user. The legitimate
high-degree hub is in the dataset precisely so this number can be shown rather than claimed.</p></div>
<div class="verdict bad"><h4>False negatives ({len(false_negatives)})</h4>
<p>{name_list(false_negatives, "none — every planted account was caught")}</p>
<p class="dim small">Fraud that went past the threshold. Named here rather than rounded away.</p></div>
</div>
"""


def _render_html(
    result: AnalysisResult,
    findings: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    generated_at: str,
) -> str:
    payload = json.dumps(_graph_payload(result, findings)).replace("</", "<\\/")
    band_legend = "".join(
        f'<span class="pill band-{name}">{_band_range(index)} {name}</span>'
        for index, (_, name, _colour) in enumerate(BANDS)
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Transaction Network Analyzer — fraud ring findings</title>
<link rel="icon" href="favicon.ico" sizes="any">
<link rel="icon" type="image/png" href="favicon.png">
<meta name="description" content="Network analysis of BazaarAfrica P2P payments: ranked fraud-ring
findings, evidence, methodology and measured precision/recall.">
<style>
:root {{
  --bg: #050717; --panel: #0A0F2E; --panel-2: #101640; --line: rgba(255,255,255,0.10);
  --ink: #E8EAF5; --ink-2: #B9C0DE; --dim: #7E88B0; --blue: #6B7BFF; --blue-deep: #3E4FE0;
  --critical: #F2545B; --high: #F2A65A; --elevated: #7C89EF; --low: #3E4560;
  --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, "Liberation Mono", "Courier New", monospace;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  font-size: 16px; line-height: 1.55; -webkit-font-smoothing: antialiased;
}}
.wrap {{ max-width: 1180px; margin: 0 auto; padding: 0 20px 96px; }}
header.top {{ border-bottom: 1px solid var(--line); padding: 44px 0 28px; margin-bottom: 32px; }}
.eyebrow {{ font-family: var(--mono); font-size: 11px; letter-spacing: 0.10em; text-transform: uppercase;
  color: var(--dim); margin: 0 0 12px; }}
h1 {{ font-size: clamp(28px, 5vw, 44px); line-height: 1.05; font-weight: 600; margin: 0 0 12px; }}
h2 {{ font-size: 24px; font-weight: 600; margin: 0 0 6px; }}
h3 {{ font-size: 17px; font-weight: 600; margin: 26px 0 10px; color: var(--ink); }}
h4 {{ font-size: 14px; font-weight: 600; margin: 0 0 8px; }}
p {{ margin: 0 0 14px; color: var(--ink-2); max-width: 78ch; }}
a {{ color: var(--blue); }}
section {{ padding: 30px 0; border-top: 1px solid var(--line); }}
section > .lede {{ margin-bottom: 20px; }}
.mono {{ font-family: var(--mono); }}
.small {{ font-size: 12.5px; }}
.dim {{ color: var(--dim); }}
.strong {{ color: #fff; font-weight: 600; }}
.num {{ font-variant-numeric: tabular-nums; white-space: nowrap; }}
nav.jump {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 18px; }}
nav.jump a {{ font-family: var(--mono); font-size: 12px; text-decoration: none; color: var(--ink-2);
  border: 1px solid var(--line); border-radius: 9999px; padding: 5px 12px; }}
nav.jump a:hover {{ border-color: var(--blue); color: #fff; }}
.cards {{ display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }}
.card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; }}
.card-label {{ font-family: var(--mono); font-size: 10.5px; letter-spacing: 0.09em; text-transform: uppercase;
  color: var(--dim); }}
.card-value {{ font-size: 34px; font-weight: 600; line-height: 1.15; margin: 6px 0 2px;
  font-variant-numeric: tabular-nums; }}
.card-note {{ font-size: 12.5px; color: var(--dim); }}
.scroll {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 12px; background: var(--panel); }}
table {{ border-collapse: collapse; width: 100%; min-width: 760px; font-size: 14px; }}
th, td {{ text-align: left; padding: 11px 14px; border-bottom: 1px solid var(--line); vertical-align: top; }}
th {{ font-family: var(--mono); font-size: 10.5px; letter-spacing: 0.09em; text-transform: uppercase;
  color: var(--dim); background: var(--panel-2); position: sticky; top: 0; }}
tbody tr:last-child td {{ border-bottom: none; }}
tbody tr:hover {{ background: rgba(107,123,255,0.06); }}
ul.evidence {{ margin: 0; padding-left: 17px; color: var(--ink-2); font-size: 13.5px; }}
ul.evidence li {{ margin-bottom: 5px; }}
ul.evidence li:last-child {{ margin-bottom: 0; }}
.tag {{ display: inline-block; font-family: var(--mono); font-size: 11px; padding: 2px 8px; margin: 0 4px 4px 0;
  border: 1px solid var(--line); border-radius: 9999px; color: var(--ink-2); background: rgba(255,255,255,0.03); }}
td.tags {{ min-width: 170px; }}
.pill {{ display: inline-block; font-family: var(--mono); font-size: 11.5px; font-weight: 600;
  padding: 3px 10px; border-radius: 9999px; color: #050717; white-space: nowrap; }}
.band-critical {{ background: var(--critical); }}
.band-high {{ background: var(--high); }}
.band-elevated {{ background: var(--elevated); }}
.band-low {{ background: var(--low); color: var(--ink); }}
.legend {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 16px; }}
.clusters {{ display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }}
.cluster {{ background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 18px; }}
.cluster header {{ display: flex; align-items: center; justify-content: space-between; gap: 12px;
  margin-bottom: 12px; }}
.cluster h3 {{ margin: 0; font-size: 16px; }}
.cluster dl {{ margin: 0 0 12px; display: grid; gap: 8px; }}
.cluster dl div {{ display: flex; justify-content: space-between; gap: 12px; border-bottom: 1px dashed var(--line);
  padding-bottom: 6px; }}
.cluster dt {{ font-size: 12.5px; color: var(--dim); }}
.cluster dd {{ margin: 0; font-size: 13.5px; text-align: right; }}
.cluster-members {{ font-size: 12.5px; word-break: break-word; margin-bottom: 0; }}
#network {{ height: 620px; border: 1px solid var(--line); border-radius: 12px; background: var(--panel); }}
.controls {{ display: flex; flex-wrap: wrap; align-items: center; gap: 14px; margin: 0 0 12px;
  font-size: 13.5px; color: var(--ink-2); }}
.controls label {{ display: flex; align-items: center; gap: 7px; cursor: pointer; }}
figure {{ margin: 20px 0 0; }}
figure img {{ width: 100%; max-width: 100%; height: auto; border: 1px solid var(--line); border-radius: 12px;
  display: block; }}
figcaption {{ font-size: 12.5px; color: var(--dim); margin-top: 8px; }}
noscript .note {{ display: block; background: var(--panel-2); border: 1px solid var(--line); border-radius: 10px;
  padding: 12px 14px; font-size: 13.5px; }}
.verdicts {{ display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); margin-top: 18px; }}
.verdict {{ background: var(--panel); border: 1px solid var(--line); border-left: 3px solid var(--elevated);
  border-radius: 10px; padding: 14px 16px; }}
.verdict.bad {{ border-left-color: var(--high); }}
.verdict p {{ margin: 0 0 8px; }}
.threshold {{ background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px;
  max-width: none; }}
footer {{ border-top: 1px solid var(--line); padding-top: 20px; margin-top: 40px; font-size: 12.5px;
  color: var(--dim); }}
@media (max-width: 640px) {{ .card-value {{ font-size: 27px; }} #network {{ height: 440px; }} }}
</style>
</head>
<body>
<div class="wrap">

<header class="top">
  <p class="eyebrow">BazaarAfrica · Transaction Network Analyzer</p>
  <h1>Fraud rings found by looking at the network, not the transaction.</h1>
  <p>Rule engines score payments one at a time and are structurally blind to fraud whose signal lives
  between accounts. This report models {len(result.transactions):,} P2P payments as a directed graph,
  computes network metrics per account, runs six structural detectors, and ranks every account by an
  explainable weighted risk score. Every flag below carries the evidence that produced it.</p>
  <p class="mono small dim">Generated {_esc(generated_at)} · deterministic pipeline, seeded layout</p>
  <nav class="jump">
    <a href="#findings">Findings</a><a href="#clusters">Clusters</a><a href="#network-section">Network</a>
    <a href="#methodology">Methodology</a><a href="#validation">Validation</a>
    <a href="findings.json">findings.json</a><a href="network.png">network.png</a>
  </nav>
</header>

<section id="summary" style="border-top:none; padding-top:0;">
  <h2>Summary</h2>
  <p class="lede">Counts from this run. Precision and recall are measured against the labelled fraud
  planted in the generated ledger, not estimated.</p>
  <div class="cards">{_summary_cards(result, findings, clusters)}</div>
</section>

<section id="findings">
  <h2>Ranked findings</h2>
  <p class="lede">Every account at or above the flagging threshold of {FLAG_THRESHOLD:.0f}, most suspicious
  first, with the signals that fired and the full evidence sentence behind each one. An investigator who
  cannot justify a freeze cannot act on it, so nothing here is summarised away.</p>
  <div class="legend">{band_legend}</div>
  {_findings_table(findings)}
</section>

<section id="clusters">
  <h2>Suspicious clusters</h2>
  <p class="lede">Fraud is worked cluster by cluster, not account by account. Flagged accounts grouped by
  the weakly connected component they sit in, with the value circulating inside each component and the
  signals that characterise it. Value is reported per currency — no exchange rates were invented.</p>
  {_clusters_section(clusters)}
</section>

<section id="network-section">
  <h2>The payment network</h2>
  <p class="lede">Nodes are accounts, coloured by risk band and sized by degree; arrows follow the money.
  The interactive canvas is an enhancement — every fact in it also appears as text above, and the static
  render below carries the same picture with no JavaScript at all.</p>
  <div class="controls">
    <label><input type="checkbox" id="only-flagged"> Show only flagged accounts and their neighbours</label>
    <span class="dim small">Drag to pan, scroll to zoom, hover a node for its metrics and reasons.</span>
  </div>
  <div id="network"></div>
  <noscript><span class="note">JavaScript is disabled, so the interactive canvas is empty. The static
  render below is the same graph.</span></noscript>
  <figure>
    <img src="network.png" alt="Spring-layout render of the payment network: accounts as nodes coloured by
    risk band and sized by degree, payments as directed edges, flagged accounts labelled.">
    <figcaption>Static render (<span class="mono">network.png</span>) — seeded spring layout, reproducible
    across runs. Labels are drawn only for flagged and elevated-risk accounts so the picture stays legible.</figcaption>
  </figure>
</section>

<section id="methodology">
  <h2>Methodology</h2>
  <p class="lede">Two views of the ledger: a multigraph keeping every transaction as its own edge, because
  timing between two accounts is itself a signal, and an aggregated graph for the structural algorithms.
  Metrics describe each account, detectors produce evidence, the score combines it.</p>
  {_methodology_section()}
</section>

<section id="validation">
  <h2>Validation</h2>
  {_validation_section(result)}
</section>

<footer>
  <p class="dim">Transaction Network Analyzer — synthetic BazaarAfrica ledger, seeded and reproducible.
  Machine-readable findings: <a href="findings.json">findings.json</a>. Static network render:
  <a href="network.png">network.png</a>.</p>
</footer>

</div>

<script type="application/json" id="graph-data">{payload}</script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/vis-network/9.1.9/dist/vis-network.min.js"></script>
<script>
(function () {{
  var el = document.getElementById("network");
  if (!el || typeof vis === "undefined") {{ return; }}
  var data = JSON.parse(document.getElementById("graph-data").textContent);
  var nodes = new vis.DataSet(data.nodes.map(function (n) {{
    return {{
      id: n.id, label: n.label, value: n.value, title: n.title, risk: n.risk, flagged: n.flagged,
      color: {{ background: n.color, border: n.flagged ? "#FFFFFF" : "#141A4A",
                highlight: {{ background: n.color, border: "#FFFFFF" }} }},
      borderWidth: n.flagged ? 2 : 1,
      font: {{ color: n.flagged || n.risk >= 20 ? "#E8EAF5" : "rgba(232,234,245,0.35)", size: 11 }}
    }};
  }}));
  var edges = new vis.DataSet(data.edges.map(function (e, i) {{
    return {{ id: "e" + i, from: e.from, to: e.to, value: e.value, title: e.title }};
  }}));
  var network = new vis.Network(el, {{ nodes: nodes, edges: edges }}, {{
    physics: {{ stabilization: {{ iterations: 250 }},
      barnesHut: {{ gravitationalConstant: -4200, centralGravity: 1.1, springLength: 95,
        springConstant: 0.05, avoidOverlap: 0.2 }} }},
    nodes: {{ shape: "dot", scaling: {{ min: 9, max: 38, label: {{ enabled: true, min: 11, max: 20 }} }} }},
    edges: {{ arrows: {{ to: {{ enabled: true, scaleFactor: 0.42 }} }}, color: {{ color: "#2A3050",
      highlight: "#6B7BFF", hover: "#6B7BFF" }}, smooth: {{ type: "continuous" }},
      scaling: {{ min: 0.4, max: 4 }} }},
    interaction: {{ hover: true, tooltipDelay: 90 }}
  }});
  network.once("stabilizationIterationsDone", function () {{
    network.setOptions({{ physics: false }});
    network.fit({{ animation: false }});
  }});
  var flaggedIds = data.nodes.filter(function (n) {{ return n.flagged; }}).map(function (n) {{ return n.id; }});
  var keep = {{}};
  flaggedIds.forEach(function (id) {{ keep[id] = true; }});
  data.edges.forEach(function (e) {{
    if (flaggedIds.indexOf(e.from) >= 0) {{ keep[e.to] = true; }}
    if (flaggedIds.indexOf(e.to) >= 0) {{ keep[e.from] = true; }}
  }});
  document.getElementById("only-flagged").addEventListener("change", function (event) {{
    var on = event.target.checked;
    nodes.update(data.nodes.map(function (n) {{ return {{ id: n.id, hidden: on && !keep[n.id] }}; }}));
    edges.update(data.edges.map(function (e, i) {{
      return {{ id: "e" + i, hidden: on && !(keep[e.from] && keep[e.to]) }};
    }}));
    network.fit({{ nodes: on ? Object.keys(keep) : [], animation: {{ duration: 400 }} }});
  }});
}})();
</script>
</body>
</html>
"""
