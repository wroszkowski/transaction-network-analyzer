import pytest

from tna.detectors import Signal
from tna.evaluate import evaluate
from tna.score import WEIGHTS, score_accounts


def sig(account: str, signal: str, strength: float = 1.0) -> Signal:
    return Signal(account=account, signal=signal, strength=strength, evidence=f"{signal} on {account}")


def test_every_signal_the_detectors_emit_has_a_documented_weight():
    from tna import detectors

    assert set(detectors.SIGNAL_NAMES) == set(WEIGHTS)


def test_an_account_with_no_signals_scores_zero():
    scored = score_accounts([sig("A", "circular_flow")], universe=["A", "B"])

    assert scored.loc["B", "risk_score"] == 0
    assert scored.loc["B", "reasons"] == []


def test_score_increases_with_signal_strength():
    weak = score_accounts([sig("A", "circular_flow", 0.2)], universe=["A"])
    strong = score_accounts([sig("A", "circular_flow", 0.9)], universe=["A"])

    assert strong.loc["A", "risk_score"] > weak.loc["A", "risk_score"]


def test_multiple_distinct_signals_accumulate():
    one = score_accounts([sig("A", "circular_flow")], universe=["A"])
    two = score_accounts([sig("A", "circular_flow"), sig("A", "velocity_burst")], universe=["A"])

    assert two.loc["A", "risk_score"] > one.loc["A", "risk_score"]


def test_score_is_clipped_to_one_hundred():
    everything = [sig("A", name) for name in WEIGHTS]

    scored = score_accounts(everything, universe=["A"])

    assert scored.loc["A", "risk_score"] <= 100


def test_reasons_carry_the_evidence_forward():
    scored = score_accounts([sig("A", "circular_flow")], universe=["A"])

    assert "circular_flow on A" in scored.loc["A", "reasons"]


def test_results_are_ranked_most_suspicious_first():
    scored = score_accounts(
        [sig("LOW", "shared_identifiers", 0.1), sig("HIGH", "circular_flow", 1.0)],
        universe=["LOW", "HIGH"],
    )

    assert list(scored.index)[0] == "HIGH"


def test_unknown_signal_names_are_rejected_rather_than_silently_ignored():
    with pytest.raises(KeyError):
        score_accounts([sig("A", "not_a_real_signal")], universe=["A"])


def test_evaluate_reports_precision_recall_and_the_accounts_behind_them():
    truth = {"F1": "ring_a", "F2": "ring_a", "F3": "ring_b"}

    result = evaluate(flagged={"F1", "F2", "GOOD"}, truth=truth)

    assert result["true_positives"] == ["F1", "F2"]
    assert result["false_positives"] == ["GOOD"]
    assert result["false_negatives"] == ["F3"]
    assert result["precision"] == pytest.approx(2 / 3)
    assert result["recall"] == pytest.approx(2 / 3)
    assert result["f1"] == pytest.approx(2 / 3)


def test_evaluate_handles_flagging_nothing_without_dividing_by_zero():
    result = evaluate(flagged=set(), truth={"F1": "ring_a"})

    assert result["precision"] == 0.0
    assert result["recall"] == 0.0
    assert result["f1"] == 0.0
