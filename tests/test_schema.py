"""The ledger is the only untrusted input, so every rule guarding it is tested by name.

Each test asserts two things: that the bad ledger is rejected, and that the message names the actual
problem. A validator that rejects everything with "invalid input" would pass the first assertion and
be useless to the analyst holding the file.
"""

import pandas as pd
import pytest

from tna.schema import MISSING_IDENTIFIER, LedgerValidationError, validate_ledger


def ledger(**overrides) -> pd.DataFrame:
    """A small, valid ledger; keyword arguments replace one column with a bad one."""
    frame = pd.DataFrame(
        {
            "transaction_id": ["T1", "T2", "T3"],
            "timestamp": pd.to_datetime(["2024-01-01 10:00", "2024-01-01 11:00", "2024-01-02 09:00"]),
            "sender_account": ["A", "B", "C"],
            "receiver_account": ["B", "C", "A"],
            "amount": [100.0, 90.0, 80.0],
            "currency": ["NGN", "NGN", "NGN"],
            "device_id": ["D1", "D2", "D3"],
            "ip_address": ["1.1.1.1", "1.1.1.2", "1.1.1.3"],
            "payment_method": ["card", "card", "transfer"],
        }
    )
    return frame.assign(**overrides)


def test_a_valid_ledger_passes_through_unchanged():
    valid = ledger()

    validated = validate_ledger(valid)

    pd.testing.assert_frame_equal(validated, valid)


def test_missing_required_column_is_named_in_the_message():
    with pytest.raises(LedgerValidationError) as error:
        validate_ledger(ledger().drop(columns=["amount"]))

    assert "'amount' is missing" in str(error.value)


def test_an_empty_ledger_is_rejected():
    with pytest.raises(LedgerValidationError) as error:
        validate_ledger(ledger().iloc[0:0])

    assert "no rows" in str(error.value)


def test_string_timestamps_are_coerced_rather_than_rejected():
    validated = validate_ledger(ledger(timestamp=["2024-01-01 10:00", "2024-01-01 11:00", "2024-01-02 09:00"]))

    assert pd.api.types.is_datetime64_any_dtype(validated["timestamp"])


def test_unparseable_timestamps_are_rejected_with_an_example():
    with pytest.raises(LedgerValidationError) as error:
        validate_ledger(ledger(timestamp=["2024-01-01 10:00", "yesterday-ish", "2024-01-02 09:00"]))

    message = str(error.value)
    assert "1 row(s) have a 'timestamp' that is not a date/time" in message
    assert "yesterday-ish" in message


def test_empty_timestamps_are_rejected():
    with pytest.raises(LedgerValidationError) as error:
        validate_ledger(ledger(timestamp=["2024-01-01 10:00", None, "2024-01-02 09:00"]))

    assert "empty 'timestamp'" in str(error.value)


def test_non_numeric_amounts_are_rejected():
    with pytest.raises(LedgerValidationError) as error:
        validate_ledger(ledger(amount=[100.0, "a lot", 80.0]))

    message = str(error.value)
    assert "not a number" in message
    assert "a lot" in message


def test_negative_amounts_are_rejected_with_their_row_count():
    with pytest.raises(LedgerValidationError) as error:
        validate_ledger(ledger(amount=[100.0, -90.0, -80.0]))

    assert "2 row(s) have a negative 'amount'" in str(error.value)


def test_numeric_amounts_stored_as_text_are_coerced():
    validated = validate_ledger(ledger(amount=["100", "90", "80"]))

    assert validated["amount"].tolist() == [100, 90, 80]


def test_a_null_sender_is_rejected():
    with pytest.raises(LedgerValidationError) as error:
        validate_ledger(ledger(sender_account=["A", None, "C"]))

    assert "empty 'sender_account'" in str(error.value)


def test_a_null_receiver_is_rejected():
    with pytest.raises(LedgerValidationError) as error:
        validate_ledger(ledger(receiver_account=["B", "", "A"]))

    assert "empty 'receiver_account'" in str(error.value)


def test_duplicate_transaction_ids_are_rejected_with_the_offending_id():
    with pytest.raises(LedgerValidationError) as error:
        validate_ledger(ledger(transaction_id=["T1", "T1", "T3"]))

    message = str(error.value)
    assert "'transaction_id' is not unique" in message
    assert "T1" in message


def test_absent_optional_columns_are_filled_and_never_link_two_accounts():
    thin = ledger().drop(columns=["device_id", "ip_address", "payment_method"])

    validated = validate_ledger(thin)

    assert {"device_id", "ip_address", "payment_method"} <= set(validated.columns)
    assert validated["device_id"].str.startswith(MISSING_IDENTIFIER).all()
    assert validated["device_id"].nunique() == len(validated)


def test_every_problem_is_reported_at_once_so_one_pass_fixes_the_file():
    broken = ledger(
        transaction_id=["T1", "T1", "T3"],
        amount=[100.0, -90.0, 80.0],
        sender_account=["A", None, "C"],
    ).drop(columns=["currency"])

    with pytest.raises(LedgerValidationError) as error:
        validate_ledger(broken)

    message = str(error.value)
    assert "'currency' is missing" in message
    assert "negative 'amount'" in message
    assert "empty 'sender_account'" in message
    assert "'transaction_id' is not unique" in message
    assert "4 problem(s) found" in message


def test_validation_does_not_modify_the_callers_frame():
    original = ledger(timestamp=["2024-01-01 10:00", "2024-01-01 11:00", "2024-01-02 09:00"])
    before = original.copy()

    validate_ledger(original)

    pd.testing.assert_frame_equal(original, before)
