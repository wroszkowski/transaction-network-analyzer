"""Command line surface.

Three commands, matching the three things a fraud team actually does: produce a dataset, analyse a
dataset, and do both at once for a demo.

    tna demo                                  generate the sample data, analyse it, write the report
    tna generate --seed 42 --out data/        write transactions.csv, accounts.csv, ground_truth.json
    tna analyze --input <csv> --out public/   analyse any ledger with the same columns
"""

import argparse
import json
from pathlib import Path

import pandas as pd

from .analyze import analyze
from .generate import generate
from .report import render_report

DEFAULT_DATA_DIR = Path("data")
DEFAULT_OUT_DIR = Path("public")


def write_dataset(out_dir: Path, seed: int) -> None:
    """Generate the demo dataset and persist it next to its ground-truth labels."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset = generate(seed=seed)
    dataset.transactions.to_csv(out_dir / "transactions.csv", index=False)
    dataset.accounts.to_csv(out_dir / "accounts.csv", index=False)
    (out_dir / "ground_truth.json").write_text(
        json.dumps({"labels": dataset.ground_truth, "legit_hub": dataset.legit_hub}, indent=2)
    )
    print(f"wrote {len(dataset.transactions)} transactions and {len(dataset.accounts)} accounts to {out_dir}")


def run_analysis(input_csv: Path, accounts_csv: Path | None, truth_json: Path | None, out_dir: Path) -> None:
    """Analyse a ledger and render the investigator report."""
    transactions = pd.read_csv(input_csv, parse_dates=["timestamp"])
    accounts = (
        pd.read_csv(accounts_csv, parse_dates=["created_at"])
        if accounts_csv and accounts_csv.exists()
        else pd.DataFrame(columns=["account_id", "created_at", "country", "currency", "kyc_level"])
    )
    ground_truth = json.loads(truth_json.read_text())["labels"] if truth_json and truth_json.exists() else None

    result = analyze(transactions, accounts, ground_truth)
    out_dir.mkdir(parents=True, exist_ok=True)
    render_report(result, out_dir)

    print(f"analysed {len(transactions)} transactions across {len(result.metrics)} accounts")
    print(f"flagged {len(result.flagged)} accounts for investigation")
    if result.evaluation:
        evaluation = result.evaluation
        print(
            f"against ground truth: precision {evaluation['precision']:.0%}, "
            f"recall {evaluation['recall']:.0%}, F1 {evaluation['f1']:.2f}"
        )
    print(f"report written to {out_dir / 'index.html'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tna", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    generate_command = commands.add_parser("generate", help="write a synthetic labelled dataset")
    generate_command.add_argument("--seed", type=int, default=42)
    generate_command.add_argument("--out", type=Path, default=DEFAULT_DATA_DIR)

    analyze_command = commands.add_parser("analyze", help="analyse a transaction ledger")
    analyze_command.add_argument("--input", type=Path, default=DEFAULT_DATA_DIR / "transactions.csv")
    analyze_command.add_argument("--accounts", type=Path, default=DEFAULT_DATA_DIR / "accounts.csv")
    analyze_command.add_argument("--truth", type=Path, default=DEFAULT_DATA_DIR / "ground_truth.json")
    analyze_command.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)

    demo_command = commands.add_parser("demo", help="generate, analyse and report in one step")
    demo_command.add_argument("--seed", type=int, default=42)

    args = parser.parse_args(argv)
    if args.command == "generate":
        write_dataset(args.out, args.seed)
    elif args.command == "analyze":
        run_analysis(args.input, args.accounts, args.truth, args.out)
    else:
        write_dataset(DEFAULT_DATA_DIR, args.seed)
        run_analysis(
            DEFAULT_DATA_DIR / "transactions.csv",
            DEFAULT_DATA_DIR / "accounts.csv",
            DEFAULT_DATA_DIR / "ground_truth.json",
            DEFAULT_OUT_DIR,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
