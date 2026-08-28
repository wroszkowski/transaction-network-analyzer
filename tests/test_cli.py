"""The CLI is how a fraud analyst supplies their own ledger, so it gets exercised end to end."""

import json

from tna.cli import main


def test_generate_writes_the_dataset_and_its_labels(tmp_path):
    assert main(["generate", "--seed", "7", "--out", str(tmp_path)]) == 0

    assert (tmp_path / "transactions.csv").exists()
    assert (tmp_path / "accounts.csv").exists()
    truth = json.loads((tmp_path / "ground_truth.json").read_text())
    assert truth["labels"]
    assert truth["legit_hub"]


def test_analyze_reads_a_ledger_from_disk_and_writes_a_report(tmp_path):
    data_dir, out_dir = tmp_path / "data", tmp_path / "out"
    main(["generate", "--out", str(data_dir)])

    exit_code = main(
        [
            "analyze",
            "--input",
            str(data_dir / "transactions.csv"),
            "--accounts",
            str(data_dir / "accounts.csv"),
            "--truth",
            str(data_dir / "ground_truth.json"),
            "--out",
            str(out_dir),
        ]
    )

    assert exit_code == 0
    assert (out_dir / "index.html").exists()
    assert (out_dir / "findings.json").exists()


def test_analyze_works_without_ground_truth_because_real_ledgers_have_none(tmp_path):
    data_dir, out_dir = tmp_path / "data", tmp_path / "out"
    main(["generate", "--out", str(data_dir)])

    exit_code = main(
        [
            "analyze",
            "--input",
            str(data_dir / "transactions.csv"),
            "--accounts",
            str(data_dir / "accounts.csv"),
            "--truth",
            str(data_dir / "missing.json"),
            "--out",
            str(out_dir),
        ]
    )

    assert exit_code == 0
    assert (out_dir / "index.html").exists()
