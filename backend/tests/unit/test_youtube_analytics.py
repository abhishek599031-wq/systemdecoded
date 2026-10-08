"""Pure provider parsing, sync planning and window derivation tests."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.integrations.youtube import analytics_api
from app.integrations.youtube.errors import (
    GoogleAuthError,
    RateLimitedError,
    TransientGoogleError,
)
from app.models.analytics import YouTubeVideoDailyMetric
from app.models.content import PublishedVideo
from app.services import youtube_analytics


def test_report_maps_headers_and_keeps_missing_metrics_null() -> None:
    rows = analytics_api.parse_report(
        {
            "columnHeaders": [
                {"name": "day"},
                {"name": "engagedViews"},
                {"name": "views"},
                {"name": "averageViewDuration"},
            ],
            "rows": [["2026-09-08", 7, 11, 12.5]],
        }
    )
    assert rows[0].metric_date == date(2026, 9, 8)
    assert rows[0].views == 11
    assert rows[0].engaged_views == 7
    assert rows[0].likes is None
    assert rows[0].average_view_duration == Decimal("12.5")
    assert "likes" not in rows[0].returned_metrics


def test_aggregate_report_maps_provider_window_without_day() -> None:
    metric = analytics_api.parse_aggregate_report(
        {
            "columnHeaders": [
                {"name": "engagedViews"},
                {"name": "estimatedMinutesWatched"},
                {"name": "averageViewDuration"},
            ],
            "rows": [[10, 2, 16]],
        }
    )
    assert metric is not None
    assert metric.engaged_views == 10
    assert metric.estimated_minutes_watched == Decimal("2")
    assert metric.average_view_duration == Decimal("16")
    assert metric.views is None


def test_empty_report_without_rows_is_valid_no_data_yet() -> None:
    assert analytics_api.parse_report({"columnHeaders": [], "kind": "youtubeAnalytics"}) == []


def test_malformed_report_day_is_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid YouTube Analytics day"):
        analytics_api.parse_report({"columnHeaders": [{"name": "day"}], "rows": [["not-a-date"]]})


async def test_reports_query_uses_owned_channel_video_day_and_all_metrics(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        seen["authorization"] = request.headers["Authorization"]
        return httpx.Response(
            200,
            json={"columnHeaders": [{"name": "day"}, {"name": "views"}], "rows": []},
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(analytics_api.httpx, "AsyncClient", patched)
    await analytics_api.fetch_daily_video_metrics(
        "token", "Q-CJWCG90qI", date(2026, 9, 8), date(2026, 9, 20)
    )
    assert seen["ids"] == "channel==MINE"
    assert seen["filters"] == "video==Q-CJWCG90qI"
    assert seen["dimensions"] == "day"
    assert seen["sort"] == "day"
    assert seen["metrics"] == ",".join(analytics_api.METRICS)
    assert seen["authorization"] == "Bearer token"


@pytest.mark.parametrize(
    ("end_date", "window"),
    [
        (date(2026, 9, 8), "D1"),
        (date(2026, 9, 10), "D3"),
        (date(2026, 9, 14), "D7"),
    ],
)
async def test_window_query_has_no_day_dimension(monkeypatch, end_date, window) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "columnHeaders": [{"name": "views"}, {"name": "engagedViews"}],
                "rows": [[12, 6]],
            },
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(analytics_api.httpx, "AsyncClient", patched)
    metric = await analytics_api.fetch_video_window_metrics(
        "token", "Q-CJWCG90qI", date(2026, 9, 8), end_date
    )
    assert metric is not None
    assert "dimensions" not in seen, window
    assert "sort" not in seen
    assert seen["startDate"] == "2026-09-08"
    assert seen["endDate"] == end_date.isoformat()


@pytest.mark.parametrize(
    ("status", "reason", "error_type"),
    [
        (401, "authError", GoogleAuthError),
        (403, "forbidden", GoogleAuthError),
        (429, "rateLimitExceeded", RateLimitedError),
        (503, "backendError", RateLimitedError),
    ],
)
async def test_provider_error_classification(monkeypatch, status, reason, error_type) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            status,
            json={"error": {"message": "provider failure", "errors": [{"reason": reason}]}},
        )
    )
    real_client = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(analytics_api.httpx, "AsyncClient", patched)
    with pytest.raises(error_type):
        await analytics_api.fetch_daily_video_metrics(
            "token", "Q-CJWCG90qI", date(2026, 9, 8), date(2026, 9, 9)
        )


async def test_network_failure_is_retryable(monkeypatch) -> None:
    transport = httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("offline"))
    )
    real_client = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(analytics_api.httpx, "AsyncClient", patched)
    with pytest.raises(TransientGoogleError):
        await analytics_api.fetch_daily_video_metrics(
            "token", "Q-CJWCG90qI", date(2026, 9, 8), date(2026, 9, 9)
        )


def test_initial_sync_backfills_full_range() -> None:
    assert youtube_analytics.plan_sync_ranges(date(2026, 9, 8), date(2026, 9, 20), []) == [
        (date(2026, 9, 8), date(2026, 9, 20))
    ]


def test_incremental_sync_refreshes_fourteen_days() -> None:
    end = date(2026, 10, 20)
    known = [date(2026, 9, 8) + timedelta(days=i) for i in range(43)]
    assert youtube_analytics.plan_sync_ranges(date(2026, 9, 8), end, known) == [
        (date(2026, 10, 7), end)
    ]


def test_historical_gap_extends_refresh_back_to_first_missing_day() -> None:
    publication = date(2026, 9, 1)
    end = date(2026, 10, 1)
    known = [publication + timedelta(days=i) for i in range(31) if i != 4]
    assert youtube_analytics.plan_sync_ranges(publication, end, known) == [(date(2026, 9, 5), end)]


def _metric(day: date, **values) -> YouTubeVideoDailyMetric:
    return YouTubeVideoDailyMetric(
        published_video_id=uuid.uuid4(),
        metric_date=day,
        synced_at=datetime.now(UTC),
        **values,
    )


def test_d1_and_d7_are_derived_from_publication_reporting_day() -> None:
    start = date(2026, 9, 8)
    rows = [_metric(start + timedelta(days=i), views=i + 1, engaged_views=i + 1) for i in range(7)]
    windows = {
        item["window"]: item
        for item in youtube_analytics.derive_windows(rows, publication_date=start)
    }
    assert windows["D1"]["views"] == 1
    assert windows["D1"]["complete"] is True
    assert windows["D7"]["views"] == 28
    assert windows["D7"]["days_present"] == 7
    assert windows["D7"]["complete"] is True


def test_late_first_row_does_not_shift_diagnostic_window_anchor() -> None:
    publication = date(2026, 9, 8)
    rows = [_metric(date(2026, 9, 9), views=4, engaged_views=3)]
    windows = {
        item["window"]: item
        for item in youtube_analytics.derive_windows(rows, publication_date=publication)
    }
    assert windows["D1"]["start_date"] == publication
    assert windows["D1"]["views"] is None
    assert windows["D1"]["complete"] is False


def test_aggregate_averages_are_weighted_by_engaged_views() -> None:
    rows = [
        _metric(
            date(2026, 9, 8),
            engaged_views=1,
            average_view_duration=Decimal("10"),
            average_view_percentage=Decimal("20"),
        ),
        _metric(
            date(2026, 9, 9),
            engaged_views=3,
            average_view_duration=Decimal("30"),
            average_view_percentage=Decimal("60"),
        ),
    ]
    result = youtube_analytics.aggregate_rows(rows)
    assert result["average_view_duration"] == Decimal("25")
    assert result["average_view_percentage"] == Decimal("50")


def test_weighted_average_is_unavailable_without_all_denominators() -> None:
    rows = [
        _metric(
            date(2026, 9, 8),
            engaged_views=None,
            average_view_duration=Decimal("10"),
        )
    ]
    assert youtube_analytics.aggregate_rows(rows)["average_view_duration"] is None


def test_additive_aggregate_is_unavailable_when_any_day_is_missing_the_metric() -> None:
    rows = [
        _metric(date(2026, 9, 8), views=10),
        _metric(date(2026, 9, 9), views=None),
    ]
    assert youtube_analytics.aggregate_rows(rows)["views"] is None


def test_publication_request_boundary_uses_youtube_pacific_not_machine_timezone() -> None:
    video = PublishedVideo(
        project_id=uuid.uuid4(),
        youtube_video_id="Q-CJWCG90qI",
        published_at=datetime(2026, 9, 8, 6, 30, tzinfo=UTC),
        reconciliation_method="manual_confirmation_verified",
    )
    # 06:30 UTC is still the prior Pacific reporting date during DST.
    assert youtube_analytics.publication_reporting_date(video) == date(2026, 9, 7)
