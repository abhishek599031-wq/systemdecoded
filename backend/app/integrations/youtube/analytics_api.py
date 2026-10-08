"""YouTube Analytics API v2 targeted-query client.

Daily Analytics rows are the performance source of truth. In particular,
``views`` and ``engagedViews`` remain separate: for Shorts, public views count
starts/replays while engaged views preserve the continued-playback signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.integrations.youtube.errors import TransientGoogleError, classify_api_error

API_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

METRICS = (
    "views",
    "engagedViews",
    "likes",
    "comments",
    "shares",
    "subscribersGained",
    "subscribersLost",
    "estimatedMinutesWatched",
    "averageViewDuration",
    "averageViewPercentage",
)


@dataclass(frozen=True, slots=True)
class DailyMetric:
    metric_date: date
    views: int | None = None
    engaged_views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    subscribers_gained: int | None = None
    subscribers_lost: int | None = None
    estimated_minutes_watched: Decimal | None = None
    average_view_duration: Decimal | None = None
    average_view_percentage: Decimal | None = None
    # None is reserved for hand-built rows in tests/internal callers. An empty
    # set is meaningful: the provider returned a day but no metric columns.
    returned_metrics: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class AggregateMetric:
    """One dimensionless provider aggregate for an exact date range."""

    views: int | None = None
    engaged_views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    subscribers_gained: int | None = None
    subscribers_lost: int | None = None
    estimated_minutes_watched: Decimal | None = None
    average_view_duration: Decimal | None = None
    average_view_percentage: Decimal | None = None
    returned_metrics: frozenset[str] | None = None


def _integer(value: Any) -> int | None:
    if value is None:
        return None
    try:
        converted = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Expected integer metric, received {value!r}") from exc
    if converted != converted.to_integral_value():
        raise ValueError(f"Expected integer metric, received {value!r}")
    return int(converted)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Expected numeric metric, received {value!r}") from exc


def parse_report(payload: dict[str, Any]) -> list[DailyMetric]:
    """Map a reports.query response by header name, tolerating missing metrics."""
    headers = payload.get("columnHeaders") or []
    names = [str(header.get("name")) for header in headers if isinstance(header, dict)]
    if "day" not in names:
        if not payload.get("rows"):
            return []
        raise ValueError("YouTube Analytics response did not include the day dimension")

    rows: list[DailyMetric] = []
    for raw_row in payload.get("rows") or []:
        if not isinstance(raw_row, list):
            raise ValueError("YouTube Analytics returned a malformed row")
        values = {
            name: raw_row[index] if index < len(raw_row) else None
            for index, name in enumerate(names)
        }
        try:
            metric_date = date.fromisoformat(str(values["day"]))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid YouTube Analytics day: {values.get('day')!r}") from exc

        rows.append(
            DailyMetric(
                metric_date=metric_date,
                views=_integer(values.get("views")),
                engaged_views=_integer(values.get("engagedViews")),
                likes=_integer(values.get("likes")),
                comments=_integer(values.get("comments")),
                shares=_integer(values.get("shares")),
                subscribers_gained=_integer(values.get("subscribersGained")),
                subscribers_lost=_integer(values.get("subscribersLost")),
                estimated_minutes_watched=_decimal(values.get("estimatedMinutesWatched")),
                average_view_duration=_decimal(values.get("averageViewDuration")),
                average_view_percentage=_decimal(values.get("averageViewPercentage")),
                returned_metrics=frozenset(name for name in names if name != "day"),
            )
        )
    return rows


def parse_aggregate_report(payload: dict[str, Any]) -> AggregateMetric | None:
    """Parse the single row returned by a report without dimensions."""
    headers = payload.get("columnHeaders") or []
    names = [str(header.get("name")) for header in headers if isinstance(header, dict)]
    raw_rows = payload.get("rows") or []
    if not raw_rows:
        return None
    if len(raw_rows) != 1 or not isinstance(raw_rows[0], list):
        raise ValueError("YouTube Analytics aggregate response must contain one row")
    if "day" in names:
        raise ValueError("YouTube Analytics aggregate response unexpectedly included day")
    values = {
        name: raw_rows[0][index] if index < len(raw_rows[0]) else None
        for index, name in enumerate(names)
    }
    return AggregateMetric(
        views=_integer(values.get("views")),
        engaged_views=_integer(values.get("engagedViews")),
        likes=_integer(values.get("likes")),
        comments=_integer(values.get("comments")),
        shares=_integer(values.get("shares")),
        subscribers_gained=_integer(values.get("subscribersGained")),
        subscribers_lost=_integer(values.get("subscribersLost")),
        estimated_minutes_watched=_decimal(values.get("estimatedMinutesWatched")),
        average_view_duration=_decimal(values.get("averageViewDuration")),
        average_view_percentage=_decimal(values.get("averageViewPercentage")),
        returned_metrics=frozenset(names),
    )


async def _fetch_report(access_token: str, params: dict[str, str]) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(
                API_URL,
                params=params,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )
    except httpx.HTTPError as exc:
        raise TransientGoogleError(
            f"YouTube Analytics API unreachable: {type(exc).__name__}"
        ) from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400:
        raise classify_api_error(response.status_code, payload)
    if not isinstance(payload, dict):
        raise ValueError("YouTube Analytics returned a non-object response")
    return payload


async def fetch_daily_video_metrics(
    access_token: str,
    youtube_video_id: str,
    start_date: date,
    end_date: date,
) -> list[DailyMetric]:
    """Fetch provider-day metrics for one owned video, ordered by day."""
    if start_date > end_date:
        return []
    params = {
        "ids": "channel==MINE",
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "metrics": ",".join(METRICS),
        "dimensions": "day",
        "filters": f"video=={youtube_video_id}",
        "sort": "day",
    }
    return parse_report(await _fetch_report(access_token, params))


async def fetch_video_window_metrics(
    access_token: str,
    youtube_video_id: str,
    start_date: date,
    end_date: date,
) -> AggregateMetric | None:
    """Fetch the provider aggregate for one exact lifecycle window."""
    if start_date > end_date:
        return None
    params = {
        "ids": "channel==MINE",
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "metrics": ",".join(METRICS),
        "filters": f"video=={youtube_video_id}",
    }
    return parse_aggregate_report(await _fetch_report(access_token, params))
