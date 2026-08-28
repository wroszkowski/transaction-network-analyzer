"""Combine detector evidence into one ranked, explainable risk score.

Deliberately a weighted sum rather than a learned model. An investigator has to justify freezing an
account, so the contribution of every signal stays visible and the weights stay arguable. The
weights below encode a view about how strongly each structure implies coordination — they are a
starting point for a fraud team to tune, not a truth.
"""

from collections.abc import Iterable, Sequence

import pandas as pd

from .detectors import Signal

#: Points contributed by a signal at full strength. Structural evidence outranks circumstantial:
#: a closed money loop is hard to explain innocently, while a shared IP could be a family or a cafe.
WEIGHTS: dict[str, float] = {
    "circular_flow": 35.0,
    "tight_component": 25.0,
    "fan_in_fan_out": 25.0,
    "velocity_burst": 20.0,
    "synchronised_onboarding": 15.0,
    "shared_identifiers": 15.0,
}

#: Accounts at or above this score are surfaced for investigation.
FLAG_THRESHOLD = 40.0


def score_accounts(signals: Iterable[Signal], universe: Sequence[str]) -> pd.DataFrame:
    """Score every account in ``universe``, ranked most suspicious first.

    A signal firing several times for one account (three separate loops, say) counts once, at its
    strongest, so that a single structure cannot inflate a score by being detected repeatedly.
    """
    strongest: dict[str, dict[str, Signal]] = {account: {} for account in universe}
    for signal in signals:
        if signal.signal not in WEIGHTS:
            raise KeyError(f"no weight defined for signal {signal.signal!r} — add one to score.WEIGHTS")
        best = strongest.setdefault(signal.account, {}).get(signal.signal)
        if best is None or signal.strength > best.strength:
            strongest[signal.account][signal.signal] = signal

    rows = []
    for account in universe:
        hits = strongest.get(account, {})
        score = sum(WEIGHTS[name] * hit.strength for name, hit in hits.items())
        rows.append(
            {
                "account_id": account,
                "risk_score": round(min(100.0, score), 1),
                "signals": sorted(hits),
                "reasons": [f"{hit.evidence}" for _, hit in sorted(hits.items())],
            }
        )

    frame = pd.DataFrame(rows).set_index("account_id")
    return frame.sort_values("risk_score", ascending=False, kind="stable")


def flagged_accounts(scores: pd.DataFrame, threshold: float = FLAG_THRESHOLD) -> list[str]:
    """The accounts an investigator should look at, highest risk first."""
    return [str(account) for account in scores[scores["risk_score"] >= threshold].index]
