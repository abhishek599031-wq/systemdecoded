"""Retry classification and UUIDv7 ordering.

The retry classification tests matter more than they look: getting this wrong
is what causes duplicate YouTube uploads later (ARCH §9.2, §13.4).
"""

from __future__ import annotations

import time
import uuid

import pytest

from app.core.errors import (
    DEFAULT_RETRYABLE,
    AppError,
    NotFoundError,
    RetryableError,
    TerminalError,
    is_retryable,
)
from app.core.ids import timestamp_ms_from_uuid7, uuid7


# ---------------------------------------------------------------- retry ---
def test_retryable_error_is_retryable() -> None:
    assert is_retryable(RetryableError("transient"))


def test_terminal_error_is_never_retryable() -> None:
    assert not is_retryable(TerminalError("permanent"))


def test_terminal_wins_even_when_explicitly_listed() -> None:
    """TerminalError must override the retry_on set, not merely be absent from it."""
    assert not is_retryable(TerminalError("boom"), (TerminalError, RetryableError))


def test_unknown_exception_is_terminal_by_default() -> None:
    # The conservative default: an unrecognised failure does not get retried.
    assert not is_retryable(ValueError("unexpected"))


def test_network_style_errors_are_retryable_by_default() -> None:
    assert is_retryable(ConnectionError("reset"))
    assert is_retryable(TimeoutError("slow"))
    assert is_retryable(OSError("io"))


def test_custom_retry_set_is_honoured() -> None:
    assert is_retryable(ValueError("x"), (ValueError,))
    assert not is_retryable(KeyError("x"), (ValueError,))


def test_default_retryable_set_is_a_tuple_of_types() -> None:
    assert all(isinstance(t, type) for t in DEFAULT_RETRYABLE)


# --------------------------------------------------------------- AppError ---
def test_app_error_payload_shape() -> None:
    err = NotFoundError("Job 123 not found")
    payload = err.to_payload(request_id="req-1")
    assert payload == {
        "error": {"code": "not_found", "message": "Job 123 not found", "request_id": "req-1"}
    }
    assert err.status_code == 404


def test_app_error_detail_is_included_when_present() -> None:
    err = AppError("bad", code="custom", status_code=400, detail={"field": "x"})
    assert err.to_payload()["error"]["detail"] == {"field": "x"}


def test_app_error_omits_detail_when_absent() -> None:
    assert "detail" not in AppError("bad").to_payload()["error"]


# ----------------------------------------------------------------- uuid7 ---
def test_uuid7_has_correct_version_and_variant() -> None:
    value = uuid7()
    assert value.version == 7
    assert (value.bytes[8] & 0xC0) == 0x80


def test_uuid7_values_are_unique() -> None:
    values = {uuid7() for _ in range(2000)}
    assert len(values) == 2000


def test_uuid7_sorts_by_creation_time_across_milliseconds() -> None:
    first = uuid7()
    time.sleep(0.005)
    second = uuid7()
    assert first < second
    assert str(first) < str(second)


def test_timestamp_roundtrip_is_close_to_now() -> None:
    before = int(time.time() * 1000)
    extracted = timestamp_ms_from_uuid7(uuid7())
    after = int(time.time() * 1000)
    assert before - 5 <= extracted <= after + 5


def test_is_a_real_uuid() -> None:
    assert isinstance(uuid7(), uuid.UUID)


@pytest.mark.parametrize("_", range(5))
def test_repeated_generation_stays_ordered(_: int) -> None:
    values = []
    for _i in range(3):
        values.append(uuid7())
        time.sleep(0.002)
    assert values == sorted(values)
