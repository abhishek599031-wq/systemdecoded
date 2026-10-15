"use client";

import {useCallback, useEffect, useState} from "react";
import {api, ApiError, type ContentExperiment, type PlannerProposal, type PlannerRun, type ProjectDetail} from "@/lib/api";

const POLL_MS = 3000;

export default function PlannerPage() {
  const [run, setRun] = useState<PlannerRun | null>(null);
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [experiment, setExperiment] = useState<ContentExperiment | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const latest = (await api.plannerRuns()).items[0] ?? null;
      setRun(latest);
      const projectId = latest?.proposals.find(item => item.selected)?.project_id;
      setProject(projectId ? await api.project(projectId) : null);
      const experimentList = projectId ? await api.experiments() : null;
      setExperiment(experimentList?.items.find(item => item.project_id === projectId) ?? null);
      setError(null);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Cannot reach the planner API.");
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const act = async (key: string, action: () => Promise<unknown>) => {
    setBusy(key); setError(null);
    try { await action(); await refresh(); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Planner action failed."); }
    finally { setBusy(null); }
  };

  return <div className="space-y-6">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div><h1 className="text-xl font-semibold">Content planner</h1><p className="mt-1 text-sm text-ink-500">Research-backed drafts only. Production requires separate human approval.</p></div>
      <button disabled={busy !== null || run?.status === "PENDING" || run?.status === "RUNNING"} onClick={() => void act("start", api.startPlanner)} className="rounded-md bg-signal px-4 py-2 text-sm font-semibold text-ink-950 disabled:opacity-40">Plan next video</button>
    </header>
    {error && <p className="rounded-md bg-bad/10 p-3 text-sm text-bad">{error}</p>}
    {run ? <RunCard run={run} project={project} experiment={experiment} busy={busy} act={act}/> : <p className="text-sm text-ink-500">No planning runs yet.</p>}
  </div>;
}

function RunCard({run, project, experiment, busy, act}: {run: PlannerRun; project: ProjectDetail | null; experiment: ContentExperiment | null; busy: string | null; act: (key: string, action: () => Promise<unknown>) => Promise<void>}) {
  return <section className="space-y-5 rounded-xl border border-ink-800 bg-ink-900 p-5">
    <div className="flex flex-wrap items-center gap-3"><h2 className="font-medium">Current run</h2><span className="rounded bg-ink-800 px-2 py-1 text-xs">{run.status}</span><span className="text-xs text-ink-500">{run.provider} {run.model}</span>{run.status === "FAILED" && <button disabled={busy !== null} onClick={() => void act("resume", () => api.resumePlanner(run.id))} className="rounded border border-signal px-3 py-1 text-xs text-signal">Retry from checkpoint</button>}</div>
    {run.error && <div className="rounded bg-bad/10 p-3 text-sm text-bad"><div className="font-medium">Validation failure</div><div className="mt-1">{run.error}</div></div>}
    {run.decision_summary && <p className="text-sm text-ink-300"><b>Selection rationale:</b> {run.decision_summary}</p>}
    <div className="grid gap-3 md:grid-cols-2">{run.proposals.map(proposal => <Candidate key={proposal.id} run={run} proposal={proposal} busy={busy} act={act}/>)}</div>
    {project && <ReviewPackage project={project} experiment={experiment} busy={busy} act={act}/>} 
  </section>;
}

function Candidate({run, proposal, busy, act}: {run: PlannerRun; proposal: PlannerProposal; busy: string | null; act: (key: string, action: () => Promise<unknown>) => Promise<void>}) {
  return <article className={`rounded-lg border p-4 ${proposal.selected ? "border-signal bg-signal/5" : "border-ink-800"}`}>
    <div className="flex items-start justify-between gap-3"><h3 className="font-medium">#{proposal.rank} {proposal.topic}</h3><div className="space-x-1 text-xs">{proposal.ai_selected && <span className="rounded bg-good/10 px-2 py-1 text-good">AI choice</span>}{proposal.selected && <span className="rounded bg-signal/10 px-2 py-1 text-signal">In use</span>}</div></div>
    <p className="mt-2 text-sm text-ink-300">{proposal.hook}</p>
    <details className="mt-3 text-xs text-ink-500"><summary className="cursor-pointer text-ink-300">View candidate details</summary><dl className="mt-2 space-y-2"><div><dt className="text-ink-300">Why it fits</dt><dd>{proposal.rationale}</dd></div><div><dt className="text-ink-300">Research</dt><dd>{proposal.research_feasibility}</dd></div><div><dt className="text-ink-300">Visuals</dt><dd>{proposal.visual_feasibility}</dd></div><div><dt className="text-ink-300">Evidence limits</dt><dd>{proposal.evidence_limitations}</dd></div></dl></details>
    {!proposal.selected && proposal.state !== "REJECTED" && !proposal.project_id && <button disabled={busy !== null || run.status === "RUNNING" || run.status === "PENDING"} onClick={() => void act(`select-${proposal.id}`, () => api.selectPlannerProposal(run.id, proposal.id))} className="mt-3 rounded border border-ink-700 px-3 py-1.5 text-xs hover:border-signal hover:text-signal disabled:opacity-40">Use This Idea</button>}
  </article>;
}

function ReviewPackage({project, experiment, busy, act}: {project: ProjectDetail; experiment: ContentExperiment | null; busy: string | null; act: (key: string, action: () => Promise<unknown>) => Promise<void>}) {
  const review = (decision: "revise" | "reject") => {
    const notes = window.prompt(decision === "revise" ? "What should be revised?" : "Why reject this draft?");
    if (notes === null) return Promise.resolve();
    return api.reviewScript(project.id, decision, notes);
  };
  const activeResearch = project.research.filter(item => item.verification_status === "SOURCE_RETRIEVED");
  const startProduction = () => {
    if (!experiment) return Promise.reject(new Error("This project has no experiment."));
    const version = project.production.current_script_version;
    if (!window.confirm(`Authorize production for approved script v${version}? This will run Original Chatterbox on local CUDA, the generic Motion Canvas SceneSpecs, FFmpeg, and full QC. It will not publish.`)) return Promise.resolve();
    return (async () => {
      if (experiment.status === "DRAFT") await api.planExperiment(experiment.id);
      return api.launchExperiment(experiment.id);
    })();
  };
  return <section className="space-y-4 border-t border-ink-800 pt-5"><div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-medium">Video #3 content-review package</h2><p className="text-xs text-ink-500">{project.status} · experiment {experiment?.status ?? "missing"} · target {project.target_duration_seconds}s</p></div>{project.status === "SCRIPT_REVIEW" && <div className="flex gap-2"><button disabled={busy !== null} onClick={() => void act("revise", () => review("revise"))} className="rounded border border-signal px-3 py-2 text-sm text-signal">Request revision</button><button disabled={busy !== null} onClick={() => void act("reject", () => review("reject"))} className="rounded border border-bad px-3 py-2 text-sm text-bad">Reject</button><button disabled={busy !== null} onClick={() => void act("approve", () => api.approveScript(project.id))} className="rounded bg-good px-3 py-2 text-sm font-semibold text-ink-950">Approve script</button></div>}{project.production.can_start_production && <button disabled={busy !== null} onClick={() => void act("start-production", startProduction)} className="rounded bg-signal px-3 py-2 text-sm font-semibold text-white">{project.production.action_label}</button>}</div>
    <dl className="grid gap-2 rounded border border-ink-800 p-3 text-xs sm:grid-cols-2 lg:grid-cols-5"><div><dt className="text-ink-500">Current script</dt><dd>v{project.production.current_script_version ?? "—"}</dd></div><div><dt className="text-ink-500">Latest rendered script</dt><dd>{project.production.latest_rendered_script_version ? `v${project.production.latest_rendered_script_version}` : "None"}</dd></div><div><dt className="text-ink-500">Latest job</dt><dd>{project.production.latest_production_job_status ?? "None"}</dd></div><div><dt className="text-ink-500">New attempt</dt><dd>{project.production.production_required ? "Required" : "Not required"}</dd></div><div><dt className="text-ink-500">Latest render</dt><dd>{project.production.latest_render_qc ? `QC ${project.production.latest_render_qc}` : "No QC"} · {project.production.latest_render_review_state ?? "historical"}</dd></div></dl>
    {project.script && <div><h3 className="text-sm font-medium">{project.script.selected_title}</h3><p className="mt-2 whitespace-pre-wrap text-sm text-ink-300">{project.script.narration}</p><p className="mt-2 text-xs text-ink-500">Exact word count: {project.script.word_count} · estimated duration: {project.script.estimated_duration_seconds ?? "pending"}s</p></div>}
    <div><h3 className="text-sm font-medium">Verified research</h3><ul className="mt-2 space-y-2 text-xs text-ink-400">{activeResearch.map((item, index) => <li key={`${item.claim}-${index}`}><a className="text-signal" href={item.source?.url ?? undefined} target="_blank">{item.source?.title ?? "Source"}</a>: {item.claim}</li>)}</ul></div>
    <div><h3 className="text-sm font-medium">SceneSpecs</h3><ol className="mt-2 space-y-2">{project.scenes.map(scene => <li key={scene.scene_number} className="rounded border border-ink-800 p-3 text-xs"><b>Scene {scene.scene_number}</b><p className="mt-1 text-ink-300">{scene.narration}</p><p className="mt-1 text-ink-500">{scene.on_screen_text} · {scene.template_id}</p></li>)}</ol></div>
    {project.production_job && <div className="rounded border border-ink-800 p-3 text-xs"><b>Production job: {project.production_job.status}</b><p className="mt-1 text-ink-500">Script v{project.production_job.script_version ?? "unknown"} · {project.production_job.current_script_match ? "current revision" : "historical revision"} · attempt {project.production_job.attempt}/{project.production_job.max_attempts} · {project.production_job.id}</p>{project.production_job.error_message && <p className="mt-2 text-bad">{project.production_job.error_message}</p>}</div>}
    {project.failure_reason && <p className="rounded bg-bad/10 p-3 text-sm text-bad">{project.failure_reason}</p>}
    <p className="text-xs text-ink-500">Script approval does not start TTS or rendering. Production always requires explicit confirmation and is bound to the displayed script revision.</p>
  </section>;
}
