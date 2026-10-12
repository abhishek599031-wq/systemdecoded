"use client";

import { useEffect, useMemo, useState } from "react";

import { api, type ContentExperiment, type ExperimentList } from "../../lib/api";

type FormState = {
  hypothesis: string;
  primary_variable: string;
  control_reference_project_id: string;
  planned_topic_category: string;
  planned_topic: string;
  planned_hook_type: string;
  planned_duration_min_seconds: string;
  planned_duration_max_seconds: string;
  planned_explanation_pattern: string;
  planned_audience_level: string;
  planned_technical_depth: string;
  planned_title_style: string;
};

const EMPTY: FormState = {
  hypothesis: "",
  primary_variable: "duration / hook presentation",
  control_reference_project_id: "",
  planned_topic_category: "Cybersecurity",
  planned_topic: "",
  planned_hook_type: "surprising_fact",
  planned_duration_min_seconds: "30",
  planned_duration_max_seconds: "35",
  planned_explanation_pattern: "surprising claim → mechanism → consequence/payoff",
  planned_audience_level: "beginner_general",
  planned_technical_depth: "introductory",
  planned_title_style: "curiosity",
};

export default function ExperimentsPage() {
  const [data, setData] = useState<ExperimentList | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const selected = useMemo(
    () => data?.items.find((item) => item.id === selectedId) ?? null,
    [data, selectedId],
  );

  async function load() {
    try {
      const next = await api.experiments();
      setData(next);
      setSelectedId((current) => current ?? next.items[0]?.id ?? null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to load experiments");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (!selected) return;
    setForm({
      hypothesis: selected.hypothesis,
      primary_variable: selected.primary_variable,
      control_reference_project_id: selected.control_reference?.id ?? "",
      planned_topic_category: String(selected.planned.topic_category ?? ""),
      planned_topic: String(selected.planned.topic ?? ""),
      planned_hook_type: String(selected.planned.hook_type ?? ""),
      planned_duration_min_seconds: String(selected.planned.duration_min_seconds ?? ""),
      planned_duration_max_seconds: String(selected.planned.duration_max_seconds ?? ""),
      planned_explanation_pattern: String(selected.planned.explanation_pattern ?? ""),
      planned_audience_level: String(selected.planned.audience_level ?? ""),
      planned_technical_depth: String(selected.planned.technical_depth ?? ""),
      planned_title_style: String(selected.planned.title_style ?? ""),
    });
  }, [selected]);

  async function create() {
    setBusy(true);
    setMessage(null);
    try {
      const created = await api.createExperiment(toPayload(form));
      setMessage("Draft experiment created. Review the plan before production.");
      await load();
      setSelectedId(created.id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to create experiment");
    } finally {
      setBusy(false);
    }
  }

  async function saveDraft() {
    if (!selected || selected.status !== "DRAFT") return;
    setBusy(true);
    try {
      await api.updateExperiment(selected.id, toPayload(form));
      setMessage("Draft intent saved.");
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to save draft");
    } finally {
      setBusy(false);
    }
  }

  async function plan() {
    if (!selected) return;
    setBusy(true);
    try {
      await api.planExperiment(selected.id);
      setMessage("Experiment planned and locked. Research and script approval remain required.");
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to plan experiment");
    } finally {
      setBusy(false);
    }
  }

  async function launch() {
    if (!selected) return;
    setBusy(true);
    try {
      await api.launchExperiment(selected.id);
      setMessage("Experiment entered production through the existing pipeline.");
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Production cannot start yet");
    } finally {
      setBusy(false);
    }
  }

  if (!data) return <p className="text-sm text-ink-500">Loading experiments…</p>;

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-widest text-signal">Experiments</p>
          <h1 className="mt-1 text-2xl font-semibold">What did we intentionally test?</h1>
          <p className="mt-2 max-w-3xl text-sm text-ink-300">
            Planned intent is preserved separately from actual production and provider outcomes.
            This page makes no strategy or winner claims.
          </p>
        </div>
        <button
          onClick={() => {
            setSelectedId(null);
            setForm(EMPTY);
          }}
          className="rounded-md bg-signal px-4 py-2 text-sm text-white"
        >
          New experiment
        </button>
      </header>

      {message && <div className="rounded-md border border-ink-700 bg-ink-900 px-3 py-2 text-sm">{message}</div>}

      <section className="grid gap-3 md:grid-cols-5">
        <Evidence label="Published experiments" value={data.evidence.number_of_published_experiments} />
        <Evidence label="Complete D1" value={data.evidence.complete_d1_samples} />
        <Evidence label="Complete D3" value={data.evidence.complete_d3_samples} />
        <Evidence label="Complete D7" value={data.evidence.complete_d7_samples} />
        <Evidence label="Evidence mode" value="descriptive only" />
      </section>

      <section>
        <h2 className="mb-3 text-xs uppercase tracking-widest text-ink-500">Experiment register</h2>
        <div className="overflow-x-auto rounded-lg border border-ink-800 bg-ink-900">
          <table className="w-full min-w-[900px] text-left text-xs">
            <thead className="border-b border-ink-800 text-ink-500">
              <tr>
                {["Project / video", "Status", "Hypothesis", "Variable", "Planned topic", "Hook", "Duration", "Reference", "D1", "D3", "D7"].map((heading) => (
                  <th key={heading} className="px-3 py-3 font-medium">{heading}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.items.map((item) => (
                <tr
                  key={item.id}
                  onClick={() => setSelectedId(item.id)}
                  className={selected?.id === item.id ? "cursor-pointer border-b border-ink-800/70 bg-signal/5" : "cursor-pointer border-b border-ink-800/70 hover:bg-ink-800/50"}
                >
                  <td className="px-3 py-3">{item.project?.topic ?? item.project_id}</td>
                  <td className="px-3 py-3">{item.status}</td>
                  <td className="max-w-[260px] px-3 py-3">{item.hypothesis}</td>
                  <td className="px-3 py-3">{item.primary_variable}</td>
                  <td className="px-3 py-3">{String(item.planned.topic ?? "—")}</td>
                  <td className="px-3 py-3">{String(item.planned.hook_type ?? "—")}</td>
                  <td className="px-3 py-3">{duration(item)}</td>
                  <td className="px-3 py-3">{item.control_reference?.topic ?? "—"}</td>
                  <td className="px-3 py-3">{item.window_status?.D1 ?? "—"}</td>
                  <td className="px-3 py-3">{item.window_status?.D3 ?? "—"}</td>
                  <td className="px-3 py-3">{item.window_status?.D7 ?? "—"}</td>
                </tr>
              ))}
              {data.items.length === 0 && (
                <tr><td colSpan={11} className="px-3 py-6 text-center text-ink-500">No experiments yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="grid gap-6 lg:grid-cols-2">
        <div>
          <h2 className="mb-3 text-xs uppercase tracking-widest text-ink-500">
            {selected ? "Experiment plan · " + selected.status : "New experiment"}
          </h2>
          <div className="space-y-3 rounded-lg border border-ink-800 bg-ink-900 p-4">
            <TextArea label="Hypothesis" value={form.hypothesis} disabled={Boolean(selected && selected.status !== "DRAFT")} onChange={(value) => set("hypothesis", value)} />
            <Field label="Primary variable" value={form.primary_variable} disabled={Boolean(selected && selected.status !== "DRAFT")} onChange={(value) => set("primary_variable", value)} />
            <Field label="Planned topic" value={form.planned_topic} disabled={Boolean(selected && selected.status !== "DRAFT")} onChange={(value) => set("planned_topic", value)} />
            <Field label="Topic category" value={form.planned_topic_category} disabled={Boolean(selected && selected.status !== "DRAFT")} onChange={(value) => set("planned_topic_category", value)} />
            <div className="grid grid-cols-2 gap-3">
              <Field label="Duration min (s)" value={form.planned_duration_min_seconds} disabled={Boolean(selected && selected.status !== "DRAFT")} onChange={(value) => set("planned_duration_min_seconds", value)} />
              <Field label="Duration max (s)" value={form.planned_duration_max_seconds} disabled={Boolean(selected && selected.status !== "DRAFT")} onChange={(value) => set("planned_duration_max_seconds", value)} />
            </div>
            <Field label="Hook type" value={form.planned_hook_type} disabled={Boolean(selected && selected.status !== "DRAFT")} onChange={(value) => set("planned_hook_type", value)} />
            <Field label="Explanation pattern" value={form.planned_explanation_pattern} disabled={Boolean(selected && selected.status !== "DRAFT")} onChange={(value) => set("planned_explanation_pattern", value)} />
            <Field label="Audience level" value={form.planned_audience_level} disabled={Boolean(selected && selected.status !== "DRAFT")} onChange={(value) => set("planned_audience_level", value)} />
            <Field label="Technical depth" value={form.planned_technical_depth} disabled={Boolean(selected && selected.status !== "DRAFT")} onChange={(value) => set("planned_technical_depth", value)} />
            <Field label="Title style" value={form.planned_title_style} disabled={Boolean(selected && selected.status !== "DRAFT")} onChange={(value) => set("planned_title_style", value)} />
            {selected?.control_reference && <p className="text-xs text-ink-500">Reference: {selected.control_reference.topic}</p>}
            <div className="flex flex-wrap gap-2 pt-2">
              {!selected && <button disabled={busy} onClick={() => void create()} className="rounded-md bg-signal px-3 py-2 text-sm text-white disabled:opacity-50">Create draft</button>}
              {selected?.status === "DRAFT" && <button disabled={busy} onClick={() => void saveDraft()} className="rounded-md bg-ink-700 px-3 py-2 text-sm text-white disabled:opacity-50">Save draft</button>}
              {selected?.status === "DRAFT" && <button disabled={busy} onClick={() => void plan()} className="rounded-md bg-good px-3 py-2 text-sm text-ink-950 disabled:opacity-50">Mark planned</button>}
              {selected?.status === "PLANNED" && <button disabled={busy} onClick={() => void launch()} className="rounded-md bg-signal px-3 py-2 text-sm text-white disabled:opacity-50">Launch after script approval</button>}
            </div>
          </div>
        </div>

        <div>
          <h2 className="mb-3 text-xs uppercase tracking-widest text-ink-500">Planned vs actual</h2>
          <div className="rounded-lg border border-ink-800 bg-ink-900 p-4 text-sm">
            {!selected && <p className="text-ink-500">Create or select an experiment to inspect its outcome.</p>}
            {selected && <Comparison experimentId={selected.id} />}
          </div>
        </div>
      </section>
    </div>
  );

  function set(field: keyof FormState, value: string) {
    setForm((current) => ({ ...current, [field]: value }));
  }
}

function toPayload(form: FormState): Record<string, unknown> {
  const body: Record<string, unknown> = { ...form };
  delete body.control_reference_project_id;
  if (form.control_reference_project_id) body.control_reference_project_id = form.control_reference_project_id;
  body.planned_duration_min_seconds = form.planned_duration_min_seconds ? Number(form.planned_duration_min_seconds) : null;
  body.planned_duration_max_seconds = form.planned_duration_max_seconds ? Number(form.planned_duration_max_seconds) : null;
  return body;
}

function duration(item: ContentExperiment) {
  const min = item.planned.duration_min_seconds;
  const max = item.planned.duration_max_seconds;
  return min != null && max != null ? String(min) + "–" + String(max) + "s" : "—";
}

function Evidence({ label, value }: { label: string; value: number | string }) {
  return <div className="rounded-lg border border-ink-800 bg-ink-900 p-3"><div className="text-xs text-ink-500">{label}</div><div className="mt-1 text-lg">{value}</div></div>;
}

function Field({ label, value, disabled, onChange }: { label: string; value: string; disabled: boolean; onChange: (value: string) => void }) {
  return <label className="block text-xs text-ink-300"><span>{label}</span><input disabled={disabled} value={value} onChange={(event) => onChange(event.target.value)} className="mt-1 w-full rounded border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-ink-100 disabled:opacity-50" /></label>;
}

function TextArea({ label, value, disabled, onChange }: { label: string; value: string; disabled: boolean; onChange: (value: string) => void }) {
  return <label className="block text-xs text-ink-300"><span>{label}</span><textarea disabled={disabled} value={value} onChange={(event) => onChange(event.target.value)} rows={4} className="mt-1 w-full rounded border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-ink-100 disabled:opacity-50" /></label>;
}

function Comparison({ experimentId }: { experimentId: string }) {
  const [comparison, setComparison] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    void api.experimentComparison(experimentId).then(setComparison).catch(() => setComparison(null));
  }, [experimentId]);
  if (!comparison) return <p className="text-ink-500">No actual production profile yet.</p>;
  const dimensions = (comparison.dimensions ?? {}) as Record<string, { status?: string }>;
  return <div className="space-y-2">{Object.entries(dimensions).map(([name, value]) => <div key={name} className="flex items-center justify-between border-b border-ink-800 py-2"><span>{name.replaceAll("_", " ")}</span><span className={value.status === "MATCH" ? "text-good" : value.status === "DEVIATED" ? "text-warn" : "text-ink-500"}>{value.status ?? "UNKNOWN"}</span></div>)}<p className="pt-2 text-xs text-ink-500">Actual values are observational and may remain UNKNOWN until editorial review or production completes.</p></div>;
}
