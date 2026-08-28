"""Sweep the flagging threshold so the chosen cut-off is evidence rather than an assertion.

A single operating point is a claim: "we flag at 40, and here is the precision we got". A curve is
evidence: it shows what would have happened at every other cut-off, and therefore whether 40 was
chosen because the data supports it or because it happened to produce a flattering number.

The choice is not really a modelling decision, it is a resourcing one. Lowering the threshold buys
recall with analyst-hours — every extra false positive is a person investigating an innocent
customer, and a fraud team has a fixed number of those hours in a week. Raising it protects those
hours by letting rings through, and a missed ring keeps draining money for as long as it runs. Only
the team that carries both costs can say where on this curve they want to sit, so the honest thing
for a detector to publish is the whole curve and let them pick.

The other thing a sweep reveals is robustness. If precision and recall are unchanged across a wide
band of thresholds, the exact number is not load-bearing and the result is not tuned to fit. If the
metrics move sharply on either side of the chosen value, that is worth knowing too — and worth
saying out loud.

Deliberately pure: it takes scores and labels, returns a frame. No plotting, no file I/O, no
knowledge of how the report will draw it.
"""

from collections.abc import Iterable, Mapping

import pandas as pd

from .evaluate import evaluate
from .score import flagged_accounts

#: Coarse enough to read at a glance, fine enough to locate the edges of a plateau.
DEFAULT_THRESHOLDS: tuple[float, ...] = tuple(float(step * 5) for step in range(21))


def sweep(
    scores: pd.DataFrame,
    truth: Mapping[str, str],
    thresholds: Iterable[float] = DEFAULT_THRESHOLDS,
) -> pd.DataFrame:
    """Evaluate the detector at every threshold, one tidy row each.

    Columns: ``threshold``, ``flagged``, ``precision``, ``recall``, ``f1``, ``false_positives``,
    ``false_negatives`` — the last two as counts, because the curve is read for its shape and the
    account names are already listed in the headline evaluation.
    """
    rows = []
    for threshold in thresholds:
        flagged = flagged_accounts(scores, float(threshold))
        result = evaluate(flagged, truth)
        rows.append(
            {
                "threshold": float(threshold),
                "flagged": len(flagged),
                "precision": float(result["precision"]),
                "recall": float(result["recall"]),
                "f1": float(result["f1"]),
                "false_positives": len(result["false_positives"]),
                "false_negatives": len(result["false_negatives"]),
            }
        )
    return pd.DataFrame(
        rows,
        columns=["threshold", "flagged", "precision", "recall", "f1", "false_positives", "false_negatives"],
    )


def plateau(curve: pd.DataFrame, precision: float = 1.0, recall: float = 1.0) -> tuple[float, float] | None:
    """The widest run of thresholds over which precision and recall both hold at the given values.

    This is the robustness claim: within this band the exact cut-off does not change a single
    decision, so the choice of threshold is not what produced the result. ``None`` when no threshold
    reaches both targets.
    """
    best: tuple[float, float] | None = None
    start: float | None = None
    previous: float | None = None
    for row in curve.to_dict("records"):
        threshold = float(row["threshold"])
        if float(row["precision"]) >= precision and float(row["recall"]) >= recall:
            start = threshold if start is None else start
            previous = threshold
        elif start is not None and previous is not None:
            best = _wider(best, (start, previous))
            start = previous = None
    if start is not None and previous is not None:
        best = _wider(best, (start, previous))
    return best


def _wider(current: tuple[float, float] | None, candidate: tuple[float, float]) -> tuple[float, float]:
    if current is None or (candidate[1] - candidate[0]) > (current[1] - current[0]):
        return candidate
    return current
