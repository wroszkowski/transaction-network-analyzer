import networkx as nx

from tna.graph import aggregate, build_graph

from .conftest import make_txns


def test_build_graph_makes_a_node_per_account_and_an_edge_per_transaction(cycle_txns):
    g = build_graph(cycle_txns)

    assert isinstance(g, nx.MultiDiGraph)
    assert set(g.nodes) == {"A", "B", "C"}
    assert g.number_of_edges() == 3


def test_edges_are_directed_from_sender_to_receiver(cycle_txns):
    g = build_graph(cycle_txns)

    assert g.has_edge("A", "B")
    assert not g.has_edge("B", "A")


def test_edges_carry_transaction_attributes(cycle_txns):
    g = build_graph(cycle_txns)

    _, _, data = next(iter(g.edges(data=True)))
    assert data["amount"] == 1000.0
    assert data["currency"] == "NGN"
    assert "timestamp" in data


def test_repeated_pairs_are_kept_as_parallel_edges():
    txns = make_txns([("A", "B", 10, 0), ("A", "B", 20, 5), ("A", "B", 30, 9)])

    g = build_graph(txns)

    assert g.number_of_edges("A", "B") == 3


def test_aggregate_collapses_parallel_edges_into_weighted_edges():
    txns = make_txns([("A", "B", 10, 0), ("A", "B", 20, 5)])

    agg = aggregate(build_graph(txns))

    assert isinstance(agg, nx.DiGraph)
    assert agg["A"]["B"]["total_amount"] == 30.0
    assert agg["A"]["B"]["count"] == 2
