"""Per-account network and behavioural metrics.

Each metric here is chosen because it distinguishes a *shape* of behaviour, not a threshold on a
single transaction — that is the whole reason for modelling payments as a graph.
"""

import networkx as nx
import pandas as pd

DEFAULT_WINDOW_MINUTES = 60


def compute_metrics(
    transactions: pd.DataFrame,
    graph: nx.MultiDiGraph,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
) -> pd.DataFrame:
    """Return one row per account, indexed by account id.

    Columns:
        in_degree, out_degree      how many transactions received / sent
        distinct_counterparties    how many *different* accounts, ignoring repeat business
        total_in, total_out        value received / sent
        net_flow                   value retained (in minus out)
        pass_through_ratio         share of received value that left again — the mule signature
        peak_velocity              most transactions in any rolling window
        component_id, component_size   which weakly connected cluster the account sits in
    """
    accounts = sorted(set(transactions["sender_account"]) | set(transactions["receiver_account"]))
    sent = transactions.groupby("sender_account")["amount"]
    received = transactions.groupby("receiver_account")["amount"]

    frame = pd.DataFrame(index=pd.Index(accounts, name="account_id"))
    frame["in_degree"] = received.count().reindex(accounts).fillna(0).astype(int)
    frame["out_degree"] = sent.count().reindex(accounts).fillna(0).astype(int)
    frame["total_in"] = received.sum().reindex(accounts).fillna(0.0)
    frame["total_out"] = sent.sum().reindex(accounts).fillna(0.0)
    frame["net_flow"] = frame["total_in"] - frame["total_out"]
    frame["pass_through_ratio"] = _pass_through_ratio(frame)
    frame["distinct_counterparties"] = _distinct_counterparties(graph, accounts)
    frame["peak_velocity"] = _peak_velocity(transactions, accounts, window_minutes)

    components = {
        account: index for index, component in enumerate(nx.weakly_connected_components(graph)) for account in component
    }
    sizes = pd.Series(components).value_counts()
    frame["component_id"] = [components[account] for account in accounts]
    frame["component_size"] = [int(sizes[components[account]]) for account in accounts]
    return frame


def _pass_through_ratio(frame: pd.DataFrame) -> pd.Series:
    """How much of what came in went straight back out. 1.0 means a pure conduit."""
    ratio = frame["total_out"] / frame["total_in"].where(frame["total_in"] > 0)
    return pd.Series(ratio.fillna(0.0).clip(upper=1.0).round(6), index=frame.index)


def _distinct_counterparties(graph: nx.MultiDiGraph, accounts: list[str]) -> list[int]:
    return [len(set(graph.predecessors(a)) | set(graph.successors(a))) for a in accounts]


def _peak_velocity(transactions: pd.DataFrame, accounts: list[str], window_minutes: int) -> list[int]:
    """Largest number of transactions an account was party to within any window of that length."""
    window = pd.Timedelta(minutes=window_minutes)
    involvement = pd.concat(
        [
            transactions[["sender_account", "timestamp"]].rename(columns={"sender_account": "account"}),
            transactions[["receiver_account", "timestamp"]].rename(columns={"receiver_account": "account"}),
        ]
    )
    peaks = {}
    for account, group in involvement.groupby("account"):
        times = group["timestamp"].sort_values().to_numpy()
        peaks[account] = max(
            (int(((times >= start) & (times <= start + window)).sum()) for start in times),
            default=0,
        )
    return [peaks.get(account, 0) for account in accounts]
