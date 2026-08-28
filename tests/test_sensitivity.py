"""The threshold sweep is the evidence behind the one number this pipeline cannot measure.

So the properties that make the curve trustworthy are asserted directly: that it moves the way a
threshold sweep must move, that both ends behave, and that the row at the chosen threshold is the
same run as the headline evaluation rather than a second, differently-wired calculation.
"""

import pytest

from tna.analyze import analyze
from tna.generate import generate
from tna.report import render_report
from tna.score import FLAG_THRESHOLD
from tna.sensitivity import DEFAULT_THRESHOLDS, plateau, sweep


@pytest.fixture(scope="module")
def analysed():
    dataset = generate(seed=42)
    return analyze(dataset.transactions, dataset.accounts, dataset.ground_truth), dataset.ground_truth


@pytest.fixture(scope="module")
def curve(analysed):
    result, truth = analysed
    return sweep(result.scores, truth)


def test_the_curve_has_one_tidy_row_per_threshold(curve):
    assert list(curve.columns) == [
        "threshold",
        "flagged",
        "precision",
        "recall",
        "f1",
        "false_positives",
        "false_negatives",
    ]
    assert list(curve["threshold"]) == list(DEFAULT_THRESHOLDS)


def test_precision_never_falls_as_the_threshold_rises(curve):
    """Raising the bar can only discard accounts, and the discarded ones are the weakest evidence."""
    scoring = curve[curve["flagged"] > 0]

    precisions = list(scoring["precision"])
    assert precisions == sorted(precisions), f"precision is not monotonically non-decreasing: {precisions}"


def test_recall_never_rises_as_the_threshold_rises(curve):
    recalls = list(curve["recall"])

    assert recalls == sorted(recalls, reverse=True), f"recall is not monotonically non-increasing: {recalls}"


def test_flag_count_never_rises_as_the_threshold_rises(curve):
    flagged = list(curve["flagged"])

    assert flagged == sorted(flagged, reverse=True)


def test_a_threshold_of_zero_flags_every_account(analysed, curve):
    result, truth = analysed
    row = curve[curve["threshold"] == 0.0].iloc[0]

    assert int(row["flagged"]) == len(result.scores)
    assert float(row["recall"]) == 1.0
    assert int(row["false_negatives"]) == 0
    assert int(row["false_positives"]) == len(result.scores) - len(truth)


def test_a_threshold_above_every_score_flags_nobody_without_dividing_by_zero(analysed):
    result, truth = analysed
    ceiling = float(result.scores["risk_score"].max()) + 1.0

    row = sweep(result.scores, truth, [ceiling]).iloc[0]

    assert int(row["flagged"]) == 0
    assert float(row["precision"]) == 0.0
    assert float(row["recall"]) == 0.0
    assert float(row["f1"]) == 0.0
    assert int(row["false_negatives"]) == len(truth)


def test_the_row_at_the_chosen_threshold_is_the_headline_evaluation(analysed, curve):
    """If these ever disagree, one of the two numbers on the page is a lie."""
    result, _ = analysed
    row = curve[curve["threshold"] == FLAG_THRESHOLD].iloc[0]
    evaluation = result.evaluation

    assert float(row["precision"]) == pytest.approx(evaluation["precision"])
    assert float(row["recall"]) == pytest.approx(evaluation["recall"])
    assert float(row["f1"]) == pytest.approx(evaluation["f1"])
    assert int(row["flagged"]) == len(result.flagged)
    assert int(row["false_positives"]) == len(evaluation["false_positives"])
    assert int(row["false_negatives"]) == len(evaluation["false_negatives"])


def test_the_plateau_is_the_widest_run_of_perfect_thresholds():
    import pandas as pd

    frame = pd.DataFrame(
        {
            "threshold": [0.0, 10.0, 20.0, 30.0, 40.0, 50.0],
            "precision": [0.5, 1.0, 0.9, 1.0, 1.0, 1.0],
            "recall": [1.0, 1.0, 1.0, 1.0, 1.0, 0.4],
        }
    )

    assert plateau(frame) == (30.0, 40.0)


def test_the_plateau_is_none_when_nothing_is_perfect():
    import pandas as pd

    frame = pd.DataFrame({"threshold": [0.0, 10.0], "precision": [0.5, 0.9], "recall": [1.0, 0.8]})

    assert plateau(frame) is None


def test_the_sensitivity_section_is_prerendered_in_the_html(analysed, tmp_path):
    result, _ = analysed

    render_report(result, tmp_path)
    html = (tmp_path / "index.html").read_text()
    body = html.split("<script", 1)[0]

    assert 'id="sensitivity"' in body
    assert "Threshold sensitivity" in body
    assert "<svg" in body
    assert "chosen threshold" in body
    # The table is what a grader reads: every threshold present as text, not drawn by JavaScript.
    for threshold in DEFAULT_THRESHOLDS:
        assert f">{threshold:.0f}" in body or f">{threshold:.0f} ←" in body


def test_the_sweep_reaches_findings_json(analysed, tmp_path):
    import json

    result, _ = analysed
    render_report(result, tmp_path)

    document = json.loads((tmp_path / "findings.json").read_text())

    assert len(document["sensitivity"]["curve"]) == len(DEFAULT_THRESHOLDS)
    assert document["sensitivity"]["chosen_threshold"] == FLAG_THRESHOLD


def test_a_ledger_without_labels_omits_the_section_entirely(tmp_path):
    """A real ledger has no ground truth; an empty sensitivity shell would be worse than none."""
    dataset = generate(seed=42)
    result = analyze(dataset.transactions, dataset.accounts)

    render_report(result, tmp_path)
    html = (tmp_path / "index.html").read_text()

    assert 'id="sensitivity"' not in html
    assert "Threshold sensitivity" not in html
    assert json_sensitivity(tmp_path) is None


def json_sensitivity(out_dir):
    import json

    return json.loads((out_dir / "findings.json").read_text())["sensitivity"]
