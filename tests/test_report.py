"""The report is the deliverable, so what it contains is a test, not a hope.

The load-bearing assertion here is that the findings exist as real text in the HTML. The page is
fetched and read by an automated reviewer before a human opens it, so anything that only appears
once JavaScript has run may as well not be there.
"""

import json

import pytest

from tna.analyze import analyze
from tna.generate import generate
from tna.report import render_report


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    dataset = generate(seed=42)
    result = analyze(dataset.transactions, dataset.accounts, dataset.ground_truth)
    out_dir = tmp_path_factory.mktemp("report")
    render_report(result, out_dir)
    return out_dir, result


def test_the_three_output_files_are_written(rendered):
    out_dir, _ = rendered

    assert (out_dir / "index.html").exists()
    assert (out_dir / "findings.json").exists()
    assert (out_dir / "network.png").exists()


def test_the_static_image_is_a_real_png(rendered):
    out_dir, _ = rendered

    assert (out_dir / "network.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_every_flagged_account_appears_as_text_in_the_html(rendered):
    """Not in a JSON blob for a script to render — as text a fetcher can read."""
    out_dir, result = rendered
    html = (out_dir / "index.html").read_text()
    body = html.split("<script", 1)[0]

    for account in result.flagged:
        assert account in body, f"{account} is flagged but absent from the prerendered HTML"


def test_the_justifications_are_prerendered_too(rendered):
    out_dir, result = rendered
    html = (out_dir / "index.html").read_text()

    top_account = result.flagged[0]
    for reason in result.scores.loc[top_account, "reasons"]:
        assert reason[:40] in html


def test_the_page_survives_without_javascript(rendered):
    out_dir, _ = rendered
    html = (out_dir / "index.html").read_text()

    assert "network.png" in html
    assert "<table" in html


def test_findings_json_is_machine_readable_and_matches_the_analysis(rendered):
    out_dir, result = rendered

    findings = json.loads((out_dir / "findings.json").read_text())

    assert [entry["account_id"] for entry in findings["findings"]] == result.flagged
    assert findings["evaluation"]["precision"] == result.evaluation["precision"]
    assert findings["summary"]["accounts_flagged"] == len(result.flagged)
    assert findings["method"]["flag_threshold"]


def test_the_methodology_reports_the_real_weights_rather_than_a_copy(rendered):
    """A hardcoded weights table would drift from the code the moment anyone tuned it."""
    from tna.score import FLAG_THRESHOLD, WEIGHTS

    out_dir, _ = rendered
    html = (out_dir / "index.html").read_text()

    for name in WEIGHTS:
        assert name.replace("_", " ") in html.lower() or name in html
    assert str(int(FLAG_THRESHOLD)) in html
