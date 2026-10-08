"use client";

import { useEffect, useMemo, useState } from "react";

import {
  api,
  type ContentFeatureProfile,
  type LearningRecord,
} from "../../lib/api";

const WINDOWS = ["D1", "D3", "D7", "D28", "D90"] as const;

const EMPTY_EDITORIAL: ContentFeatureProfile["editorial"] = {
  topic_category: null,
  topic: null,
  subtopic: null,
  hook_type: null,
  hook_text: null,
  title_style: null,
  explanation_pattern: null,
  audience_level: null,
  technical_depth: null,
  cta_type: null,
  visual_storytelling_style: null,
  question_answered: null,
};

export default function ContentIntelligencePage() {
  const [records, setRecords] = useState<LearningRecord[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editorial, setEditorial] = useState(EMPTY_EDITORIAL);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const selected = useMemo(
    () => records.find((record) => record.published_video_id === selectedId) ?? records[0],
    [records, selectedId],
  );

  async function load() {
    setLoading(true);
    try {
      const response = await api.contentRecords();
      setRecords(response.items);
      setSelectedId((current) => current ?? response.items[0]?.published_video_id ?? null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to load content intelligence");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    setEditorial(selected?.features?.editorial ?? EMPTY_EDITORIAL);
  }, [selected]);

  async function save() {
    if (!selected) return;
    setSaving(true);
    setMessage(null);
    try {
      const profile = await api.updateContentProfile(selected.project_id, editorial);
      setRecords((current) =>
        current.map((record) =>
          record.project_id === selected.project_id ? { ...record, features: profile } : record,
        ),
      );
      setMessage("Editorial classification saved.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to save classification");
    } finally {
      setSaving(false);
    }
  }

  async function refresh() {
    if (!selected) return;
    try {
      const result = await api.refreshContentIntelligence(selected.published_video_id);
      setMessage(`Refresh ${result.created ? "queued" : "already active"}: ${result.job_id}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to queue refresh");
    }
  }

  if (loading) return <p className="text-sm text-ink-500">Loading content intelligence…</p>;

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-widest text-signal">Content Intelligence</p>
          <h1 className="mt-1 text-2xl font-semibold">What did we try, and what happened?</h1>
          <p className="mt-2 max-w-3xl text-sm text-ink-300">
            Structured creative choices joined to provider-authoritative lifecycle windows. This is
            observational evidence, not a strategy recommendation.
          </p>
        </div>
        <button onClick={() => void refresh()} className="rounded-md bg-signal px-4 py-2 text-sm text-white">
          Refresh learning data
        </button>
      </header>

      {message && <div className="rounded-md border border-ink-700 bg-ink-900 px-3 py-2 text-sm">{message}</div>}

      {records.length <= 1 && (
        <div className="rounded-md border border-warn/30 bg-warn/5 px-4 py-3 text-sm text-warn">
          Comparative evidence is insufficient: only {records.length} published video is available.
          No creative strategy conclusions are shown.
        </div>
      )}

      <section>
        <SectionTitle>Published videos</SectionTitle>
        <div className="grid gap-3 md:grid-cols-2">
          {records.map((record) => (
            <button
              key={record.published_video_id}
              onClick={() => setSelectedId(record.published_video_id)}
              className={`rounded-lg border p-4 text-left ${
                selected?.published_video_id === record.published_video_id
                  ? "border-signal bg-signal/5"
                  : "border-ink-800 bg-ink-900"
              }`}
            >
              <div className="font-medium">{record.title ?? record.video_id}</div>
              <div className="mt-1 text-xs text-ink-500">
                {record.features?.editorial.topic_category ?? "Unclassified"} · {record.video_id}
              </div>
            </button>
          ))}
        </div>
      </section>

      {selected && (
        <>
          <section>
            <SectionTitle>Selected publication</SectionTitle>
            <div className="rounded-lg border border-ink-800 bg-ink-900 p-4 text-sm">
              <div className="font-medium">{selected.title ?? selected.video_id}</div>
              <div className="mt-2 grid gap-1 text-xs text-ink-400 md:grid-cols-2">
                <div>YouTube video: {selected.video_id}</div>
                <div>Project: {selected.project_id}</div>
              </div>
            </div>
          </section>

          <section>
            <SectionTitle>Provider-authoritative windows</SectionTitle>
            <div className="grid gap-3 md:grid-cols-5">
              {WINDOWS.map((name) => {
                const window = selected.performance[name];
                return (
                  <div key={name} className="rounded-lg border border-ink-800 bg-ink-900 p-3">
                    <div className="flex items-center justify-between">
                      <span className="font-semibold">{name}</span>
                      <span className="text-[10px] text-ink-500">
                        {window?.status ?? "NOT SYNCED"}
                      </span>
                    </div>
                    <div className="mt-3 text-xl">{window?.views ?? "—"}</div>
                    <div className="text-xs text-ink-500">public views</div>
                    <div className="mt-2 text-xs text-ink-300">
                      {window?.engaged_views ?? "—"} engaged · {window?.average_view_duration ?? "—"}s avg
                    </div>
                  </div>
                );
              })}
            </div>
          </section>

          <section className="grid gap-6 lg:grid-cols-2">
            <div>
              <SectionTitle>Auto / system-derived</SectionTitle>
              <div className="grid grid-cols-2 gap-3 rounded-lg border border-ink-800 bg-ink-900 p-4 text-sm">
                <Fact label="Duration" value={`${selected.features?.system_derived.duration_seconds ?? "—"}s`} />
                <Fact label="Scenes" value={selected.features?.system_derived.scene_count ?? "—"} />
                <Fact label="Narration words" value={selected.features?.system_derived.narration_word_count ?? "—"} />
                <Fact label="Words/min" value={round(selected.features?.system_derived.narration_words_per_minute)} />
                <Fact label="Voice" value={selected.features?.system_derived.voice_name ?? "—"} />
                <Fact label="Provider" value={selected.features?.system_derived.voice_provider ?? "—"} />
                <Fact label="Captions" value={selected.features?.system_derived.caption_event_count ?? "—"} />
                <Fact
                  label="Resolution"
                  value={`${selected.features?.system_derived.render_width ?? "—"}×${selected.features?.system_derived.render_height ?? "—"}`}
                />
                <Fact
                  label="Templates"
                  value={selected.features?.system_derived.template_families.join(", ") || "—"}
                  wide
                />
              </div>
            </div>

            <div>
              <SectionTitle>Manual / editorial</SectionTitle>
              <div className="space-y-3 rounded-lg border border-ink-800 bg-ink-900 p-4">
                <TextField label="Topic category" value={editorial.topic_category} onChange={(value) => update("topic_category", value)} />
                <TextField label="Topic / concept" value={editorial.topic} onChange={(value) => update("topic", value)} />
                <TextField label="Subtopic" value={editorial.subtopic} onChange={(value) => update("subtopic", value)} />
                <TextField label="Hook text" value={editorial.hook_text} onChange={(value) => update("hook_text", value)} />
                <Choice label="Hook type" value={editorial.hook_type} values={["curiosity_gap", "surprising_fact", "question", "contrarian", "problem_solution", "other"]} onChange={(value) => update("hook_type", value)} />
                <Choice label="Title style" value={editorial.title_style} values={["curiosity", "how_to", "question", "list", "warning", "direct", "other"]} onChange={(value) => update("title_style", value)} />
                <Choice label="Audience" value={editorial.audience_level} values={["beginner_general", "intermediate", "advanced", "mixed"]} onChange={(value) => update("audience_level", value)} />
                <Choice label="Technical depth" value={editorial.technical_depth} values={["introductory", "practical", "deep_dive"]} onChange={(value) => update("technical_depth", value)} />
                <TextField label="Explanation pattern" value={editorial.explanation_pattern} onChange={(value) => update("explanation_pattern", value)} />
                <TextField label="CTA type" value={editorial.cta_type} onChange={(value) => update("cta_type", value)} />
                <TextField label="Visual storytelling" value={editorial.visual_storytelling_style} onChange={(value) => update("visual_storytelling_style", value)} />
                <TextField label="Question answered" value={editorial.question_answered} onChange={(value) => update("question_answered", value)} />
                <button disabled={saving} onClick={() => void save()} className="rounded-md bg-good px-4 py-2 text-sm font-medium text-ink-950 disabled:opacity-50">
                  {saving ? "Saving…" : "Save editorial profile"}
                </button>
              </div>
            </div>
          </section>

          <section>
            <SectionTitle>Field provenance</SectionTitle>
            <div className="flex flex-wrap gap-2 rounded-lg border border-ink-800 bg-ink-900 p-4">
              {Object.entries(selected.features?.field_provenance ?? {}).map(([field, source]) => (
                <div key={field} className="rounded border border-ink-700 bg-ink-950 px-2 py-1 text-xs">
                  <span className="text-ink-300">{field.replaceAll("_", " ")}</span>{" "}
                  <span className={source === "MANUAL" ? "text-warn" : "text-signal"}>{source}</span>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  );

  function update<K extends keyof ContentFeatureProfile["editorial"]>(
    field: K,
    value: ContentFeatureProfile["editorial"][K],
  ) {
    setEditorial((current) => ({ ...current, [field]: value }));
  }
}

function round(value: number | null | undefined) {
  return value == null ? "—" : value.toFixed(1);
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="mb-3 text-xs uppercase tracking-widest text-ink-500">{children}</h2>;
}

function Fact({ label, value, wide = false }: { label: string; value: React.ReactNode; wide?: boolean }) {
  return <div className={wide ? "col-span-2" : ""}><div className="text-xs text-ink-500">{label}</div><div className="mt-1">{value}</div></div>;
}

function TextField({ label, value, onChange }: { label: string; value: string | null; onChange: (value: string) => void }) {
  return <label className="block text-xs text-ink-300"><span>{label}</span><input value={value ?? ""} onChange={(event) => onChange(event.target.value)} className="mt-1 w-full rounded border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-ink-100" /></label>;
}

function Choice({ label, value, values, onChange }: { label: string; value: string | null; values: string[]; onChange: (value: string | null) => void }) {
  return <label className="block text-xs text-ink-300"><span>{label}</span><select value={value ?? ""} onChange={(event) => onChange(event.target.value || null)} className="mt-1 w-full rounded border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-ink-100"><option value="">Unclassified</option>{values.map((item) => <option key={item} value={item}>{item.replaceAll("_", " ")}</option>)}</select></label>;
}
