"use client";

/**
 * Review Queue.
 *
 * Everything needed to judge a video in one place (PHASE-1-ARCHITECTURE.md
 * §12.1): the video itself with the Shorts safe area overlaid, the script, the
 * scenes with their measured timings, the factual sources, and the QC report.
 *
 * The safe-area overlay is the point of reviewing here rather than in a media
 * player. Text that clears the overlays locally can still be covered by the
 * Shorts UI on a real device, and this is where that gets caught.
 */

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type ProjectDetail, type ProjectSummary } from "@/lib/api";

const VERDICT_TONE: Record<string, string> = {
  PASS: "bg-good/15 text-good",
  PASS_WITH_WARNINGS: "bg-warn/15 text-warn",
  FAIL: "bg-bad/15 text-bad",
};

function fmtBytes(n: number | null | undefined): string {
  if (!n) return "—";
  return `${(n / 1_048_576).toFixed(1)} MB`;
}

export default function ReviewPage() {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [showSafeArea, setShowSafeArea] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const [videoId, setVideoId] = useState("");

  const refresh = useCallback(async () => {
    try {
      const list = await api.projects();
      setProjects(list.items);
      const id = selected ?? list.items[0]?.id ?? null;
      if (id) {
        setSelected(id);
        setDetail(await api.project(id));
      }
    } catch (e) {
      setMessage({
        tone: "bad",
        text: e instanceof ApiError ? e.message : "Cannot reach the API.",
      });
    }
  }, [selected]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const act = async (name: string, fn: () => Promise<unknown>, ok: string) => {
    setBusy(name);
    setMessage(null);
    try {
      await fn();
      setMessage({ tone: "good", text: ok });
      if (selected) setDetail(await api.project(selected));
      await refresh();
    } catch (e) {
      setMessage({ tone: "bad", text: e instanceof ApiError ? e.message : "Action failed." });
    } finally {
      setBusy(null);
    }
  };

  if (!detail) {
    return <div className="text-sm text-ink-500">Loading projects…</div>;
  }

  const q = detail.quality;

  return (
    <div className="space-y-6">
      {/* --------------------------------------------------------- header --- */}
      <div className="flex flex-wrap items-center gap-3">
        <select
          value={selected ?? ""}
          onChange={async (e) => {
            setSelected(e.target.value);
            setDetail(await api.project(e.target.value));
          }}
          className="rounded-md border border-ink-700 bg-ink-900 px-3 py-2 text-sm"
        >
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.working_title ?? p.topic} — {p.status}
            </option>
          ))}
        </select>
        <span className="rounded bg-ink-800 px-2 py-1 text-xs text-ink-300">{detail.status}</span>
        {q && (
          <span className={`rounded px-2 py-1 text-xs ${VERDICT_TONE[q.verdict] ?? ""}`}>
            QC {q.verdict}
          </span>
        )}
      </div>

      {message && (
        <div
          className={`rounded-md px-3 py-2 text-sm ${
            message.tone === "good" ? "bg-good/10 text-good" : "bg-bad/10 text-bad"
          }`}
        >
          {message.text}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[420px_1fr]">
        {/* ------------------------------------------------------ player --- */}
        <div className="space-y-3">
          <div className="relative overflow-hidden rounded-xl border border-ink-800 bg-black">
            {detail.render ? (
              <video
                key={detail.render.id}
                src={api.videoUrl(detail.id, detail.render.id)}
                controls
                className="block w-full"
                style={{ aspectRatio: "9 / 16" }}
              />
            ) : (
              <div
                className="flex items-center justify-center text-sm text-ink-500"
                style={{ aspectRatio: "9 / 16" }}
              >
                No render yet
              </div>
            )}

            {/* Shorts UI zones, to scale. Anything important underneath these
                is invisible on a real device. */}
            {showSafeArea && detail.render && (
              <div className="pointer-events-none absolute inset-0">
                <div
                  className="absolute inset-x-0 bottom-0 border-t border-dashed border-warn/60 bg-warn/10"
                  style={{ height: "19.8%" }}
                >
                  <span className="absolute left-2 top-1 text-[10px] font-semibold uppercase tracking-wider text-warn">
                    Shorts title / description
                  </span>
                </div>
                <div
                  className="absolute bottom-[19.8%] right-0 top-0 border-l border-dashed border-warn/60 bg-warn/10"
                  style={{ width: "15.6%" }}
                >
                  <span className="absolute right-1 top-2 rotate-90 text-[10px] font-semibold uppercase tracking-wider text-warn">
                    Action rail
                  </span>
                </div>
              </div>
            )}
          </div>

          <label className="flex items-center gap-2 text-xs text-ink-300">
            <input
              type="checkbox"
              checked={showSafeArea}
              onChange={(e) => setShowSafeArea(e.target.checked)}
            />
            Show Shorts safe-area overlay
          </label>

          {detail.render && (
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
              <Field label="Resolution" value={`${detail.render.width}×${detail.render.height}`} />
              <Field
                label="Duration"
                value={
                  detail.render.duration_seconds
                    ? `${detail.render.duration_seconds.toFixed(1)}s`
                    : "—"
                }
              />
              <Field label="FPS" value={String(detail.render.fps ?? "—")} />
              <Field label="Size" value={fmtBytes(detail.render.bytes)} />
              <Field
                label="Loudness"
                value={detail.render.loudness_lufs ? `${detail.render.loudness_lufs} LUFS` : "—"}
              />
              <Field
                label="True peak"
                value={detail.render.peak_dbfs ? `${detail.render.peak_dbfs} dBFS` : "—"}
              />
            </dl>
          )}

          {/* -------------------------------------------------- decisions --- */}
          {detail.status === "VIDEO_REVIEW" && (
            <div className="flex flex-wrap gap-2 pt-1">
              <button
                onClick={() =>
                  void act("approve", () => api.review(detail.id, "approve"), "Approved.")
                }
                disabled={busy !== null}
                className="rounded-md bg-good px-3 py-2 text-sm font-medium text-ink-950 disabled:opacity-40"
              >
                Approve
              </button>
              <button
                onClick={() =>
                  void act(
                    "revise",
                    () => api.review(detail.id, "revise", "Revision requested from review UI"),
                    "Marked for revision.",
                  )
                }
                disabled={busy !== null}
                className="rounded-md border border-ink-700 px-3 py-2 text-sm disabled:opacity-40"
              >
                Request revision
              </button>
              <button
                onClick={() =>
                  void act("reject", () => api.review(detail.id, "reject"), "Rejected.")
                }
                disabled={busy !== null}
                className="rounded-md border border-ink-700 px-3 py-2 text-sm text-ink-300 hover:border-bad hover:text-bad disabled:opacity-40"
              >
                Reject
              </button>
            </div>
          )}

          {detail.status === "AWAITING_HUMAN_UPLOAD" && (
            <div className="rounded-lg border border-signal/30 bg-signal/5 p-3">
              <div className="text-sm font-medium text-signal">Ready for manual upload</div>
              <p className="mt-1 text-xs text-ink-300">
                Upload the MP4 via YouTube Studio, then paste the video ID here so the system can
                link it back to this project.
              </p>
              <div className="mt-2 flex gap-2">
                <input
                  value={videoId}
                  onChange={(e) => setVideoId(e.target.value)}
                  placeholder="YouTube video ID"
                  className="mono flex-1 rounded-md border border-ink-700 bg-ink-950 px-2 py-1.5 text-sm"
                />
                <button
                  onClick={() =>
                    void act(
                      "publish",
                      () => api.recordPublished(detail.id, videoId.trim()),
                      "Linked to the published video.",
                    )
                  }
                  disabled={busy !== null || !videoId.trim()}
                  className="rounded-md bg-signal px-3 py-1.5 text-sm font-medium text-ink-950 disabled:opacity-40"
                >
                  Link
                </button>
              </div>
            </div>
          )}

          {detail.published_video && (
            <a
              href={detail.published_video.url}
              target="_blank"
              rel="noreferrer"
              className="block rounded-lg border border-good/30 bg-good/5 p-3 text-sm text-good"
            >
              Published → {detail.published_video.youtube_video_id}
            </a>
          )}
        </div>

        {/* ------------------------------------------------------ details --- */}
        <div className="space-y-6">
          {q && (
            <Section title={`Quality checks — ${q.verdict}`}>
              <ul className="space-y-1 text-sm">
                {q.checks.map((c) => (
                  <li key={c.name} className="flex gap-2">
                    <span className={c.passed ? "text-good" : c.blocking ? "text-bad" : "text-warn"}>
                      {c.passed ? "✓" : c.blocking ? "✕" : "!"}
                    </span>
                    <span className="text-ink-300">
                      <span className="mono text-xs text-ink-500">{c.name}</span> — {c.detail}
                    </span>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          {detail.script && (
            <Section title="Script">
              <div className="space-y-2 text-sm">
                <Row label="Title" value={detail.script.selected_title ?? "—"} />
                <Row label="Hook" value={detail.script.selected_hook ?? "—"} />
                <Row label="Words" value={String(detail.script.word_count ?? "—")} />
                <Row label="Authoring" value={detail.script.authoring_mode} />
              </div>
              <p className="mt-3 rounded-md bg-ink-900 p-3 text-sm leading-relaxed text-ink-300">
                {detail.script.narration}
              </p>
            </Section>
          )}

          <Section title={`Scenes (${detail.scenes.length})`}>
            <div className="space-y-2">
              {detail.scenes.map((s) => (
                <div key={s.scene_number} className="rounded-md border border-ink-800 p-3">
                  <div className="flex flex-wrap items-baseline gap-2 text-xs text-ink-500">
                    <span className="font-semibold text-ink-300">#{s.scene_number}</span>
                    <span className="mono">{s.template_id}</span>
                    <span>
                      {s.start_seconds?.toFixed(2)}s → {s.end_seconds?.toFixed(2)}s
                      {s.duration_seconds ? ` (${s.duration_seconds.toFixed(2)}s)` : ""}
                    </span>
                  </div>
                  <div className="mt-1 text-sm text-ink-100">{s.narration}</div>
                </div>
              ))}
            </div>
          </Section>

          <Section title={`Factual sources (${detail.research.length} claims)`}>
            <ul className="space-y-2 text-sm">
              {detail.research.map((r, i) => (
                <li key={i} className="rounded-md border border-ink-800 p-3">
                  <div className="text-ink-100">{r.claim}</div>
                  <div className="mt-1 text-xs text-ink-500">
                    {r.confidence} · {r.verification_status}
                    {r.source && (
                      <>
                        {" · "}
                        {r.source.url ? (
                          <a
                            href={r.source.url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-signal hover:underline"
                          >
                            {r.source.title}
                          </a>
                        ) : (
                          r.source.title
                        )}
                      </>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </Section>

          {detail.publishing && (
            <Section title={`Publishing package — ${detail.publishing.mode}`}>
              <div className="space-y-2 text-sm">
                <Row label="Title" value={detail.publishing.title ?? "—"} />
                <Row label="Tags" value={detail.publishing.tags.join(", ") || "—"} />
                <Row
                  label="Synthetic media"
                  value={detail.publishing.contains_synthetic_media ? "declared" : "no"}
                />
              </div>
              <pre className="mono mt-3 whitespace-pre-wrap rounded-md bg-ink-900 p-3 text-xs text-ink-300">
                {detail.publishing.description}
              </pre>
            </Section>
          )}

          <Section title="Timeline">
            <ol className="space-y-1 text-xs">
              {detail.timeline.map((t, i) => (
                <li key={i} className="flex gap-2 text-ink-500">
                  <span className="mono">{new Date(t.at).toLocaleTimeString()}</span>
                  <span className="text-ink-300">
                    {t.from ?? "—"} → <span className="text-ink-100">{t.to}</span>
                  </span>
                  <span>{t.actor}</span>
                  {t.reason && <span className="truncate">· {t.reason}</span>}
                </li>
              ))}
            </ol>
          </Section>
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="mb-2 text-xs uppercase tracking-widest text-ink-500">{title}</h2>
      <div className="rounded-lg border border-ink-800 bg-ink-900 p-4">{children}</div>
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-3">
      <span className="w-32 shrink-0 text-xs text-ink-500">{label}</span>
      <span className="text-ink-100">{value}</span>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-ink-500">{label}</dt>
      <dd className="text-ink-100">{value}</dd>
    </>
  );
}
