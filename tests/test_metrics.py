from tna.graph import build_graph
from tna.metrics import compute_metrics

from .conftest import make_txns


def test_degree_counts_incoming_and_outgoing_transactions(star_txns):
    m = compute_metrics(star_txns, build_graph(star_txns))

    assert m.loc["HUB", "in_degree"] == 6
    assert m.loc["HUB", "out_degree"] == 0
    assert m.loc["S0", "out_degree"] == 1


def test_value_totals_and_net_flow():
    txns = make_txns([("A", "B", 100, 0), ("B", "C", 60, 10)])

    m = compute_metrics(txns, build_graph(txns))

    assert m.loc["B", "total_in"] == 100.0
    assert m.loc["B", "total_out"] == 60.0
    assert m.loc["B", "net_flow"] == 40.0


def test_pass_through_ratio_is_high_for_a_mule_and_zero_for_a_sink():
    txns = make_txns([("A", "M", 100, 0), ("M", "B", 98, 5), ("A", "SINK", 100, 0)])

    m = compute_metrics(txns, build_graph(txns))

    assert m.loc["M", "pass_through_ratio"] == 0.98
    assert m.loc["SINK", "pass_through_ratio"] == 0.0


def test_peak_velocity_counts_transactions_in_the_busiest_window():
    # five transactions inside ten minutes, then one a day later
    rows = [("V", f"R{i}", 10, i * 2) for i in range(5)] + [("V", "R9", 10, 60 * 24)]

    m = compute_metrics(make_txns(rows), build_graph(make_txns(rows)), window_minutes=60)

    assert m.loc["V", "peak_velocity"] == 5


def test_peak_velocity_window_is_inclusive_at_the_boundary():
    rows = [("V", "R1", 10, 0), ("V", "R2", 10, 60)]

    m = compute_metrics(make_txns(rows), build_graph(make_txns(rows)), window_minutes=60)

    assert m.loc["V", "peak_velocity"] == 2


def test_distinct_counterparties_ignores_repeat_business():
    txns = make_txns([("A", "B", 10, 0), ("A", "B", 10, 5), ("A", "C", 10, 9)])

    m = compute_metrics(txns, build_graph(txns))

    assert m.loc["A", "distinct_counterparties"] == 2


def test_component_membership_is_reported_per_account():
    txns = make_txns([("A", "B", 10, 0), ("X", "Y", 10, 5)])

    m = compute_metrics(txns, build_graph(txns))

    assert m.loc["A", "component_size"] == 2
    assert m.loc["A", "component_id"] != m.loc["X", "component_id"]
