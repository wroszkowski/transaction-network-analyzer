"""Turn a transaction ledger into a directed payment graph.

Two views of the same data:

* ``build_graph`` keeps every transaction as its own edge, because timing between two accounts is
  itself a fraud signal — collapsing repeats would destroy the burst patterns we look for.
* ``aggregate`` collapses parallel edges into one weighted edge, which is what the structural
  algorithms (components, cycles) need.
"""

import networkx as nx
import pandas as pd

EDGE_ATTRIBUTES = ("transaction_id", "timestamp", "amount", "currency", "device_id", "ip_address", "payment_method")


def build_graph(transactions: pd.DataFrame) -> nx.MultiDiGraph:
    """One node per account, one directed edge per transaction, sender to receiver."""
    graph = nx.MultiDiGraph()
    for row in transactions.to_dict("records"):
        graph.add_edge(
            row["sender_account"],
            row["receiver_account"],
            **{attribute: row[attribute] for attribute in EDGE_ATTRIBUTES},
        )
    return graph


def aggregate(graph: nx.MultiDiGraph) -> nx.DiGraph:
    """Collapse parallel edges into a single edge carrying total value, count and time span.

    The timestamps survive aggregation because structural detectors still need them: a loop of
    payments is only suspicious when it *closes quickly*, and that question cannot be answered from
    topology alone.
    """
    aggregated = nx.DiGraph()
    aggregated.add_nodes_from(graph.nodes)
    for sender, receiver, data in graph.edges(data=True):
        timestamp = data["timestamp"]
        if aggregated.has_edge(sender, receiver):
            edge = aggregated[sender][receiver]
            edge["total_amount"] += data["amount"]
            edge["count"] += 1
            edge["first_seen"] = min(edge["first_seen"], timestamp)
            edge["last_seen"] = max(edge["last_seen"], timestamp)
        else:
            aggregated.add_edge(
                sender,
                receiver,
                total_amount=data["amount"],
                count=1,
                first_seen=timestamp,
                last_seen=timestamp,
            )
    return aggregated
