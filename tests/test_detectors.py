from tna.detectors import (
    detect_circular_flows,
    detect_fan_in_fan_out,
    detect_shared_identifiers,
    detect_velocity_bursts,
)
from tna.graph import aggregate, build_graph
from tna.metrics import compute_metrics

from .conftest import make_txns


def test_a_three_node_loop_is_detected_as_a_circular_flow(cycle_txns):
    signals = detect_circular_flows(aggregate(build_graph(cycle_txns)))

    assert {s.account for s in signals} == {"A", "B", "C"}
    assert all(s.signal == "circular_flow" for s in signals)


def test_circular_flow_evidence_names_the_path_and_the_value(cycle_txns):
    signals = detect_circular_flows(aggregate(build_graph(cycle_txns)))

    evidence = signals[0].evidence
    assert "A" in evidence and "B" in evidence and "C" in evidence
    assert "900" in evidence or "2,850" in evidence or "2850" in evidence


def test_a_star_topology_is_not_reported_as_circular(star_txns):
    """The legitimate hub trap: high degree without a loop must not fire this detector."""
    signals = detect_circular_flows(aggregate(build_graph(star_txns)))

    assert signals == []


def test_a_chain_without_a_return_edge_is_not_circular():
    txns = make_txns([("A", "B", 10, 0), ("B", "C", 10, 5), ("C", "D", 10, 9)])

    assert detect_circular_flows(aggregate(build_graph(txns))) == []


def test_velocity_burst_fires_above_the_threshold_and_not_below():
    burst = make_txns([("V", f"R{i}", 10, i) for i in range(12)])
    calm = make_txns([("C", f"R{i}", 10, i * 600) for i in range(12)])

    burst_signals = detect_velocity_bursts(compute_metrics(burst, build_graph(burst)), threshold=10)
    calm_signals = detect_velocity_bursts(compute_metrics(calm, build_graph(calm)), threshold=10)

    assert "V" in {s.account for s in burst_signals}
    assert "C" not in {s.account for s in calm_signals}


def test_fan_in_fan_out_flags_a_collector_not_an_ordinary_account():
    rows = [(f"F{i}", "COLLECTOR", 100, i * 5) for i in range(10)]
    rows.append(("COLLECTOR", "OUT", 950, 100))
    txns = make_txns(rows)

    signals = detect_fan_in_fan_out(compute_metrics(txns, build_graph(txns)))

    assert "COLLECTOR" in {s.account for s in signals}
    assert "F0" not in {s.account for s in signals}


def test_shared_identifiers_links_accounts_on_the_same_device():
    txns = make_txns([("A", "X", 10, 0), ("B", "Y", 10, 5)])
    txns.loc[:, "device_id"] = "dev_shared"

    signals = detect_shared_identifiers(txns)

    assert {"A", "B"} <= {s.account for s in signals}
    assert "dev_shared" in signals[0].evidence


def test_shared_identifiers_is_silent_when_every_account_has_its_own_device():
    txns = make_txns([("A", "X", 10, 0), ("B", "Y", 10, 5)])

    assert detect_shared_identifiers(txns) == []
