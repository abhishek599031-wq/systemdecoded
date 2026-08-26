"use client";

/**
 * Phase 0 dashboard.
 *
 * Deliberately narrow: it proves the stack is wired together and gives an
 * operator a way to exercise the queue. The full dashboard described in
 * PHASE-1-ARCHITECTURE.md §12.1 ("needs you now", backlog, planner decisions)
 * arrives with the data that makes it meaningful.
 */

import { useCallback, useEffect, useState } from "react";
import YouTubePanel from "./YouTubePanel";
import {
  api,
  ApiError,
  type Channel,
  type JobSummary,
  type JobType,
  type SystemStatus,
} from "@/lib/api";

const POLL_MS = 3000;

const STATUS_STYLE: Record<string, string> = {
  QUEUED: "bg-ink-800 text-ink-300",
  RUNNING: "bg-signal/15 text-signal",
  SUCCEEDED: "bg-good/15 text-good",
  FAILED: "bg-bad/15 text-bad",
  CANCELLED: "bg-ink-800 text-ink-500",
};

// Jobs worth one-click access on the dashboard, with why they exist.
const DEMO_JOBS: { type: string; label: string; payload?: Record<string, unknown> }[] = [
  { type: "system.ping", label: "Ping" },
  { type: "system.flaky", label: "Retry then succeed", payload: { fail_times: 2 } },
  { type: "system.always_fails", label: "Exhaust retries" },
  { type: "system.terminal_failure", label: "Terminal failure" },
];

export default function Dashboard() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [channel, setChannel] = useState<Channel | null>(null);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [jobTypes, setJobTypes] = useState<JobType[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, c, j, t] = await Promise.all([
        api.systemStatus(),
        api.channel(),
        api.jobs({ limit: 25 }),
        api.jobTypes(),
      ]);
      setStatus(s);
      setChannel(c);
      setJobs(j.items);
      setJobTypes(t);
      setError(null);
    } catch (e) {
      setError(
        e instanceof ApiError
          ? `${e.message} (${e.status})`
          : "Cannot reach the API. Is the backend running?",
      );
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  const enqueue = async (type: string, payload?: Record<string, unknown>) => {
    setBusy(type);
    try {
      await api.enqueue(type, payload ?? {});
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Enqueue failed");
    } finally {
      setBusy(null);
    }
  };

  if (error && !status) {
    return (
      <div className="rounded-lg border border-bad/30 bg-bad/5 p-6">
        <div className="font-medium text-bad">Backend unreachable</div>
        <p className="mt-2 text-sm text-ink-300">{error}</p>
        <p className="mt-3 text-sm text-ink-500">
          Start the stack with <code className="mono text-ink-300">docker compose up</code>.
        </p>
      </div>
    );
  }

  const jobCounts = status?.jobs ?? {};

  return (
    <div className="space-y-8">
      {/* ---------------------------------------------------------- health --- */}
      <section>
        <SectionTitle>System status</SectionTitle>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat
            label="Overall"
            value={status?.status ?? "…"}
            tone={status?.status === "healthy" ? "good" : "warn"}
          />
          <Stat
            label="Database"
            value={status?.database?.connected ? "connected" : "down"}
            tone={status?.database?.connected ? "good" : "bad"}
            hint={status?.database?.migration_revision ?? undefined}
          />
          <Stat
            label="Scheduler → worker"
            value={status?.heartbeat?.stale === false ? "live" : "waiting"}
            tone={status?.heartbeat?.stale === false ? "good" : "warn"}
            hint={
              status?.heartbeat?.last_at
                ? new Date(status.heartbeat.last_at).toLocaleTimeString()
                : "no heartbeat yet"
            }
          />
          <Stat
            label="YouTube"
            value={status?.integrations?.youtube.connection_status ?? "…"}
            tone={
              status?.integrations?.youtube.connected
                ? "good"
                : status?.integrations?.youtube.connection_status === "EXPIRED"
                  ? "warn"
                  : "neutral"
            }
            hint={status?.integrations?.youtube.credentials_present ? undefined : "no credentials"}
          />
        </div>
        {status?.config_problems && status.config_problems.length > 0 && (
          <ul className="mt-3 space-y-1 rounded-lg border border-warn/30 bg-warn/5 p-3 text-sm text-warn">
            {status.config_problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}
      </section>

      {/* --------------------------------------------------------- youtube --- */}
      <section>
        <SectionTitle>YouTube connection</SectionTitle>
        <YouTubePanel />
      </section>

      {/* --------------------------------------------------------- channel --- */}
      {channel && (
        <section>
          <SectionTitle>Channel</SectionTitle>
          <div className="rounded-lg border border-ink-800 bg-ink-900 p-4">
            <div className="flex flex-wrap items-baseline gap-x-3">
              <span className="font-medium">{channel.name}</span>
              <span className="text-sm text-ink-500">{channel.tagline}</span>
            </div>
            <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-4">
              <Field label="Niche" value={channel.niche ?? "—"} />
              <Field label="Connection" value={channel.connection_status} />
              <Field
                label="Publishing"
                value={channel.publishing_enabled ? "enabled" : "disabled (kill switch)"}
              />
              <Field label="Timezone" value={channel.timezone} />
            </dl>
          </div>
        </section>
      )}

      {/* ------------------------------------------------------- job counts --- */}
      <section>
        <SectionTitle>Background jobs</SectionTitle>
        <div className="grid grid-cols-3 gap-3 md:grid-cols-6">
          {["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "RETRY_PENDING"].map(
            (key) => (
              <Stat
                key={key}
                label={key.toLowerCase().replace("_", " ")}
                value={String(jobCounts[key] ?? 0)}
                tone={key === "FAILED" && (jobCounts[key] ?? 0) > 0 ? "bad" : "neutral"}
              />
            ),
          )}
        </div>

        <div className="mt-4 rounded-lg border border-ink-800 bg-ink-900 p-4">
          <div className="text-sm font-medium">Queue diagnostics</div>
          <p className="mt-1 text-xs text-ink-500">
            These run the real queue: claim, retry with backoff, attempt exhaustion and
            terminal-error handling. Watch the table below update.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {DEMO_JOBS.map((d) => (
              <button
                key={d.type}
                onClick={() => void enqueue(d.type, d.payload)}
                disabled={busy !== null}
                className="rounded-md border border-ink-700 bg-ink-850 px-3 py-1.5 text-sm text-ink-100 transition hover:border-signal hover:text-signal disabled:opacity-40"
              >
                {busy === d.type ? "queuing…" : d.label}
              </button>
            ))}
          </div>
          {jobTypes.length > 0 && (
            <p className="mt-3 text-xs text-ink-700">
              {jobTypes.length} job types registered
            </p>
          )}
        </div>
      </section>

      {/* ------------------------------------------------------- job table --- */}
      <section>
        <SectionTitle>Recent jobs</SectionTitle>
        <div className="overflow-x-auto rounded-lg border border-ink-800">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead className="bg-ink-900 text-xs uppercase tracking-wide text-ink-500">
              <tr>
                <th className="px-4 py-2.5 font-medium">Type</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 font-medium">Attempt</th>
                <th className="px-4 py-2.5 font-medium">Worker</th>
                <th className="px-4 py-2.5 font-medium">Error</th>
                <th className="px-4 py-2.5 font-medium">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-800 bg-ink-950">
              {jobs.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-ink-500">
                    No jobs yet. Queue one above.
                  </td>
                </tr>
              )}
              {jobs.map((job) => (
                <tr key={job.id}>
                  <td className="mono px-4 py-2.5 text-xs">{job.job_type}</td>
                  <td className="px-4 py-2.5">
                    <span
                      className={`rounded px-2 py-0.5 text-xs ${
                        STATUS_STYLE[job.status] ?? "bg-ink-800"
                      }`}
                    >
                      {job.status === "QUEUED" && job.attempt > 0 ? "RETRY PENDING" : job.status}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-ink-300">
                    {job.attempt}/{job.max_attempts}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-ink-500">{job.claimed_by ?? "—"}</td>
                  <td
                    className="max-w-[240px] truncate px-4 py-2.5 text-xs text-bad"
                    title={job.error_message ?? undefined}
                  >
                    {job.error_class ?? "—"}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-ink-500">
                    {new Date(job.created_at).toLocaleTimeString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="mb-3 text-xs uppercase tracking-widest text-ink-500">{children}</h2>;
}

function Stat({
  label,
  value,
  tone = "neutral",
  hint,
}: {
  label: string;
  value: string;
  tone?: "good" | "warn" | "bad" | "neutral";
  hint?: string;
}) {
  const toneClass = {
    good: "text-good",
    warn: "text-warn",
    bad: "text-bad",
    neutral: "text-ink-100",
  }[tone];
  return (
    <div className="rounded-lg border border-ink-800 bg-ink-900 p-3">
      <div className="text-xs text-ink-500">{label}</div>
      <div className={`mt-1 text-lg font-medium ${toneClass}`}>{value}</div>
      {hint && <div className="mono mt-0.5 truncate text-[10px] text-ink-700">{hint}</div>}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-ink-500">{label}</dt>
      <dd className="text-ink-100">{value}</dd>
    </div>
  );
}
