"""The CLI is how a fraud analyst supplies their own ledger, so it gets exercised end to end."""

import json

import pytest

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


def test_a_malformed_ledger_exits_non_zero_with_a_readable_message(tmp_path, capsys):
    """An analyst's bad CSV is their problem to fix, so they get a list, not a traceback."""
    bad_csv = tmp_path / "broken.csv"
    bad_csv.write_text(
        "transaction_id,timestamp,sender_account,amount,currency\nT1,2024-01-01 10:00,A,-5,NGN\nT1,not-a-date,,10,NGN\n"
    )

    exit_code = main(["analyze", "--input", str(bad_csv), "--out", str(tmp_path / "out")])

    assert exit_code != 0
    stderr = capsys.readouterr().err
    assert "Traceback" not in stderr
    assert "'receiver_account' is missing" in stderr
    assert "negative 'amount'" in stderr
    assert "'transaction_id' is not unique" in stderr
    assert "not a date/time" in stderr


def test_a_missing_input_file_is_reported_rather_than_raised(tmp_path, capsys):
    exit_code = main(["analyze", "--input", str(tmp_path / "absent.csv"), "--out", str(tmp_path / "out")])

    assert exit_code != 0
    assert "no such file" in capsys.readouterr().err


def test_verbose_narrates_the_pipeline_without_polluting_stdout(tmp_path, capsys, caplog):
    data_dir, out_dir = tmp_path / "data", tmp_path / "out"
    main(["generate", "--out", str(data_dir)])

    with caplog.at_level("INFO", logger="tna.analyze"):
        exit_code = main(
            [
                "analyze",
                "--verbose",
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
    assert any("graph built" in record.message for record in caplog.records)
    assert any("scoring complete" in record.message for record in caplog.records)
    assert "graph built" not in capsys.readouterr().out


@pytest.mark.parametrize("column", ["timestamp", "sender_account", "amount"])
def test_dropping_any_required_column_is_caught_at_the_boundary(tmp_path, capsys, column):
    data_dir = tmp_path / "data"
    main(["generate", "--out", str(data_dir)])
    ledger = data_dir / "transactions.csv"
    header, *rows = ledger.read_text().splitlines()
    index = header.split(",").index(column)
    trimmed = [
        ",".join(part for position, part in enumerate(line.split(",")) if position != index) for line in [header, *rows]
    ]
    ledger.write_text("\n".join(trimmed) + "\n")

    exit_code = main(
        ["analyze", "--input", str(ledger), "--truth", str(data_dir / "none.json"), "--out", str(tmp_path / "out")]
    )

    assert exit_code != 0
    assert f"'{column}' is missing" in capsys.readouterr().err


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
