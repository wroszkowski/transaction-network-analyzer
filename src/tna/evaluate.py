"""Score the detector against the fraud that was actually planted.

Because the demo data is generated, ground truth is known, so the detector's quality is a
measurement rather than a claim. Recall says how much of the ring we would have caught; precision
says how much of an investigator's time we would have wasted.
"""

from collections.abc import Iterable, Mapping
from typing import TypedDict


class Evaluation(TypedDict):
    """The detector's report card. Typed so callers can read the numbers without casting."""

    precision: float
    recall: float
    f1: float
    true_positives: list[str]
    false_positives: list[str]
    false_negatives: list[str]


def evaluate(flagged: Iterable[str], truth: Mapping[str, str]) -> Evaluation:
    """Compare flagged accounts against labelled fraudulent accounts."""
    flagged_set = set(flagged)
    fraudulent = set(truth)

    true_positives = sorted(flagged_set & fraudulent)
    false_positives = sorted(flagged_set - fraudulent)
    false_negatives = sorted(fraudulent - flagged_set)

    precision = len(true_positives) / len(flagged_set) if flagged_set else 0.0
    recall = len(true_positives) / len(fraudulent) if fraudulent else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
    }
