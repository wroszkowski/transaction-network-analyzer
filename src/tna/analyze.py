"""The single entry point that composes the pipeline.

Every other module does one job and knows nothing about the others: `graph` builds the two views of
the ledger, `metrics` describes each account, `detectors` produce evidence, `score` combines it, and
`evaluate` measures the result. This module is the only place that knows the order they run in, so
the CLI, the report and the tests all share one definition of "analysing a ledger" rather than
re-wiring the steps three times and drifting apart.

It stays deliberately thin: no analysis logic lives here, only the wiring and the bundled result.
"""

from collections.abc import Mapping
from dataclasses import dataclass

import networkx as nx
import pandas as pd

from . import evaluate as evaluate_module
from . import graph as graph_module
from . import metrics as metrics_module
from .detectors import Signal, run_all
from .score import flagged_accounts, score_accounts


@dataclass
class AnalysisResult:
    """Everything one pass of the pipeline produced, kept together so it can be inspected."""

    transactions: pd.DataFrame
    accounts: pd.DataFrame
    graph: nx.MultiDiGraph
    aggregated: nx.DiGraph
    metrics: pd.DataFrame
    signals: list[Signal]
    scores: pd.DataFrame
    flagged: list[str]
    evaluation: evaluate_module.Evaluation | None
    #: The labels the run was scored against, kept so the report can re-evaluate at other
    #: thresholds. ``None`` for a real ledger, which is what makes the sensitivity sweep optional.
    ground_truth: Mapping[str, str] | None = None


def analyze(
    transactions: pd.DataFrame,
    accounts: pd.DataFrame,
    ground_truth: Mapping[str, str] | None = None,
) -> AnalysisResult:
    """Run the whole pipeline over one ledger.

    ``ground_truth`` is optional because real ledgers do not come with labels; when it is supplied
    the run is also scored, which is how the detector's quality is measured rather than asserted.
    """
    multi = graph_module.build_graph(transactions)
    aggregated = graph_module.aggregate(multi)
    metrics = metrics_module.compute_metrics(transactions, multi)
    signals = run_all(transactions, accounts, aggregated, metrics)
    scores = score_accounts(signals, list(metrics.index))
    flagged = flagged_accounts(scores)
    evaluation = evaluate_module.evaluate(flagged, ground_truth) if ground_truth is not None else None
    return AnalysisResult(
        transactions=transactions,
        accounts=accounts,
        graph=multi,
        aggregated=aggregated,
        metrics=metrics,
        signals=signals,
        scores=scores,
        flagged=flagged,
        evaluation=evaluation,
        ground_truth=ground_truth,
    )
