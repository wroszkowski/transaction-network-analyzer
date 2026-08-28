"""Fraud signals.

Every detector answers one question, returns ``Signal`` objects, and writes the sentence an
investigator will read. Detectors never decide guilt — they contribute weighted evidence, and
``score`` combines them. That separation is what keeps the output explainable: a flag can always be
traced back to the specific structure that produced it.
"""

from dataclasses import dataclass

import networkx as nx
import pandas as pd

MAX_CYCLE_LENGTH = 6
#: A laundering loop closes fast; an accidental one is spread across unrelated weeks of trade.
CYCLE_MAX_SPAN_DAYS = 5.0
#: Ratio of the smallest to the largest hop in a loop. Laundering conserves value; coincidence does not.
CYCLE_MIN_VALUE_COHERENCE = 0.25
VELOCITY_THRESHOLD = 8
FAN_IN_MINIMUM = 5
CONDUIT_RATIO = 0.7
TIGHT_COMPONENT_MAX_SIZE = 25
ONBOARDING_WINDOW_DAYS = 3

SIGNAL_NAMES = (
    "circular_flow",
    "tight_component",
    "velocity_burst",
    "fan_in_fan_out",
    "shared_identifiers",
    "synchronised_onboarding",
)


@dataclass(frozen=True)
class Signal:
    """One piece of evidence against one account."""

    account: str
    signal: str
    strength: float
    evidence: str


def detect_circular_flows(
    aggregated: nx.DiGraph,
    max_length: int = MAX_CYCLE_LENGTH,
    max_span_days: float = CYCLE_MAX_SPAN_DAYS,
    min_coherence: float = CYCLE_MIN_VALUE_COHERENCE,
) -> list[Signal]:
    """Money returning to where it started — but only when the loop behaves like laundering.

    Topology alone is not evidence. In any reasonably connected marketplace, hundreds of accounts
    sit on *some* cycle purely by chance: A buys from B, B buys from C, C happens to buy from A over
    three separate weeks. Flagging those buries the real rings under background noise, which is
    exactly the false-positive failure this tool exists to avoid.

    Two conditions separate a laundering loop from coincidence:

    * **It closes quickly.** Value is moved round the ring in hours or days, not over a month of
      unrelated trade.
    * **The value is conserved.** Roughly the same sum makes every hop, minus a cut. Coincidental
      loops join up payments of wildly different sizes, because nothing connects them.
    """
    signals = []
    for cycle in nx.simple_cycles(aggregated, length_bound=max_length):
        if len(cycle) < 2:
            continue
        edges = [aggregated[cycle[i]][cycle[(i + 1) % len(cycle)]] for i in range(len(cycle))]
        amounts = [edge["total_amount"] for edge in edges]
        coherence = min(amounts) / max(amounts) if max(amounts) else 0.0
        if coherence < min_coherence:
            continue
        span = max(edge["last_seen"] for edge in edges) - min(edge["first_seen"] for edge in edges)
        span_days = span / pd.Timedelta(days=1)
        if span_days > max_span_days:
            continue

        value = sum(amounts)
        path = " -> ".join([*cycle, cycle[0]])
        strength = min(1.0, len(cycle) / max_length + 0.4)
        for account in cycle:
            signals.append(
                Signal(
                    account=account,
                    signal="circular_flow",
                    strength=strength,
                    evidence=(
                        f"sits on a {len(cycle)}-account loop {path} carrying {value:,.0f} in total, "
                        f"closed within {span_days:.2f} day(s) with {coherence:.0%} of the value "
                        f"preserved at every hop"
                    ),
                )
            )
    return signals


def detect_tight_components(aggregated: nx.DiGraph) -> list[Signal]:
    """A small cluster that transacts almost exclusively with itself.

    Ordinary marketplace users are embedded in the wider graph. A group that is dense internally and
    barely connected outward is behaving like a closed economy, which is what a ring looks like.
    """
    signals = []
    for component in nx.weakly_connected_components(aggregated):
        size = len(component)
        if not 3 <= size <= TIGHT_COMPONENT_MAX_SIZE:
            continue
        subgraph = aggregated.subgraph(component)
        density = subgraph.number_of_edges() / size
        if density < 1.2:
            continue
        strength = min(1.0, density / 2.5)
        for account in component:
            signals.append(
                Signal(
                    account=account,
                    signal="tight_component",
                    strength=strength,
                    evidence=(
                        f"belongs to an isolated {size}-account cluster with "
                        f"{subgraph.number_of_edges()} internal payment relationships and no outside ties"
                    ),
                )
            )
    return signals


def detect_velocity_bursts(metrics: pd.DataFrame, threshold: int = VELOCITY_THRESHOLD) -> list[Signal]:
    """Bursts of activity in a short window — the signature of a drained account."""
    hits = metrics[metrics["peak_velocity"] >= threshold]
    return [
        Signal(
            account=str(account),
            signal="velocity_burst",
            strength=min(1.0, row["peak_velocity"] / (threshold * 2)),
            evidence=f"{int(row['peak_velocity'])} transactions inside a single hour",
        )
        for account, row in hits.iterrows()
    ]


def detect_fan_in_fan_out(metrics: pd.DataFrame) -> list[Signal]:
    """Many small payments in, few large payments out. Classic smurfing / collector behaviour.

    The conduit test matters: an account that receives from many people and *keeps* the money is a
    popular seller, not a mule. Only money that moves straight back out is suspicious.
    """
    candidates = metrics[
        (metrics["in_degree"] >= FAN_IN_MINIMUM)
        & (metrics["pass_through_ratio"] >= CONDUIT_RATIO)
        & (metrics["out_degree"] < metrics["in_degree"] / 2)
    ]
    return [
        Signal(
            account=str(account),
            signal="fan_in_fan_out",
            strength=min(1.0, row["in_degree"] / (FAN_IN_MINIMUM * 3)),
            evidence=(
                f"collected {int(row['in_degree'])} inbound payments and forwarded "
                f"{row['pass_through_ratio']:.0%} of the value out through "
                f"{int(row['out_degree'])} transfers"
            ),
        )
        for account, row in candidates.iterrows()
    ]


def detect_shared_identifiers(transactions: pd.DataFrame) -> list[Signal]:
    """Distinct accounts operating from the same device or IP — one person, many identities."""
    signals = []
    for column, label in (("device_id", "device"), ("ip_address", "IP address")):
        by_identifier = transactions.groupby(column)["sender_account"].apply(lambda s: sorted(set(s)))
        for identifier, accounts in by_identifier.items():
            if len(accounts) < 2:
                continue
            others = ", ".join(accounts)
            for account in accounts:
                signals.append(
                    Signal(
                        account=account,
                        signal="shared_identifiers",
                        strength=min(1.0, 0.4 + 0.2 * len(accounts)),
                        evidence=f"shares {label} {identifier} with {len(accounts) - 1} other account(s): {others}",
                    )
                )
    return signals


def detect_synchronised_onboarding(
    accounts: pd.DataFrame,
    metrics: pd.DataFrame,
    window_days: int = ONBOARDING_WINDOW_DAYS,
) -> list[Signal]:
    """A cluster whose members all signed up within days of each other was provisioned, not grown."""
    created = accounts.set_index("account_id")["created_at"]
    signals = []
    for component_id, group in metrics.groupby("component_id"):
        members = [a for a in group.index if a in created.index]
        if len(members) < 4:
            continue
        dates = pd.to_datetime(created.loc[members])
        spread_days = (dates.max() - dates.min()).days
        if spread_days > window_days:
            continue
        for account in members:
            signals.append(
                Signal(
                    account=str(account),
                    signal="synchronised_onboarding",
                    strength=min(1.0, 1.0 - spread_days / (window_days + 1)),
                    evidence=(
                        f"all {len(members)} accounts in cluster {component_id} were created "
                        f"within {spread_days} day(s) of each other"
                    ),
                )
            )
    return signals


def run_all(
    transactions: pd.DataFrame,
    accounts: pd.DataFrame,
    aggregated: nx.DiGraph,
    metrics: pd.DataFrame,
) -> list[Signal]:
    """Every detector, one list of evidence."""
    return [
        *detect_circular_flows(aggregated),
        *detect_tight_components(aggregated),
        *detect_velocity_bursts(metrics),
        *detect_fan_in_fan_out(metrics),
        *detect_shared_identifiers(transactions),
        *detect_synchronised_onboarding(accounts, metrics),
    ]
