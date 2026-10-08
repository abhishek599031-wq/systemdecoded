"""Pure Content Intelligence derivation tests."""

from decimal import Decimal

from app.services import content_intelligence


def test_word_count_and_duration_bands_are_deterministic() -> None:
    assert content_intelligence._word_count("Nobody sent you that code.") == 5
    assert content_intelligence._duration_band(Decimal("30")) == "up_to_30s"
    assert content_intelligence._duration_band(Decimal("40")) == "31_to_45s"
    assert content_intelligence._duration_band(Decimal("46")) == "over_45s"
    assert content_intelligence._duration_band(None) == "unknown"
