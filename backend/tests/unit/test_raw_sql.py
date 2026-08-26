"""Guards on hand-written SQL.

`text()` scans an entire statement for `:name` tokens — including inside `--`
comments — so any stray colon silently becomes a required bind parameter and the
statement fails at execution time with "a value is required for bind parameter".
These tests are cheap and catch that class of bug at import time, without a
database.
"""

from __future__ import annotations

from sqlalchemy import text

from app.jobs.queue import _CLAIM_SQL, CLAIM_SQL_PARAMS


def test_claim_sql_binds_exactly_the_expected_parameters() -> None:
    assert set(_CLAIM_SQL._bindparams) == set(CLAIM_SQL_PARAMS)


def test_claim_sql_uses_skip_locked() -> None:
    """The entire concurrency guarantee rests on this clause."""
    sql = str(_CLAIM_SQL)
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "ORDER BY c.priority DESC" in sql


def test_claim_sql_avoids_double_colon_casts() -> None:
    """`:param::type` does not bind in text(); CAST(...) is the safe form."""
    assert "::" not in str(_CLAIM_SQL)


def test_claim_sql_contains_no_sql_comments() -> None:
    """Comments in a text() statement are a bind-parameter hazard."""
    assert "--" not in str(_CLAIM_SQL)


def test_claim_sql_spends_an_attempt_on_claim() -> None:
    """A dead worker must not be able to retry forever."""
    assert "attempt      = j.attempt + 1" in str(_CLAIM_SQL)


def test_the_hazard_this_guards_against_is_real() -> None:
    """Demonstrates why the tests above exist, so the reason is not lost."""
    hazardous = text("SELECT 1 -- see :param::type for details")
    assert hazardous._bindparams, "expected a phantom bind parameter from the comment"
