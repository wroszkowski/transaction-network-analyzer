"""The trust boundary: the one place untrusted data enters the pipeline.

Everything else in this package works on a ledger that has already been checked. `graph` indexes
edge attributes by name, `metrics` groups on sender and receiver, `detectors` compare amounts and
timestamps — none of them ask whether the column they need exists or whether an amount is a number,
because by the time they run the answer is yes. That assumption is only safe if it is established
exactly once, at the edge, and this module is that edge.

The input CSV comes from a fraud analyst's own tooling, so it is untrusted in the ordinary sense: it
is not hostile, but nothing guarantees it has the columns, types or keys this tool needs. Validating
it here turns a `KeyError` raised five modules deep into a message that names what is wrong with the
file, which is the difference between a report an analyst can act on and a traceback they cannot.

Two rules make the diagnosis useful:

* **Report every problem at once.** A file with three faults should take one round trip to fix, not
  three. `validate_ledger` accumulates problems and raises a single `LedgerValidationError` naming
  all of them with the offending row counts.
* **Repair what can be repaired, refuse what cannot.** A timestamp column of strings is coerced; a
  timestamp column that will not parse is an error. Optional columns that only power the enrichment
  signals are filled with a neutral default and logged, because their absence degrades the analysis
  rather than invalidating it.
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)

#: Columns the pipeline cannot run without, mapped to the description an analyst needs to fix them.
REQUIRED_COLUMNS: dict[str, str] = {
    "transaction_id": "unique identifier for the transaction",
    "timestamp": "when the transaction happened, as a date/time",
    "sender_account": "account the money left",
    "receiver_account": "account the money arrived at",
    "amount": "transaction value, numeric and not negative",
    "currency": "currency code of the amount, analysed per currency with no FX conversion",
}

#: Columns that only power the enrichment signals. Absent, the analysis is thinner, not invalid:
#: `shared_identifiers` simply has nothing to group on. They are filled with a neutral placeholder
#: so downstream code can index them unconditionally.
OPTIONAL_COLUMNS: dict[str, str] = {
    "device_id": "device fingerprint the payment was made from",
    "ip_address": "IP address the payment was made from",
    "payment_method": "how the payment was funded",
}

#: The value optional columns are filled with. Distinct per row would invent links; a single shared
#: value would invent a shared device across the whole ledger, so the placeholder is unique per
#: transaction and therefore never groups two accounts together.
MISSING_IDENTIFIER = "unknown"


class LedgerValidationError(ValueError):
    """The input ledger cannot be analysed, with every reason found stated in the message."""


def _format(problems: list[str]) -> str:
    listed = "\n".join(f"  - {problem}" for problem in problems)
    return f"the transaction ledger cannot be analysed ({len(problems)} problem(s) found):\n{listed}"


def _sample(values: pd.Series, limit: int = 3) -> str:
    shown = [str(value) for value in values.head(limit)]
    suffix = ", ..." if len(values) > limit else ""
    return ", ".join(shown) + suffix


def validate_ledger(transactions: pd.DataFrame) -> pd.DataFrame:
    """Check a ledger at the trust boundary and return the frame the pipeline may assume.

    The returned frame is a copy: `timestamp` is datetime-typed, `amount` is numeric, and any absent
    optional column has been filled. The input is never modified in place.

    Raises:
        LedgerValidationError: naming every problem found, not merely the first.
    """
    problems: list[str] = []
    ledger = transactions.copy()

    missing = [column for column in REQUIRED_COLUMNS if column not in ledger.columns]
    for column in missing:
        problems.append(f"required column '{column}' is missing ({REQUIRED_COLUMNS[column]})")

    if ledger.empty:
        problems.append("the ledger has no rows; there is nothing to analyse")

    if "timestamp" not in missing and not ledger.empty:
        column = ledger["timestamp"]
        if not pd.api.types.is_datetime64_any_dtype(column):
            coerced = pd.to_datetime(column, errors="coerce", format="mixed")
            unparsed = coerced.isna() & column.notna()
            if unparsed.any():
                problems.append(
                    f"{int(unparsed.sum())} row(s) have a 'timestamp' that is not a date/time "
                    f"(for example: {_sample(column[unparsed])})"
                )
            ledger["timestamp"] = coerced
        if ledger["timestamp"].isna().any():
            problems.append(f"{int(ledger['timestamp'].isna().sum())} row(s) have an empty 'timestamp'")

    if "amount" not in missing and not ledger.empty:
        column = ledger["amount"]
        numeric = pd.to_numeric(column, errors="coerce")
        unparsed = numeric.isna() & column.notna()
        if unparsed.any():
            problems.append(
                f"{int(unparsed.sum())} row(s) have an 'amount' that is not a number "
                f"(for example: {_sample(column[unparsed])})"
            )
        empty = column.isna()
        if empty.any():
            problems.append(f"{int(empty.sum())} row(s) have an empty 'amount'")
        negative = numeric < 0
        if negative.any():
            problems.append(
                f"{int(negative.sum())} row(s) have a negative 'amount'; a payment's value is its "
                "size, and direction is carried by sender and receiver"
            )
        ledger["amount"] = numeric

    for column_name in ("sender_account", "receiver_account"):
        if column_name in missing or ledger.empty:
            continue
        null = ledger[column_name].isna() | (ledger[column_name].astype(str).str.strip() == "")
        if null.any():
            problems.append(f"{int(null.sum())} row(s) have an empty '{column_name}'; every payment needs both ends")

    if "transaction_id" not in missing and not ledger.empty:
        duplicated = ledger["transaction_id"][ledger["transaction_id"].duplicated()]
        if not duplicated.empty:
            unique_ids = duplicated.drop_duplicates()
            problems.append(
                f"'transaction_id' is not unique: {len(duplicated)} repeated row(s) across "
                f"{len(unique_ids)} id(s) (for example: {_sample(unique_ids)})"
            )

    if problems:
        raise LedgerValidationError(_format(problems))

    absent = [column for column in OPTIONAL_COLUMNS if column not in ledger.columns]
    for column_name in absent:
        ledger[column_name] = [f"{MISSING_IDENTIFIER}-{index}" for index in range(len(ledger))]
    if absent:
        logger.info(
            "optional column(s) absent, enrichment signals will not use them: %s",
            ", ".join(absent),
        )

    logger.info("ledger validated: %d transactions, %d columns", len(ledger), len(ledger.columns))
    return ledger
