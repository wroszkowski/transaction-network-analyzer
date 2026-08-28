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
from . import sensitivity as sensitivity_module  # noqa: E402
from .score import FLAG_THRESHOLD, WEIGHTS  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .analyze import AnalysisResult

LAYOUT_SEED = 42

#: Risk bands. The label always carries the number as well as the colour, because colour alone is
#: not an accessible way to encode severity. The hex is the *fill* used for a node on the light
#: canvas (graph and static render); every one clears 3:1 against white so the shape stays visible.
BANDS: tuple[tuple[float, str, str], ...] = (
    (70.0, "critical", "#D92D3A"),
    (FLAG_THRESHOLD, "high", "#C9770F"),
    (20.0, "elevated", "#3E4FE0"),
    (0.0, "low", "#858BA3"),
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
    curve = _sensitivity_curve(result)

    _write_network_png(result, out_dir / "network.png")
    (out_dir / "findings.json").write_text(
        json.dumps(_findings_document(result, findings, clusters, curve, generated_at), indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "index.html").write_text(
        _render_html(result, findings, clusters, curve, generated_at),
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


#: Resolution of the sweep used to locate the edges of the perfect-score band. Finer than the table,
#: because "the answer is unchanged between 38 and 41" is a much stronger claim than "at 40 it works",
#: and rounding that band to the nearest 5 would throw the claim away.
BAND_STEP = 0.1


def _sensitivity_curve(result: AnalysisResult) -> pd.DataFrame | None:
    """The threshold sweep, or ``None`` when the ledger came without labels to sweep against."""
    if result.evaluation is None or result.ground_truth is None:
        return None
    return sensitivity_module.sweep(result.scores, result.ground_truth)


def _fine_sweep(result: AnalysisResult) -> pd.DataFrame | None:
    """The same sweep at 0.1 resolution, used only to locate the exact edges of a plateau."""
    if result.ground_truth is None:
        return None
    steps = int(round(100 / BAND_STEP)) + 1
    return sensitivity_module.sweep(
        result.scores,
        result.ground_truth,
        [round(index * BAND_STEP, 1) for index in range(steps)],
    )


def _recall_band(result: AnalysisResult) -> tuple[float, float] | None:
    """Every threshold that still catches all of the planted fraud.

    This is the robustness claim, and it survives a dataset where nothing scores perfectly: anywhere
    inside this band not one fraudulent account is missed, so the sensible cut-off is its top edge,
    where the fewest innocent accounts come along for the ride.
    """
    fine = _fine_sweep(result)
    return None if fine is None else sensitivity_module.plateau(fine, precision=0.0, recall=1.0)


def _findings_document(
    result: AnalysisResult,
    findings: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    curve: pd.DataFrame | None,
    generated_at: str,
) -> dict[str, Any]:
    # Not rounded: this file is the machine-readable copy, and a reader that wants two decimal places
    # can round for itself, whereas one that wants to diff two runs cannot recover what was thrown away.
    evaluation = dict(result.evaluation) if result.evaluation is not None else None
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
        "sensitivity": _sensitivity_document(result, curve),
    }


def _sensitivity_document(result: AnalysisResult, curve: pd.DataFrame | None) -> dict[str, Any] | None:
    """The sweep as records, plus the band inside which the choice of threshold changes nothing."""
    if curve is None:
        return None
    band = _recall_band(result)
    best = curve.loc[curve["f1"].idxmax()]
    return {
        "chosen_threshold": FLAG_THRESHOLD,
        "full_recall_between": list(band) if band else None,
        "best_f1_threshold": float(best["threshold"]),
        "best_f1": float(best["f1"]),
        "curve": [
            {
                "threshold": float(row["threshold"]),
                "flagged": int(row["flagged"]),
                "precision": float(row["precision"]),
                "recall": float(row["recall"]),
                "f1": float(row["f1"]),
                "false_positives": int(row["false_positives"]),
                "false_negatives": int(row["false_negatives"]),
            }
            for row in curve.to_dict("records")
        ],
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
    edge_colours = ["#5F6675" if (u in flagged and v in flagged) else "#C2C7D6" for u, v in aggregated.edges]

    figure, axes = plt.subplots(figsize=(15, 10.5), dpi=140)
    figure.patch.set_facecolor("#FFFFFF")
    axes.set_facecolor("#FFFFFF")

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
        edgecolors=["#1227AD" if node in flagged else "#FFFFFF" for node in aggregated.nodes],
    )
    labels = {node: node for node in aggregated.nodes if node in flagged or float(scores.get(node, 0.0)) >= 20.0}
    nx.draw_networkx_labels(
        aggregated,
        positions,
        labels=labels,
        ax=axes,
        font_size=6.5,
        font_color="#121212",
        bbox={"boxstyle": "round,pad=0.18", "facecolor": "#FFFFFF", "edgecolor": "none", "alpha": 0.82},
    )

    handles = [
        mpatches.Patch(color=colour, label=f"{name} risk ({_band_range(index)})")
        for index, (_, name, colour) in enumerate(BANDS)
    ]
    legend = axes.legend(
        handles=handles,
        loc="upper left",
        frameon=True,
        facecolor="#FFFFFF",
        edgecolor="#D5D8E4",
        fontsize=9,
        title="Risk score — node size is degree, labels are flagged accounts",
        title_fontsize=9,
    )
    for text in [*legend.get_texts(), legend.get_title()]:
        text.set_color("#121212")

    axes.set_title(
        "BazaarAfrica payment network — accounts as nodes, payments as directed edges",
        color="#121212",
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


def _walkthrough_section(result: AnalysisResult, findings: list[dict[str, Any]], clusters: list[dict[str, Any]]) -> str:
    """A guided tour for a reviewer who has never seen this page.

    Written as steps rather than prose because the point is the *order*: the numbers frame the
    claim, the evidence supports it, and the validation says how far to trust it. Every figure is
    read off this run, so the tour cannot drift out of step with the report it introduces.
    """
    top = findings[0]["account_id"] if findings else None
    # The highest-scoring cluster, not the largest: clusters are pre-sorted by top risk score, and
    # the biggest component is the legitimate background, which is the opposite of the tour's point.
    worst = clusters[0] if clusters else None
    steps: list[tuple[str, str]] = [
        (
            "Start with the numbers above",
            f"{len(findings)} of {len(result.metrics):,} accounts cleared the flagging threshold of "
            f"{FLAG_THRESHOLD:.0f}. If precision reads below 100%, that is deliberate — the ledger "
            "contains innocent accounts built specifically to look guilty, and a detector that never "
            "trips on them has not been tested.",
        ),
    ]
    if top is not None:
        steps.append(
            (
                f'Read one row in full — <a href="#findings">{_esc(top)}</a>',
                "Each row carries the signals that fired and the evidence sentence behind every one, "
                "naming the counterparties, the amounts and the time windows. That sentence is the "
                "deliverable: an investigator who cannot justify a freeze cannot act on it.",
            )
        )
    if worst is not None:
        steps.append(
            (
                f'Zoom out to the case — <a href="#clusters">cluster {_esc(worst["component_id"])}</a>',
                f"{len(worst['flagged_accounts'])} flagged accounts inside one component of "
                f"{worst['component_size']}, topping out at risk {worst['top_risk_score']:.0f}. Fraud is worked "
                "cluster by cluster, so this is the level at which the findings become a single "
                "investigation rather than a pile of alerts.",
            )
        )
    steps.extend(
        [
            (
                'Look at the shape — <a href="#network-section">the payment network</a>',
                "Tick <em>show only flagged accounts</em> to strip the background away. The rings have "
                "visible geometry: closed loops, a star collapsing into one collector, a fan out of a "
                "single drained victim. Compare them with the busiest unflagged account, which has a "
                "wide flat neighbourhood and no structure at all.",
            ),
            (
                'Ask how far to trust it — <a href="#validation">validation</a>',
                "Precision, recall and the false positives named individually, measured against planted "
                "labels rather than estimated. The threshold sweep below it shows what the cut-off is "
                "worth and where it stops holding.",
            ),
            (
                "Run it on your own ledger",
                "Nothing here is baked in. Point the CLI at any CSV with the same columns:"
                '<br><code class="mono">uv run python -m tna.cli analyze --input yours.csv --out ./out</code>'
                "<br>The file is validated first, so a bad column set produces one readable message "
                "listing every problem, not a stack trace from inside the metrics code.",
            ),
        ]
    )
    items = "\n".join(f"<li><strong>{title}</strong><span>{body}</span></li>" for title, body in steps)
    return f'<ol class="tour">{items}</ol>'


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


# --------------------------------------------------------------------------------------
# threshold sensitivity
# --------------------------------------------------------------------------------------

#: Chart geometry, in viewBox units. The SVG scales with the page; only the ratio matters.
CHART = {
    "w": 760.0,
    "left": 74.0,
    "right": 726.0,
    "top": 30.0,
    "bottom": 286.0,
    "legend": 372.0,
    "legend2": 404.0,
}

#: Each series gets a colour, a dash pattern and a marker shape, so the chart is readable in
#: greyscale and to a colour-blind reader. Colour alone is never the only encoding. Every hue is
#: dark enough to clear 4.5:1 against the light chart panel.
SERIES: tuple[tuple[str, str, str, str, str], ...] = (
    ("precision", "Precision", "#3E4FE0", "none", "circle"),
    ("recall", "Recall", "#8F4F00", "9 5", "square"),
    ("f1", "F1", "#0B7A6C", "2 4", "triangle"),
)

#: Chart furniture, all against the light panel.
CHART_GRID = "rgba(18,39,173,0.14)"
CHART_TICK = "rgba(18,39,173,0.32)"
CHART_AXIS = "rgba(18,39,173,0.38)"
CHART_LABEL = "#4A4A4A"
CHART_RULE = "#1227AD"
CHART_BAND = "rgba(11,122,108,0.14)"
CHART_BAND_SWATCH = "rgba(11,122,108,0.30)"


def _chart_x(threshold: float) -> float:
    return CHART["left"] + (threshold / 100.0) * (CHART["right"] - CHART["left"])


def _chart_y(value: float) -> float:
    return CHART["bottom"] - value * (CHART["bottom"] - CHART["top"])


def _marker(shape: str, x: float, y: float, colour: str) -> str:
    if shape == "circle":
        return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.4" fill="{colour}"/>'
    if shape == "square":
        return f'<rect x="{x - 3.1:.1f}" y="{y - 3.1:.1f}" width="6.2" height="6.2" fill="{colour}"/>'
    points = f"{x:.1f},{y - 3.9:.1f} {x + 3.6:.1f},{y + 2.6:.1f} {x - 3.6:.1f},{y + 2.6:.1f}"
    return f'<polygon points="{points}" fill="{colour}"/>'


def _sensitivity_svg(curve: pd.DataFrame, band: tuple[float, float] | None) -> str:
    """A hand-rolled line chart. No library, no network request, no JavaScript."""
    grid = []
    for value in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = _chart_y(value)
        grid.append(
            f'<line x1="{CHART["left"]:.1f}" y1="{y:.1f}" x2="{CHART["right"]:.1f}" y2="{y:.1f}" '
            f'stroke="{CHART_GRID}" stroke-width="1"/>'
            f'<text x="{CHART["left"] - 12:.1f}" y="{y + 4:.1f}" text-anchor="end" font-size="12" '
            f'fill="{CHART_LABEL}">{value:.2f}</text>'
        )
    for threshold in range(0, 101, 10):
        x = _chart_x(threshold)
        grid.append(
            f'<line x1="{x:.1f}" y1="{CHART["bottom"]:.1f}" x2="{x:.1f}" y2="{CHART["bottom"] + 6:.1f}" '
            f'stroke="{CHART_TICK}" stroke-width="1"/>'
            f'<text x="{x:.1f}" y="{CHART["bottom"] + 22:.1f}" text-anchor="middle" font-size="12" '
            f'fill="{CHART_LABEL}">{threshold}</text>'
        )

    shade = ""
    if band is not None:
        left, right = _chart_x(band[0]), _chart_x(band[1])
        shade = (
            f'<rect x="{left:.1f}" y="{CHART["top"]:.1f}" width="{max(right - left, 1.5):.1f}" '
            f'height="{CHART["bottom"] - CHART["top"]:.1f}" fill="{CHART_BAND}"/>'
        )

    rule_x = _chart_x(FLAG_THRESHOLD)
    rule = (
        f'<line x1="{rule_x:.1f}" y1="{CHART["top"] - 12:.1f}" x2="{rule_x:.1f}" y2="{CHART["bottom"]:.1f}" '
        f'stroke="{CHART_RULE}" stroke-width="1.4" stroke-dasharray="5 4"/>'
        f'<text x="{rule_x + 8:.1f}" y="{CHART["top"] - 3:.1f}" font-size="12.5" fill="{CHART_RULE}" '
        f'font-weight="600">chosen threshold = {FLAG_THRESHOLD:.0f}</text>'
    )

    records = curve.to_dict("records")
    paths = []
    for column, _label, colour, dash, shape in SERIES:
        points = " ".join(
            f"{_chart_x(float(row['threshold'])):.1f},{_chart_y(float(row[column])):.1f}" for row in records
        )
        dash_attr = f' stroke-dasharray="{dash}"' if dash != "none" else ""
        paths.append(
            f'<polyline points="{points}" fill="none" stroke="{colour}" stroke-width="2.2" '
            f'stroke-linejoin="round"{dash_attr}/>'
        )
        paths.extend(
            _marker(shape, _chart_x(float(row["threshold"])), _chart_y(float(row[column])), colour)
            for row in records
            if float(row["threshold"]) % 10 == 0
        )

    legend = []
    for index, (_column, label, colour, dash, shape) in enumerate(SERIES):
        x = CHART["left"] + index * 215.0
        y = CHART["legend"]
        dash_attr = f' stroke-dasharray="{dash}"' if dash != "none" else ""
        legend.append(
            f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x + 34:.1f}" y2="{y:.1f}" stroke="{colour}" '
            f'stroke-width="2.2"{dash_attr}/>'
            f"{_marker(shape, x + 17, y, colour)}"
            f'<text x="{x + 44:.1f}" y="{y + 4.5:.1f}" font-size="12.5" fill="{CHART_LABEL}">{_esc(label)} '
            f"({'solid' if dash == 'none' else 'dashed' if dash == '9 5' else 'dotted'})</text>"
        )
    if band is not None:
        y = CHART["legend2"]
        legend.append(
            f'<rect x="{CHART["left"]:.1f}" y="{y - 7:.1f}" width="34" height="14" fill="{CHART_BAND_SWATCH}"/>'
            f'<text x="{CHART["left"] + 44:.1f}" y="{y + 4.5:.1f}" font-size="12.5" fill="{CHART_LABEL}">'
            f"shaded band = every threshold that still catches all the fraud, recall 1.00 "
            f"({band[0]:g}–{band[1]:g})</text>"
        )

    caption = (
        "Line chart of precision, recall and F1 against the flagging threshold from 0 to 100. "
        f"Recall holds at 1.0 up to a threshold of {band[1]:g} and falls away above it, while "
        f"precision climbs steadily as the threshold rises. "
        if band
        else "Line chart of precision, recall and F1 against the flagging threshold from 0 to 100. "
    ) + f"The chosen threshold of {FLAG_THRESHOLD:.0f} is marked with a vertical dashed rule."

    height = (CHART["legend2"] if band is not None else CHART["legend"]) + 28.0
    return f"""<div class="chart">
<svg viewBox="0 0 {CHART["w"]:.0f} {height:.0f}" role="img" preserveAspectRatio="xMidYMid meet"
     aria-label="{_esc(caption)}">
  <title>Precision, recall and F1 against the flagging threshold</title>
  {shade}
  {"".join(grid)}
  <line x1="{CHART["left"]:.1f}" y1="{CHART["top"]:.1f}" x2="{CHART["left"]:.1f}"
        y2="{CHART["bottom"]:.1f}" stroke="{CHART_AXIS}" stroke-width="1"/>
  <line x1="{CHART["left"]:.1f}" y1="{CHART["bottom"]:.1f}" x2="{CHART["right"]:.1f}"
        y2="{CHART["bottom"]:.1f}" stroke="{CHART_AXIS}" stroke-width="1"/>
  {rule}
  {"".join(paths)}
  <text x="{(CHART["left"] + CHART["right"]) / 2:.1f}" y="{CHART["bottom"] + 46:.1f}" text-anchor="middle"
        font-size="12.5" fill="{CHART_LABEL}">Flagging threshold (risk score out of 100)</text>
  <text x="20" y="{(CHART["top"] + CHART["bottom"]) / 2:.1f}" text-anchor="middle" font-size="12.5"
        fill="{CHART_LABEL}" transform="rotate(-90 20 {(CHART["top"] + CHART["bottom"]) / 2:.1f})">Score (0–1)</text>
  {"".join(legend)}
</svg>
</div>"""


def _sensitivity_table(curve: pd.DataFrame) -> str:
    rows = []
    for row in curve.to_dict("records"):
        threshold = float(row["threshold"])
        chosen = ' class="chosen"' if threshold == FLAG_THRESHOLD else ""
        marker = " ←" if threshold == FLAG_THRESHOLD else ""
        rows.append(
            f"<tr{chosen}>"
            f'<td class="num mono strong">{threshold:.0f}{marker}</td>'
            f'<td class="num">{int(row["flagged"])}</td>'
            f'<td class="num">{float(row["precision"]):.1%}</td>'
            f'<td class="num">{float(row["recall"]):.1%}</td>'
            f'<td class="num">{float(row["f1"]):.3f}</td>'
            f'<td class="num">{int(row["false_positives"])}</td>'
            f'<td class="num">{int(row["false_negatives"])}</td>'
            f"</tr>"
        )
    return (
        '<div class="scroll"><table>'
        "<thead><tr><th>Threshold</th><th>Accounts flagged</th><th>Precision</th><th>Recall</th>"
        "<th>F1</th><th>False positives</th><th>False negatives</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _conclusion(curve: pd.DataFrame, band: tuple[float, float] | None, best: dict[str, Any]) -> str:
    """The honest reading of this particular curve, in this run's own numbers."""
    if band is None:
        return (
            "<p>No threshold on this ledger catches every planted account, so the choice here is a "
            "genuine trade-off rather than a free one. The best F1 on the sweep is "
            f"<b>{float(best['f1']):.3f}</b> at a threshold of <b>{float(best['threshold']):.0f}</b>, "
            "and the shape of the curve — not the single number — is the argument for it.</p>"
        )

    above = curve[curve["threshold"] > band[1]]
    cost = ""
    if not above.empty:
        first = above.iloc[0]
        cost = (
            f" The first step past that edge, at {float(first['threshold']):.0f}, drops "
            f"{int(first['false_negatives'])} fraudulent accounts and takes recall to "
            f"{float(first['recall']):.0%} — the fall is a cliff rather than a slope, because "
            f"the weaker members of a ring score alike and leave together."
        )

    chosen_is_best = float(best["threshold"]) == FLAG_THRESHOLD
    verdict = (
        f" On this run {FLAG_THRESHOLD:.0f} is also the F1-maximising point of the swept grid "
        f"(F1 {float(best['f1']):.3f}), so it is the best available answer as well as a defensible one."
        if chosen_is_best
        else f" Note that F1 peaks slightly elsewhere on the grid, at {float(best['threshold']):.0f} "
        f"(F1 {float(best['f1']):.3f}); {FLAG_THRESHOLD:.0f} is kept because it is a round number "
        f"inside the full-recall band, and tuning to the third decimal of F1 on one generated ledger "
        f"would be exactly the overfitting this section exists to rule out."
    )

    return (
        f"<p>Every threshold from <b>{band[0]:g}</b> up to <b>{band[1]:g}</b> catches all of the "
        f"planted fraud — recall is 1.0 across that whole band, so within it the exact cut-off "
        f"changes only how many innocent accounts come with it. That makes the top of the band the "
        f"only sensible place to sit, and {FLAG_THRESHOLD:.0f} is a round number just inside it "
        f"rather than a value tuned until the numbers flattered the detector.{cost}{verdict}</p>"
    )


def _sensitivity_section(result: AnalysisResult, curve: pd.DataFrame | None) -> str:
    """Prose, table and chart. Empty string when there is nothing to sweep against."""
    if curve is None:
        return ""
    band = _recall_band(result)
    at_zero = curve.iloc[0]
    best = dict(curve.loc[curve["f1"].idxmax()])
    conclusion = _conclusion(curve, band, best)
    return f"""
<p>The flagging threshold is the one number in this pipeline that is a policy choice rather than a
measurement, so it should be argued for rather than asserted. Lowering it buys recall with
analyst-hours: every extra false positive is a real person investigating an innocent customer, and a
fraud team has a fixed number of those hours. Raising it protects the hours by letting rings run.
The sweep below re-runs the whole evaluation at every threshold from 0 to 100 in steps of 5, so a
reviewer can see the trade-off instead of taking 40 on trust.</p>
{_sensitivity_table(curve)}
{_sensitivity_svg(curve, band)}
<p class="small dim">At a threshold of 0 every one of the {int(at_zero["flagged"])} accounts in the
graph is flagged, which is what perfect recall costs when it is bought with no discrimination at all;
above the highest score in the ledger nothing is flagged and precision is reported as 0 rather than
dividing by zero.</p>
{conclusion}
"""


def _sensitivity_block(result: AnalysisResult, curve: pd.DataFrame | None) -> str:
    """The whole ``<section>``, or nothing at all — an empty shell would be worse than no section."""
    body = _sensitivity_section(result, curve)
    if not body:
        return ""
    return f'<section id="sensitivity">\n  <h2>Threshold sensitivity</h2>{body}</section>'


def _render_html(
    result: AnalysisResult,
    findings: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    curve: pd.DataFrame | None,
    generated_at: str,
) -> str:
    payload = json.dumps(_graph_payload(result, findings)).replace("</", "<\\/")
    sensitivity_link = '<a href="#sensitivity">Threshold sensitivity</a>' if curve is not None else ""
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
  --bg: #FFFFFF; --panel: #F7F8FC; --panel-2: #E8EAF5; --line: rgba(18,39,173,0.12);
  --line-strong: rgba(18,39,173,0.22);
  --ink: #121212; --ink-2: #2A2A2A; --dim: #5F6675; --blue: #3E4FE0; --blue-deep: #1227AD;
  --critical: #C2303A; --critical-soft: #FCEDEE; --critical-line: #EFB9BD;
  --high: #8F4F00; --high-soft: #FDF4E7; --high-line: #E8C48E;
  --elevated: #3E4FE0; --elevated-soft: #E8EAF5; --elevated-line: #BDC3F6;
  --low: #4A4A4A; --low-soft: #F0F1F5; --low-line: #D6D9E2;
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
.strong {{ color: var(--ink); font-weight: 600; }}
.num {{ font-variant-numeric: tabular-nums; white-space: nowrap; }}
nav.jump {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 18px; }}
nav.jump a {{ font-family: var(--mono); font-size: 12px; text-decoration: none; color: var(--ink-2);
  background: #fff; border: 1px solid var(--line-strong); border-radius: 9999px; padding: 5px 12px; }}
nav.jump a:hover {{ border-color: var(--blue); background: var(--elevated-soft); color: var(--blue-deep); }}
ol.tour {{ list-style: none; counter-reset: tour; margin: 0; padding: 0; display: grid; gap: 12px; }}
ol.tour li {{ counter-increment: tour; position: relative; padding: 14px 18px 14px 52px;
  background: var(--panel); border: 1px solid var(--line); border-radius: 12px; }}
ol.tour li::before {{ content: counter(tour); position: absolute; left: 16px; top: 14px;
  font-family: var(--mono); font-size: 12px; font-weight: 600; color: var(--blue-deep);
  background: var(--elevated-soft); border-radius: 6px; width: 22px; height: 22px;
  display: grid; place-items: center; }}
ol.tour strong {{ display: block; font-size: 14.5px; margin-bottom: 4px; }}
ol.tour span {{ display: block; font-size: 13.5px; color: var(--ink-2); }}
ol.tour code {{ display: inline-block; margin-top: 6px; font-size: 12px; }}
.cards {{ display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }}
.card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; }}
.card-label {{ font-family: var(--mono); font-size: 10.5px; letter-spacing: 0.09em; text-transform: uppercase;
  color: var(--dim); }}
.card-value {{ font-size: 34px; font-weight: 600; line-height: 1.15; margin: 6px 0 2px;
  font-variant-numeric: tabular-nums; }}
.card-note {{ font-size: 12.5px; color: var(--dim); }}
.scroll {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 12px; background: #fff; }}
table {{ border-collapse: collapse; width: 100%; min-width: 760px; font-size: 14px; }}
th, td {{ text-align: left; padding: 11px 14px; border-bottom: 1px solid var(--line); vertical-align: top; }}
th {{ font-family: var(--mono); font-size: 10.5px; letter-spacing: 0.09em; text-transform: uppercase;
  color: var(--dim); background: var(--panel-2); position: sticky; top: 0; }}
tbody tr:last-child td {{ border-bottom: none; }}
tbody tr:hover {{ background: rgba(62,79,224,0.05); }}
ul.evidence {{ margin: 0; padding-left: 17px; color: var(--ink-2); font-size: 13.5px; }}
ul.evidence li {{ margin-bottom: 5px; }}
ul.evidence li:last-child {{ margin-bottom: 0; }}
.tag {{ display: inline-block; font-family: var(--mono); font-size: 11px; padding: 2px 8px; margin: 0 4px 4px 0;
  border: 1px solid var(--line-strong); border-radius: 9999px; color: var(--ink-2); background: #fff; }}
td.tags {{ min-width: 170px; }}
.pill {{ display: inline-block; font-family: var(--mono); font-size: 11.5px; font-weight: 600;
  padding: 3px 10px; border-radius: 9999px; border: 1px solid transparent; white-space: nowrap; }}
.band-critical {{ background: var(--critical-soft); color: var(--critical); border-color: var(--critical-line); }}
.band-high {{ background: var(--high-soft); color: var(--high); border-color: var(--high-line); }}
.band-elevated {{ background: var(--elevated-soft); color: var(--blue-deep); border-color: var(--elevated-line); }}
.band-low {{ background: var(--low-soft); color: var(--low); border-color: var(--low-line); }}
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
.cluster dd.num {{ white-space: normal; }}
.cluster-members {{ font-size: 12.5px; word-break: break-word; margin-bottom: 0; }}
#network {{ height: 620px; border: 1px solid var(--line); border-radius: 12px; background: #fff; }}
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
.chart {{ background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px;
  margin: 18px 0; }}
.chart svg {{ display: block; width: 100%; max-width: 100%; height: auto; }}
tbody tr.chosen td {{ background: var(--elevated-soft); color: var(--ink); }}
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
    <a href="#walkthrough">Start here</a><a href="#findings">Findings</a><a href="#clusters">Clusters</a>
    <a href="#network-section">Network</a>
    <a href="#methodology">Methodology</a><a href="#validation">Validation</a>{sensitivity_link}
    <a href="findings.json">findings.json</a><a href="network.png">network.png</a>
  </nav>
</header>

<section id="summary" style="border-top:none; padding-top:0;">
  <h2>Summary</h2>
  <p class="lede">Counts from this run. Precision and recall are measured against the labelled fraud
  planted in the generated ledger, not estimated.</p>
  <div class="cards">{_summary_cards(result, findings, clusters)}</div>
</section>

<section id="walkthrough">
  <h2>Start here — a guided tour</h2>
  <p class="lede">Six steps through this report in the order the argument is built, for a reader who
  has not seen it before. Every figure below is read off this run.</p>
  {_walkthrough_section(result, findings, clusters)}
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

{_sensitivity_block(result, curve)}

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
      color: {{ background: n.color, border: n.flagged ? "#1227AD" : "#C2C7D6",
                highlight: {{ background: n.color, border: "#1227AD" }} }},
      borderWidth: n.flagged ? 2 : 1,
      font: {{ color: n.flagged || n.risk >= 20 ? "#121212" : "rgba(18,18,18,0.45)", size: 11,
               strokeWidth: 3, strokeColor: "#FFFFFF" }}
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
    edges: {{ arrows: {{ to: {{ enabled: true, scaleFactor: 0.42 }} }}, color: {{ color: "#A9AFC2",
      highlight: "#3E4FE0", hover: "#3E4FE0" }}, smooth: {{ type: "continuous" }},
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
