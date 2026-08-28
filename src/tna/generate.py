"""Synthetic BazaarAfrica transaction data with known ground truth.

No dataset was supplied with the brief, so one is built here. Generating it is an advantage rather
than a compromise: because the planted fraud is labelled, the detector can be *measured* against it
instead of merely demonstrated.

The dataset deliberately contains a **legitimate high-degree hub** — a popular seller with more
counterparties than anyone in the fraud rings. Any detector that equates "busy" with "criminal"
flags them, which is the failure mode that makes fraud tooling unusable in practice.
"""

import random
from dataclasses import dataclass, field

import pandas as pd

START = pd.Timestamp("2026-07-01 08:00:00")
DAYS = 30
CURRENCIES = ("NGN", "KES", "ZAR", "GHS")
PAYMENT_METHODS = ("wallet", "bank_transfer", "card", "ussd")
COUNTRY_BY_CURRENCY = {"NGN": "Nigeria", "KES": "Kenya", "ZAR": "South Africa", "GHS": "Ghana"}

BACKGROUND_ACCOUNTS = 90
BACKGROUND_TRANSACTIONS = 340
HUB_TRANSACTIONS = 36


@dataclass
class Dataset:
    """Generated transactions, the account profiles behind them, and what is actually fraudulent."""

    transactions: pd.DataFrame
    accounts: pd.DataFrame
    ground_truth: dict[str, str]
    legit_hub: str


@dataclass
class _Builder:
    """Accumulates rows while keeping account profiles and labels consistent."""

    rng: random.Random
    rows: list[dict[str, object]] = field(default_factory=list)
    profiles: dict[str, dict[str, object]] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)

    def account(self, account_id: str, created_at: pd.Timestamp, currency: str, label: str | None = None) -> str:
        self.profiles.setdefault(
            account_id,
            {
                "account_id": account_id,
                "created_at": created_at,
                "country": COUNTRY_BY_CURRENCY[currency],
                "currency": currency,
                "kyc_level": self.rng.choice(["basic", "basic", "verified"]),
            },
        )
        if label:
            self.labels[account_id] = label
        return account_id

    def pay(
        self,
        sender: str,
        receiver: str,
        amount: float,
        at: pd.Timestamp,
        device: str | None = None,
        ip: str | None = None,
    ) -> None:
        currency = self.profiles[sender]["currency"]
        self.rows.append(
            {
                "transaction_id": f"txn_{len(self.rows):06d}",
                "timestamp": at,
                "sender_account": sender,
                "receiver_account": receiver,
                "amount": round(amount, 2),
                "currency": currency,
                "device_id": device or f"dev_{sender}",
                "ip_address": ip or f"197.{self.rng.randint(0, 255)}.{self.rng.randint(0, 255)}.{sender[-2:]}",
                "payment_method": self.rng.choice(PAYMENT_METHODS),
            }
        )

    def when(self, day: float) -> pd.Timestamp:
        moment = START + pd.Timedelta(days=day)
        assert isinstance(moment, pd.Timestamp)  # a finite offset from a real timestamp is never NaT
        return moment


def generate(seed: int = 42) -> Dataset:
    """Build the demo dataset. Deterministic for a given seed."""
    builder = _Builder(rng=random.Random(seed))

    _background(builder)
    hub = _legitimate_hub(builder)
    _ring_a_circular(builder)
    _ring_b_smurfing(builder)
    _ring_c_takeover(builder)

    transactions = pd.DataFrame(builder.rows).sort_values("timestamp").reset_index(drop=True)
    transactions["transaction_id"] = [f"txn_{i:06d}" for i in range(len(transactions))]
    accounts = pd.DataFrame(builder.profiles.values()).sort_values("account_id").reset_index(drop=True)
    return Dataset(
        transactions=transactions[
            [
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
        ],
        accounts=accounts,
        ground_truth=dict(builder.labels),
        legit_hub=hub,
    )


def _background(builder: _Builder) -> None:
    """Ordinary P2P traffic: unrelated people paying each other at irregular intervals."""
    rng = builder.rng
    ids = [
        builder.account(
            f"ACC_{i:03d}",
            builder.when(-rng.uniform(30, 900)),
            rng.choice(CURRENCIES),
        )
        for i in range(BACKGROUND_ACCOUNTS)
    ]
    # One household sharing an internet connection: a shared IP on its own must not flag anyone.
    household_ip = "197.210.44.10"

    for _ in range(BACKGROUND_TRANSACTIONS):
        sender, receiver = rng.sample(ids, 2)
        ip = household_ip if sender in (ids[3], ids[4]) else None
        builder.pay(sender, receiver, rng.lognormvariate(9.0, 1.1), builder.when(rng.uniform(0, DAYS)), ip=ip)


def _legitimate_hub(builder: _Builder) -> str:
    """A popular seller. High degree, many counterparties, entirely honest.

    They keep what they earn rather than passing it on, and their trade is spread across the month
    instead of concentrated in bursts — the two things that separate a busy merchant from a mule.
    """
    rng = builder.rng
    hub = builder.account("ACC_HUB_SELLER", builder.when(-620), "NGN")
    buyers = [f"ACC_{i:03d}" for i in rng.sample(range(BACKGROUND_ACCOUNTS), HUB_TRANSACTIONS)]
    for day, buyer in enumerate(buyers):
        builder.pay(buyer, hub, rng.lognormvariate(9.4, 0.7), builder.when(day * DAYS / HUB_TRANSACTIONS))
    return hub


def _ring_a_circular(builder: _Builder) -> None:
    """Ten accounts, freshly minted, moving the same money round overlapping loops.

    Layered short cycles rather than one long loop, which is what real laundering looks like: value
    is broken up and recombined so no single hop looks unusual.
    """
    rng = builder.rng
    label = "ring_a_circular"
    devices = ["dev_shared_a1", "dev_shared_a2"]
    members = [
        builder.account(f"RNG_A_{i:02d}", builder.when(-rng.uniform(2, 5)), "NGN", label=label) for i in range(10)
    ]

    day = 12.0
    for start in range(0, 10, 2):
        loop = [members[start], members[(start + 1) % 10], members[(start + 2) % 10]]
        amount = rng.uniform(400_000, 900_000)
        for i, sender in enumerate(loop):
            builder.pay(
                sender,
                loop[(i + 1) % len(loop)],
                amount * rng.uniform(0.93, 0.99),
                builder.when(day + i * 0.02),
                device=devices[start % 2],
            )
        day += 0.4

    for _ in range(6):
        a, b = rng.sample(members, 2)
        builder.pay(a, b, rng.uniform(200_000, 500_000), builder.when(rng.uniform(12, 16)), device=rng.choice(devices))


def _ring_b_smurfing(builder: _Builder) -> None:
    """Twelve feeder accounts consolidating into one collector, with value layered back out."""
    rng = builder.rng
    label = "ring_b_smurfing"
    collector = builder.account("SMR_COLLECTOR", builder.when(-rng.uniform(1, 3)), "KES", label=label)
    feeders = [
        builder.account(f"SMR_F{i:02d}", builder.when(-rng.uniform(1, 3)), "KES", label=label) for i in range(12)
    ]
    exits = [builder.account(f"SMR_EXIT_{i}", builder.when(-rng.uniform(1, 3)), "KES", label=label) for i in range(2)]
    devices = ["dev_shared_b1", "dev_shared_b2", "dev_shared_b3"]

    base = 18.0
    for i, feeder in enumerate(feeders):
        builder.pay(
            feeder, collector, rng.uniform(45_000, 60_000), builder.when(base + i * 0.002), device=devices[i % 3]
        )

    # Layering: value returns to half the feeders and is sent in again, closing short loops.
    for feeder in feeders[:6]:
        builder.pay(collector, feeder, rng.uniform(20_000, 30_000), builder.when(base + 0.1), device=devices[0])
        builder.pay(feeder, collector, rng.uniform(19_000, 29_000), builder.when(base + 0.2), device=devices[1])

    for feeder, exit_account in zip(feeders[6:8], exits, strict=True):
        builder.pay(feeder, exit_account, rng.uniform(80_000, 120_000), builder.when(base + 0.3), device=devices[2])
    for exit_account in exits:
        builder.pay(exit_account, collector, rng.uniform(70_000, 110_000), builder.when(base + 0.4), device=devices[2])


def _ring_c_takeover(builder: _Builder) -> None:
    """A long-dormant account drained to six mules in minutes, which then launder among themselves."""
    rng = builder.rng
    label = "ring_c_takeover"
    victim = builder.account("ATO_VICTIM", builder.when(-1400), "ZAR", label=label)
    mules = [builder.account(f"ATO_MULE_{i}", builder.when(-rng.uniform(2, 4)), "ZAR", label=label) for i in range(6)]
    device = "dev_takeover_kit"

    drain_start = 22.0
    for i in range(9):
        builder.pay(
            victim,
            mules[i % 6],
            rng.uniform(15_000, 40_000),
            builder.when(drain_start + i * 0.0008),
            device=device,
            ip="41.79.12.9",
        )

    for group in (mules[:3], mules[3:]):
        for i, sender in enumerate(group):
            builder.pay(
                sender,
                group[(i + 1) % len(group)],
                rng.uniform(10_000, 30_000),
                builder.when(drain_start + 0.05 + i * 0.01),
                device=device,
            )
    builder.pay(mules[2], mules[3], rng.uniform(10_000, 25_000), builder.when(drain_start + 0.2), device=device)
