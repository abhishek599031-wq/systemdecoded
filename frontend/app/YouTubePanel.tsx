"use client";

/**
 * YouTube connection panel.
 *
 * Deliberately shows connection *health*, not just connected/disconnected —
 * refresh tokens expire while the OAuth consent screen is in "testing"
 * (PHASE-1-ARCHITECTURE.md §3.3), so an expired connection is a normal state
 * the operator must be able to see and fix in one click.
 */

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type YouTubeStatus } from "@/lib/api";

const STATUS_TONE: Record<string, string> = {
  ACTIVE: "bg-good/15 text-good",
  EXPIRED: "bg-warn/15 text-warn",
  REVOKED: "bg-bad/15 text-bad",
  ERROR: "bg-bad/15 text-bad",
  NOT_CONNECTED: "bg-ink-800 text-ink-300",
};

function formatCount(value: number | null): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en", { notation: "compact" }).format(value);
}

export default function YouTubePanel() {
  const [status, setStatus] = useState<YouTubeStatus | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "good" | "bad"; text: string } | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await api.youtubeStatus());
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = setInterval(() => void refresh(), 15000);
    return () => clearInterval(id);
  }, [refresh]);

  // The OAuth callback returns the browser here with a result flag.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const result = params.get("youtube");
    if (!result) return;

    if (result === "connected") {
      setMessage({ tone: "good", text: "YouTube channel connected." });
    } else {
      setMessage({
        tone: "bad",
        text: `Connection failed: ${params.get("reason") ?? "unknown error"}`,
      });
    }
    // Clear the flag so a refresh does not replay the banner.
    window.history.replaceState({}, "", window.location.pathname);
    void refresh();
  }, [refresh]);

  const act = async (name: string, fn: () => Promise<unknown>, success: string) => {
    setBusy(name);
    setMessage(null);
    try {
      await fn();
      setMessage({ tone: "good", text: success });
      await refresh();
    } catch (e) {
      setMessage({
        tone: "bad",
        text: e instanceof ApiError ? e.message : "Something went wrong.",
      });
    } finally {
      setBusy(null);
    }
  };

  const connect = async () => {
    setBusy("connect");
    setMessage(null);
    try {
      const { authorization_url } = await api.youtubeAuthUrl();
      window.location.href = authorization_url;
    } catch (e) {
      setMessage({
        tone: "bad",
        text: e instanceof ApiError ? e.message : "Could not start the connection.",
      });
      setBusy(null);
    }
  };

  if (!status) {
    return (
      <div className="rounded-lg border border-ink-800 bg-ink-900 p-4 text-sm text-ink-500">
        Loading YouTube status…
      </div>
    );
  }

  const tone = STATUS_TONE[status.connection_status] ?? "bg-ink-800 text-ink-300";

  return (
    <div className="rounded-lg border border-ink-800 bg-ink-900 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-medium">YouTube</span>
        <span className={`rounded px-2 py-0.5 text-xs ${tone}`}>
          {status.connection_status}
        </span>
        {status.google_account_email && (
          <span className="text-xs text-ink-500">{status.google_account_email}</span>
        )}
        <div className="ml-auto flex flex-wrap gap-2">
          {!status.connected && (
            <button
              onClick={() => void connect()}
              disabled={busy !== null || !status.credentials_present}
              title={
                status.credentials_present
                  ? undefined
                  : "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET first"
              }
              className="rounded-md bg-signal px-3 py-1.5 text-sm font-medium text-ink-950 transition hover:opacity-90 disabled:opacity-40"
            >
              {busy === "connect" ? "Redirecting…" : "Connect YouTube Channel"}
            </button>
          )}
          {status.connected && (
            <>
              <button
                onClick={() => void act("sync", api.youtubeSync, "Channel metadata synced.")}
                disabled={busy !== null}
                className="rounded-md border border-ink-700 bg-ink-850 px-3 py-1.5 text-sm transition hover:border-signal hover:text-signal disabled:opacity-40"
              >
                {busy === "sync" ? "Syncing…" : "Sync now"}
              </button>
              <button
                onClick={() =>
                  void act("refresh", api.youtubeRefresh, "Access token refreshed.")
                }
                disabled={busy !== null}
                className="rounded-md border border-ink-700 bg-ink-850 px-3 py-1.5 text-sm transition hover:border-signal hover:text-signal disabled:opacity-40"
              >
                {busy === "refresh" ? "Refreshing…" : "Refresh token"}
              </button>
            </>
          )}
          {status.connection_status !== "NOT_CONNECTED" && (
            <button
              onClick={() =>
                void act("disconnect", api.youtubeDisconnect, "Disconnected.")
              }
              disabled={busy !== null}
              className="rounded-md border border-ink-700 px-3 py-1.5 text-sm text-ink-300 transition hover:border-bad hover:text-bad disabled:opacity-40"
            >
              {busy === "disconnect" ? "…" : "Disconnect"}
            </button>
          )}
        </div>
      </div>

      {message && (
        <div
          className={`mt-3 rounded-md px-3 py-2 text-sm ${
            message.tone === "good" ? "bg-good/10 text-good" : "bg-bad/10 text-bad"
          }`}
        >
          {message.text}
        </div>
      )}

      {status.connected && (
        <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-4">
          <Field label="Channel" value={status.channel.handle ?? status.channel.name} />
          <Field label="Subscribers" value={formatCount(status.channel.subscriber_count)} />
          <Field label="Videos" value={formatCount(status.channel.video_count)} />
          <Field
            label="Token expires"
            value={
              status.access_token_expires_at
                ? new Date(status.access_token_expires_at).toLocaleTimeString()
                : "—"
            }
          />
        </dl>
      )}

      {status.missing_scopes.length > 0 && (
        <p className="mt-3 rounded-md bg-warn/10 px-3 py-2 text-xs text-warn">
          Some permissions were not granted:{" "}
          {status.missing_scopes.map((s) => s.split("/").pop()).join(", ")}. Reconnect and
          accept all of them, or uploads will fail later.
        </p>
      )}

      {status.warnings.map((warning) => (
        <p key={warning} className="mt-3 rounded-md bg-warn/10 px-3 py-2 text-xs text-warn">
          {warning}
        </p>
      ))}

      {status.config_problems.length > 0 && (
        <ul className="mt-3 space-y-1 rounded-md bg-bad/10 px-3 py-2 text-xs text-bad">
          {status.config_problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}

      {!status.credentials_present && (
        <p className="mt-3 text-xs text-ink-500">
          Set <code className="mono">GOOGLE_CLIENT_ID</code> and{" "}
          <code className="mono">GOOGLE_CLIENT_SECRET</code> in <code className="mono">.env</code>,
          then restart the backend.
        </p>
      )}

      <p className="mt-3 text-xs text-ink-700">{status.known_limitation}</p>
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
