"""End-to-end: generate the demo dataset, analyse it, and hold the result to account."""

import pytest

from tna.analyze import analyze
from tna.generate import generate

from .conftest import COLUMNS


@pytest.fixture(scope="module")
def dataset():
    return generate(seed=42)


@pytest.fixture(scope="module")
def result(dataset):
    return analyze(dataset.transactions, dataset.accounts, dataset.ground_truth)


def test_generated_data_meets_the_brief_minimums(dataset):
    accounts = set(dataset.transactions["sender_account"]) | set(dataset.transactions["receiver_account"])

    assert len(dataset.transactions) >= 300
    assert len(accounts) >= 50
    assert set(dataset.transactions.columns) == set(COLUMNS)


def test_generated_data_spans_the_four_market_currencies(dataset):
    assert set(dataset.transactions["currency"]) == {"NGN", "KES", "ZAR", "GHS"}


def test_transaction_ids_are_unique(dataset):
    assert dataset.transactions["transaction_id"].is_unique


def test_every_transacting_account_has_a_profile(dataset):
    transacting = set(dataset.transactions["sender_account"]) | set(dataset.transactions["receiver_account"])

    assert transacting <= set(dataset.accounts["account_id"])


def test_generation_is_reproducible_for_a_fixed_seed():
    a, b = generate(seed=7), generate(seed=7)

    assert a.transactions.equals(b.transactions)


def test_the_planted_rings_are_all_labelled(dataset):
    assert set(dataset.ground_truth.values()) == {"ring_a_circular", "ring_b_smurfing", "ring_c_takeover"}


def test_the_legitimate_hub_is_high_degree(dataset, result):
    """Precondition for the false-positive test below: the trap has to be baited."""
    hub = dataset.legit_hub
    degree = result.metrics.loc[hub, "in_degree"] + result.metrics.loc[hub, "out_degree"]

    assert degree >= 30
    assert hub not in dataset.ground_truth


def test_the_legitimate_hub_is_not_flagged(dataset, result):
    """The point of the whole exercise: busy is not the same as fraudulent."""
    assert dataset.legit_hub not in result.flagged


def test_the_detector_finds_most_of_the_planted_fraud(result):
    assert result.evaluation["recall"] >= 0.8


def test_the_detector_does_not_drown_investigators_in_false_positives(result):
    assert result.evaluation["precision"] >= 0.8


def test_every_flagged_account_comes_with_its_reasons(result):
    for account in result.flagged:
        assert result.scores.loc[account, "reasons"], f"{account} was flagged with no justification"
