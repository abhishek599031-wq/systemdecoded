"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  ApiError,
  type AnalyticsMetrics,
  type AnalyticsVideo,
  type AnalyticsWindow,
  type DailyAnalytics,
} from "@/lib/api";

const EMPTY_METRICS: AnalyticsMetrics = {
  views: null,
  engaged_views: null,
  likes: null,
  comments: null,
  shares: null,
  subscribers_gained: null,
  subscribers_lost: null,
  estimated_minutes_watched: null,
  average_view_duration: null,
  average_view_percentage: null,
};

const STATUS_CLASS: Record<string, string> = {
  AVAILABLE: "bg-good/15 text-good",
  PENDING: "bg-warn/15 text-warn",
  UNAVAILABLE: "bg-ink-800 text-ink-300",
  AUTH_EXPIRED: "bg-bad/15 text-bad",
  ERROR: "bg-bad/15 text-bad",
};

function count(value: number | null): string {
  return value === null ? "—" : new Intl.NumberFormat("en").format(value);
}

function seconds(value: number | null): string {
  return value === null ? "—" : `${value.toFixed(1)}s`;
}

function percent(value: number | null): string {
  return value === null ? "—" : `${value.toFixed(1)}%`;
}

export default function AnalyticsPage() {
  const [overview, setOverview] = useState<{
    analytics_enabled: boolean;
    published_videos: number;
    videos_with_analytics: number;
    metrics: AnalyticsMetrics;
    latest_successful_sync: string | null;
  } | null>(null);
  const [videos, setVideos] = useState<AnalyticsVideo[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [daily, setDaily] = useState<DailyAnalytics[]>([]);
  const [windows, setWindows] = useState<AnalyticsWindow[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [nextOverview, nextVideos] = await Promise.all([
        api.analyticsOverview(),
        api.analyticsVideos(),
      ]);
      setOverview(nextOverview);
      setVideos(nextVideos.items);
      setSelectedId((current) => current ?? nextVideos.items[0]?.published_video_id ?? null);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load analytics");
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 15000);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!selectedId) {
      setDaily([]);
      setWindows([]);
      return;
    }
    Promise.all([api.analyticsDaily(selectedId), api.analyticsWindows(selectedId)])
      .then(([days, derived]) => {
        setDaily(days.items);
        setWindows(derived.items);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Unable to load video"));
  }, [selectedId, videos]);

  const selected = useMemo(
    () => videos.find((video) => video.published_video_id === selectedId) ?? null,
    [selectedId, videos],
  );

  const sync = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const result = await api.analyticsSync(selectedId ?? undefined);
      setMessage(result.created ? "Analytics sync queued." : "A sync is already queued or running.");
      window.setTimeout(() => void refresh(), 3000);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not queue analytics sync");
    } finally {
      setBusy(false);
    }
  };

  const totals = overview?.metrics ?? EMPTY_METRICS;

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-[0.2em] text-signal">SystemDecoded</p>
          <h1 className="mt-1 text-2xl font-semibold">Analytics</h1>
          <p className="mt-1 text-sm text-ink-500">
            Canonical daily YouTube metrics for reconciled published videos.
          </p>
        </div>
        <button
          onClick={() => void sync()}
          disabled={busy || !overview?.analytics_enabled}
          title={overview?.analytics_enabled ? undefined : "Analytics kill switch is disabled"}
          className="rounded-md bg-signal px-4 py-2 text-sm font-medium text-ink-950 disabled:opacity-40"
        >
          {busy ? "Queuing…" : "Sync Analytics"}
        </button>
      </header>

      {error && <Notice tone="bad">{error}</Notice>}
      {message && <Notice tone="good">{message}</Notice>}
      {overview && !overview.analytics_enabled && (
        <Notice tone="warn">Analytics collection is disabled by the channel kill switch.</Notice>
      )}

      <section>
        <SectionTitle>Channel overview · tracked published videos</SectionTitle>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <Stat label="Published" value={String(overview?.published_videos ?? 0)} />
          <Stat label="Public views" value={count(totals.views)} />
          <Stat label="Engaged views" value={count(totals.engaged_views)} />
          <Stat label="Likes" value={count(totals.likes)} />
          <Stat label="Comments" value={count(totals.comments)} />
        </div>
      </section>

      <section>
        <SectionTitle>Published videos</SectionTitle>
        <div className="overflow-x-auto rounded-lg border border-ink-800">
          <table className="w-full min-w-[980px] text-left text-sm">
            <thead className="bg-ink-900 text-xs uppercase tracking-wide text-ink-500">
              <tr>
                {[
                  "Video",
                  "Published",
                  "Views",
                  "Engaged",
                  "Likes",
                  "Comments",
                  "Avg duration",
                  "Avg %",
                  "Freshness",
                  "Last sync",
                ].map((heading) => (
                  <th key={heading} className="px-3 py-2.5 font-medium">{heading}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-800 bg-ink-950">
              {videos.map((video) => (
                <tr
                  key={video.published_video_id}
                  onClick={() => setSelectedId(video.published_video_id)}
                  className={`cursor-pointer hover:bg-ink-900 ${selectedId === video.published_video_id ? "bg-signal/5" : ""}`}
                >
                  <td className="max-w-[260px] px-3 py-3 font-medium">{video.title ?? video.youtube_video_id}</td>
                  <td className="px-3 py-3 text-xs text-ink-500">{new Date(video.published_at).toLocaleDateString()}</td>
                  <td className="px-3 py-3">{count(video.metrics.views)}</td>
                  <td className="px-3 py-3">{count(video.metrics.engaged_views)}</td>
                  <td className="px-3 py-3">{count(video.metrics.likes)}</td>
                  <td className="px-3 py-3">{count(video.metrics.comments)}</td>
                  <td className="px-3 py-3">{seconds(video.metrics.average_view_duration)}</td>
                  <td className="px-3 py-3">{percent(video.metrics.average_view_percentage)}</td>
                  <td className="px-3 py-3">
                    <span className={`rounded px-2 py-1 text-xs ${STATUS_CLASS[video.freshness.status] ?? "bg-ink-800"}`}>
                      {video.freshness.status}
                    </span>
                    <div className="mt-1 text-[10px] text-ink-500">{video.freshness.reason.replaceAll("_", " ")}</div>
                  </td>
                  <td className="px-3 py-3 text-xs text-ink-500">
                    {video.freshness.latest_successful_sync
                      ? new Date(video.freshness.latest_successful_sync).toLocaleString()
                      : "—"}
                  </td>
                </tr>
              ))}
              {!videos.length && (
                <tr><td colSpan={10} className="px-4 py-8 text-center text-ink-500">No published videos.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {selected && (
        <>
          <section>
            <SectionTitle>{selected.title ?? selected.youtube_video_id}</SectionTitle>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Stat label="Views" value={count(selected.metrics.views)} />
              <Stat label="Engaged views" value={count(selected.metrics.engaged_views)} />
              <Stat label="Avg view duration" value={seconds(selected.metrics.average_view_duration)} />
              <Stat label="Avg viewed" value={percent(selected.metrics.average_view_percentage)} />
              <Stat label="Likes" value={count(selected.metrics.likes)} />
              <Stat label="Comments" value={count(selected.metrics.comments)} />
              <Stat label="Shares" value={count(selected.metrics.shares)} />
              <Stat label="Subscribers gained" value={count(selected.metrics.subscribers_gained)} />
            </div>
          </section>

          <section className="grid gap-4 lg:grid-cols-2">
            <DailyChart title="Daily public views" rows={daily} field="views" color="#4f8cff" />
            <DailyChart title="Daily engaged views" rows={daily} field="engaged_views" color="#34d399" />
          </section>

          <section>
            <SectionTitle>Performance windows</SectionTitle>
            <div className="grid gap-3 md:grid-cols-5">
              {windows.map((window) => (
                <div key={window.window} className="rounded-lg border border-ink-800 bg-ink-900 p-3">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold">{window.window}</span>
                    <span className={`rounded px-1.5 py-0.5 text-[10px] ${STATUS_CLASS[window.status] ?? "bg-ink-800"}`}>
                      {window.status}
                    </span>
                  </div>
                  <div className="mt-3 text-xl">{count(window.views)}</div>
                  <div className="text-xs text-ink-500">public views</div>
                  <div className="mt-2 text-sm text-ink-300">{count(window.engaged_views)} engaged</div>
                  <div className="mt-2 text-[10px] text-ink-500">
                    {window.days_present}/{window.days_expected} reporting days
                    {!window.complete && " · incomplete"}
                  </div>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function DailyChart({
  title,
  rows,
  field,
  color,
}: {
  title: string;
  rows: DailyAnalytics[];
  field: "views" | "engaged_views";
  color: string;
}) {
  const points = rows
    .map((row, index) => ({ index, value: row[field] }))
    .filter((point): point is { index: number; value: number } => point.value !== null);
  const max = Math.max(1, ...points.map((point) => point.value));
  const width = 600;
  const height = 180;
  const polyline = points
    .map((point) => {
      const x = rows.length <= 1 ? width / 2 : (point.index / (rows.length - 1)) * width;
      const y = height - (point.value / max) * (height - 20) - 10;
      return `${x},${y}`;
    })
    .join(" ");
  return (
    <div className="rounded-lg border border-ink-800 bg-ink-900 p-4">
      <h3 className="text-sm font-medium">{title}</h3>
      {points.length ? (
        <>
          <svg viewBox={`0 0 ${width} ${height}`} className="mt-3 h-44 w-full" role="img" aria-label={title}>
            <line x1="0" y1={height - 10} x2={width} y2={height - 10} stroke="#2a2f3b" />
            <polyline points={polyline} fill="none" stroke={color} strokeWidth="3" vectorEffect="non-scaling-stroke" />
          </svg>
          <div className="flex justify-between text-[10px] text-ink-500">
            <span>{rows[0]?.metric_date}</span><span>{rows.at(-1)?.metric_date}</span>
          </div>
        </>
      ) : (
        <div className="flex h-44 items-center justify-center text-sm text-ink-500">Pending provider data</div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg border border-ink-800 bg-ink-900 p-3"><div className="text-xs text-ink-500">{label}</div><div className="mt-1 text-lg font-medium">{value}</div></div>;
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="mb-3 text-xs uppercase tracking-widest text-ink-500">{children}</h2>;
}

function Notice({ children, tone }: { children: React.ReactNode; tone: "good" | "warn" | "bad" }) {
  const style = tone === "good" ? "border-good/30 bg-good/5 text-good" : tone === "warn" ? "border-warn/30 bg-warn/5 text-warn" : "border-bad/30 bg-bad/5 text-bad";
  return <div className={`rounded-md border px-3 py-2 text-sm ${style}`}>{children}</div>;
}
