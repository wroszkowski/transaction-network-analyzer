"""The single entry point that composes the pipeline.

Every other module does one job and knows nothing about the others: `graph` builds the two views of
the ledger, `metrics` describes each account, `detectors` produce evidence, `score` combines it, and
`evaluate` measures the result. This module is the only place that knows the order they run in, so
the CLI, the report and the tests all share one definition of "analysing a ledger" rather than
re-wiring the steps three times and drifting apart.

It stays deliberately thin: no analysis logic lives here, only the wiring and the bundled result.

The ledger is validated on the way in, by `tna.schema`, so every path into the pipeline — CLI,
report, tests — passes through the same trust boundary and every stage downstream can assume a
well-formed frame. Progress is reported through `logging` rather than printed: stdout belongs to the
program's results, diagnostics belong to a logger the operator can turn up with `-v`.
"""

import logging
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

import networkx as nx
import pandas as pd

from . import evaluate as evaluate_module
from . import graph as graph_module
from . import metrics as metrics_module
from .detectors import MAX_CYCLE_LENGTH, SIGNAL_NAMES, Signal, run_all
from .schema import validate_ledger
from .score import flagged_accounts, score_accounts

logger = logging.getLogger(__name__)


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


def _log_cycle_rejections(aggregated: nx.DiGraph, signals: list[Signal]) -> None:
    """Report how much of the cycle search survived the span and coherence constraints.

    This is the interesting rejection in the whole pipeline: most candidate loops in a connected
    marketplace are coincidence, and the ratio between what was examined and what survived is the
    number that tells an operator whether the constraints are doing their job. The re-enumeration
    costs the same walk the detector already did, so it runs only when someone asked for DEBUG.
    """
    if not logger.isEnabledFor(logging.DEBUG):
        return
    candidates = sum(1 for cycle in nx.simple_cycles(aggregated, length_bound=MAX_CYCLE_LENGTH) if len(cycle) >= 2)
    survived = len({signal.evidence for signal in signals if signal.signal == "circular_flow"})
    logger.debug(
        "cycle search: %d candidate loop(s) examined, %d survived the span and coherence constraints",
        candidates,
        survived,
    )


def analyze(
    transactions: pd.DataFrame,
    accounts: pd.DataFrame,
    ground_truth: Mapping[str, str] | None = None,
) -> AnalysisResult:
    """Run the whole pipeline over one ledger.

    ``ground_truth`` is optional because real ledgers do not come with labels; when it is supplied
    the run is also scored, which is how the detector's quality is measured rather than asserted.

    Raises:
        LedgerValidationError: if the ledger is missing columns, types or keys the pipeline needs.
    """
    transactions = validate_ledger(transactions)

    multi = graph_module.build_graph(transactions)
    aggregated = graph_module.aggregate(multi)
    logger.info(
        "graph built: %d accounts, %d transactions, %d aggregated relationships",
        multi.number_of_nodes(),
        multi.number_of_edges(),
        aggregated.number_of_edges(),
    )

    metrics = metrics_module.compute_metrics(transactions, multi)
    logger.info("metrics computed for %d accounts", len(metrics))

    signals = run_all(transactions, accounts, aggregated, metrics)
    counts = Counter(signal.signal for signal in signals)
    for name in SIGNAL_NAMES:
        # Every detector is reported, silent ones included: a detector that fired nothing is as
        # much a diagnostic as one that fired a hundred times.
        logger.info("detector %s produced %d signal(s)", name, counts[name])
    _log_cycle_rejections(aggregated, signals)

    scores = score_accounts(signals, list(metrics.index))
    flagged = flagged_accounts(scores)
    logger.info("scoring complete: %d of %d accounts flagged", len(flagged), len(scores))

    evaluation = evaluate_module.evaluate(flagged, ground_truth) if ground_truth is not None else None
    if evaluation is not None:
        logger.info(
            "evaluated against ground truth: precision %.3f, recall %.3f, F1 %.3f",
            evaluation["precision"],
            evaluation["recall"],
            evaluation["f1"],
        )
    else:
        logger.info("no ground truth supplied; the run is not scored")

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
