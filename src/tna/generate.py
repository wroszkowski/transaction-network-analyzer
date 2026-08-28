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
    #: Innocent accounts planted to look guilty, mapped to why they superficially look suspicious.
    #: Deliberately absent from ``ground_truth``: every one of them is honest. Each attacks one
    #: detector, so that the score is measured against difficult negatives rather than trivial ones.
    hard_negatives: dict[str, str] = field(default_factory=dict)


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

    hard_negatives: dict[str, str] = {}
    for plant in (
        _bursty_ticket_seller,
        _shared_device_household,
        _business_settlement_pair,
        _isolated_friend_group,
        _referral_cohort,
    ):
        hard_negatives.update(plant(builder))

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
        hard_negatives=hard_negatives,
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


# --------------------------------------------------------------------------------------------
# Hard negatives: innocent accounts that look guilty.
#
# The legitimate hub is one hard case, and one is not a test. Every population below is honest and
# unlabelled, and each is built to attack a specific detector from the inside — a bursty merchant
# against the velocity window, a family tablet against shared identifiers, a shop's own settlement
# loop against the two constraints that keep cycle detection honest, a flat-share and a referral
# cohort against the structural detectors. None of them is softened to keep the score clean.
# --------------------------------------------------------------------------------------------

MINUTE = 1.0 / (24 * 60)


def _bursty_ticket_seller(builder: _Builder) -> dict[str, str]:
    """An event ticket seller. Two drops in the month, ~15 sales in half an hour each.

    Attacks ``detect_velocity_bursts``, which reads a spike inside one hour as a drained account.
    This one should survive the rest of the scoring: the money stays put (a seller banks its
    takings instead of forwarding them) and the buyers are ordinary marketplace accounts, so the
    seller sits in the middle of the main component rather than on an island.
    """
    rng = builder.rng
    seller = builder.account("MERCH_TICKETS_88", builder.when(-410), "GHS")

    for drop, day in enumerate((9.6, 23.55)):
        buyers = [f"ACC_{i:03d}" for i in rng.sample(range(BACKGROUND_ACCOUNTS), 15)]
        for i, buyer in enumerate(buyers):
            builder.pay(
                buyer,
                seller,
                rng.uniform(2_800, 9_500),
                builder.when(day + i * 2 * MINUTE + rng.uniform(0, 1) * MINUTE),
                ip=f"197.44.{drop}.{i:02d}",
            )

    # Costs go out afterwards — the venue's cut and a printing bill. A quarter of the takings,
    # nowhere near the 70% pass-through that marks a conduit.
    for i, day in enumerate((11.4, 25.7)):
        builder.pay(seller, f"ACC_{7 + i:03d}", rng.uniform(18_000, 24_000), builder.when(day), ip="197.44.9.88")

    return {
        seller: (
            "sells 15 tickets in half an hour twice in the month, so its peak velocity looks like an "
            "account being emptied, when it is a drop-day queue of ordinary buyers"
        )
    }


def _shared_device_household(builder: _Builder) -> dict[str, str]:
    """A parent and two teenagers on one family tablet, who also send money to each other.

    Attacks ``detect_shared_identifiers`` — three accounts, one ``device_id``, one home IP — and
    plants genuine short loops between family members, which is where the cycle detector has to
    tell reimbursement apart from layering.
    """
    rng = builder.rng
    tablet = "dev_family_tablet"
    home_ip = "197.210.77.32"
    parent = builder.account("HH_PARENT_01", builder.when(-1180), "NGN")
    teen_a = builder.account("HH_TEEN_02", builder.when(-260), "NGN")
    teen_b = builder.account("HH_TEEN_03", builder.when(-95), "NGN")

    def home(sender: str, receiver: str, amount: float, day: float) -> None:
        builder.pay(sender, receiver, amount, builder.when(day), device=tablet, ip=home_ip)

    # Pocket money, twice in the month, and the elder one paying a share of the phone bill back.
    for day in (3.4, 17.6):
        home(parent, teen_a, rng.uniform(11_000, 14_000), day)
    home(teen_a, parent, rng.uniform(7_500, 9_000), 6.2)

    # The younger one only gets money once, for a school trip, and returns what is left the next
    # morning. Two hops, one day apart, similar sizes — a laundering loop in every measurable way.
    home(parent, teen_b, 12_000, 12.3)
    home(teen_b, parent, 4_100, 13.1)

    # The household is not an island: all three shop on the marketplace like everyone else.
    for account, counterparty, day in (
        (parent, "ACC_012", 4.1),
        (teen_a, "ACC_031", 8.8),
        (teen_b, "ACC_045", 19.2),
        (parent, "ACC_067", 26.4),
    ):
        builder.pay(account, counterparty, rng.lognormvariate(8.6, 0.6), builder.when(day), device=tablet, ip=home_ip)

    return {
        parent: (
            "shares a device and a home IP with two other accounts and exchanges money with both, "
            "which is the shape of one person running several identities"
        ),
        teen_a: "sends money to and receives money from the account it shares a family tablet with",
        teen_b: (
            "returns part of a transfer to the sender the next day on a shared device, closing a "
            "fast, value-coherent two-account loop out of a school-trip reimbursement"
        ),
    }


def _business_settlement_pair(builder: _Builder) -> dict[str, str]:
    """A shop that runs a trading account and a payouts account and settles between them.

    This is the sharpest case in the set. ``detect_circular_flows`` rejects coincidental loops on
    two conditions — the loop must close quickly and conserve value — and a month-end settlement
    run satisfies both by design: the two accounts move nearly the same sum back and forth inside
    three days, from the same back-office terminal, because they belong to the same business.
    """
    rng = builder.rng
    terminal = "dev_shop_backoffice"
    trading = builder.account("SHOP_TRADE_71", builder.when(-505), "KES")
    payouts = builder.account("SHOP_PAYOUT_72", builder.when(-505), "KES")

    # Customers pay the trading account across the month.
    for i, day in enumerate((2.3, 5.1, 7.9, 10.2, 13.6, 16.4, 19.8, 21.3, 24.1, 27.7)):
        builder.pay(
            f"ACC_{20 + i * 3:03d}",
            trading,
            rng.uniform(30_000, 90_000),
            builder.when(day),
            ip=f"197.61.{i:02d}.20",
        )

    # Month-end reconciliation: the float moves to payouts, part of it comes back to cover card
    # settlements that had not cleared, and the net is swept out again. Same money, three days.
    for day, amount in ((24.6, 210_000), (25.4, 185_000), (26.2, 195_000)):
        builder.pay(trading, payouts, amount * rng.uniform(0.97, 1.02), builder.when(day), device=terminal)
    for day, amount in ((24.9, 175_000), (25.8, 160_000)):
        builder.pay(payouts, trading, amount * rng.uniform(0.97, 1.02), builder.when(day), device=terminal)

    # Suppliers and staff get paid out of the payouts account.
    for i, day in enumerate((26.9, 27.4, 28.1)):
        builder.pay(
            payouts,
            f"ACC_{55 + i * 4:03d}",
            rng.uniform(60_000, 110_000),
            builder.when(day),
            device=terminal,
            ip=f"197.62.{i:02d}.72",
        )

    return {
        trading: (
            "sends and receives nearly identical sums to its own payouts account three times inside "
            "three days from a shared terminal — a two-account loop that closes fast and conserves "
            "value, which is exactly what the cycle detector is looking for"
        ),
        payouts: (
            "the other half of the same month-end settlement loop, sharing a device with the "
            "account it cycles value with"
        ),
    }


def _isolated_friend_group(builder: _Builder) -> dict[str, str]:
    """Five friends splitting rent and bills, who happen to pay nobody else.

    Attacks ``detect_tight_components``. Structurally this is a small fraud ring: a handful of
    accounts, dense internal payment relationships, no ties outside the group. The only difference
    is intent, and intent is not in the ledger.
    """
    rng = builder.rng
    # They signed up years apart, so nothing about their onboarding is synchronised.
    ages = (740, 512, 388, 205, 96)
    friends = [builder.account(f"FRND_R{i + 1}", builder.when(-age), "ZAR") for i, age in enumerate(ages)]
    lease, bills, groceries = friends[0], friends[2], friends[4]

    # Rent day: everyone pays whoever holds the lease.
    for i, friend in enumerate(friends[1:]):
        builder.pay(friend, lease, rng.uniform(4_200, 4_800), builder.when(1.5 + i * 0.05))
    # Electricity and water, reimbursed to whoever paid the utility.
    for i, friend in enumerate([f for f in friends if f is not bills]):
        builder.pay(friend, bills, rng.uniform(600, 950), builder.when(8.3 + i * 0.4))
    # A shared weekly grocery run.
    for i, friend in enumerate(friends[1:3]):
        builder.pay(friend, groceries, rng.uniform(700, 1_400), builder.when(15.2 + i * 0.2))
    # Odds and ends between individuals, including money going back the way it came.
    builder.pay(lease, friends[1], rng.uniform(900, 1_300), builder.when(4.1))
    builder.pay(friends[1], lease, rng.uniform(800, 1_200), builder.when(5.6))
    builder.pay(bills, groceries, rng.uniform(500, 900), builder.when(16.4))
    builder.pay(groceries, friends[3], rng.uniform(400, 800), builder.when(22.7))
    builder.pay(friends[3], friends[1], rng.uniform(300, 700), builder.when(23.9))

    return {
        friend: (
            "belongs to a five-account flat-share that pays rent and bills only to each other, "
            "forming a dense component with no outside ties — structurally indistinguishable from a "
            "small ring"
        )
        for friend in friends
    }


def _referral_cohort(builder: _Builder) -> dict[str, str]:
    """Six accounts a marketing push signed up in the same two days, trading among themselves.

    Attacks ``detect_synchronised_onboarding``. A referral campaign produces the same fingerprint
    as a provisioned ring — accounts created together, transacting together — because a referral
    campaign *is* a group of people onboarded together, by the platform's own marketing team.
    """
    rng = builder.rng
    cohort = [
        builder.account(f"REF_C{i}", builder.when(-rng.uniform(9, 11)), "GHS")  # a two-day signup window
        for i in range(6)
    ]

    # A campaign WhatsApp group turning into small trades: second-hand phones, hair products, shoes.
    trades = [
        (0, 1, 12.1),
        (1, 2, 13.4),
        (2, 0, 14.9),
        (3, 1, 16.2),
        (4, 3, 17.8),
        (5, 4, 19.1),
        (0, 5, 20.6),
        (2, 4, 22.3),
        (1, 3, 24.5),
    ]
    for sender, receiver, day in trades:
        builder.pay(cohort[sender], cohort[receiver], rng.uniform(2_500, 11_000), builder.when(day))
    # Two of them trade both ways within a few days — one buys, then sells something back.
    builder.pay(cohort[3], cohort[4], rng.uniform(6_000, 9_000), builder.when(18.4))

    return {
        account: (
            "signed up inside the same two-day referral campaign as five other accounts it now "
            "trades with, which reads as a batch-provisioned cluster"
        )
        for account in cohort
    }
