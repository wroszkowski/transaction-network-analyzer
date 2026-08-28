"""Shared fixtures: tiny hand-built transaction sets with known topology."""

from collections.abc import Sequence

import pandas as pd
import pytest

COLUMNS = [
    "transaction_id",
    "timestamp",
    "sender_account",
    "receiver_account",
    "amount",
    "currency",
    "device_id",
    "ip_address",
    "payment_method",
]


def make_txns(rows: Sequence[tuple[str, str, float, int]]) -> pd.DataFrame:
    """Build a transaction frame from (sender, receiver, amount, minutes_offset) tuples."""
    base = pd.Timestamp("2026-01-01 00:00:00")
    records = [
        {
            "transaction_id": f"txn_{i:04d}",
            "timestamp": base + pd.Timedelta(minutes=offset),
            "sender_account": sender,
            "receiver_account": receiver,
            "amount": float(amount),
            "currency": "NGN",
            "device_id": f"dev_{sender}",
            "ip_address": f"10.0.0.{i}",
            "payment_method": "wallet",
        }
        for i, (sender, receiver, amount, offset) in enumerate(rows)
    ]
    return pd.DataFrame.from_records(records, columns=COLUMNS)


@pytest.fixture
def cycle_txns() -> pd.DataFrame:
    """A -> B -> C -> A, a closed three-node loop."""
    return make_txns([("A", "B", 1000, 0), ("B", "C", 950, 10), ("C", "A", 900, 20)])


@pytest.fixture
def star_txns() -> pd.DataFrame:
    """A legitimate hub: HUB receives from six distinct accounts, never loops."""
    return make_txns([(f"S{i}", "HUB", 100 + i, i * 600) for i in range(6)])
