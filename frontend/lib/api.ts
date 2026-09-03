/**
 * Typed client for the SystemDecoded API.
 *
 * Phase 0 hand-writes these types. Once the API surface stabilises they should
 * be generated from the FastAPI OpenAPI schema so the contract cannot drift
 * (PHASE-1-ARCHITECTURE.md §12.3).
 */

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type JobStatus = "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";

export interface JobSummary {
  id: string;
  job_type: string;
  status: JobStatus;
  priority: number;
  attempt: number;
  max_attempts: number;
  run_after: string;
  claimed_by: string | null;
  started_at: string | null;
  finished_at: string | null;
  error_class: string | null;
  error_message: string | null;
  project_id: string | null;
  created_at: string;
}

export interface JobEvent {
  id: string;
  event_type: string;
  attempt: number;
  message: string | null;
  data: Record<string, unknown> | null;
  created_at: string;
}

export interface JobDetail extends JobSummary {
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  traceback: string | null;
  timeout_seconds: number;
  idempotency_key: string | null;
  heartbeat_at: string | null;
  events: JobEvent[];
}

export interface JobList {
  items: JobSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface JobType {
  name: string;
  description: string;
  max_attempts: number;
  timeout_seconds: number;
  default_priority: number;
  retry_on: string[];
}

export interface SystemStatus {
  status: "healthy" | "degraded" | "unhealthy";
  version?: string;
  app?: { name: string; tagline: string; environment: string };
  database: { connected: boolean; migration_revision?: string | null; error?: string };
  jobs?: Record<string, number>;
  heartbeat?: { last_at: string | null; stale: boolean; stale_after_seconds: number };
  integrations?: {
    youtube: {
      enabled: boolean;
      credentials_present: boolean;
      status: string;
      connection_status?: string;
      connected?: boolean;
    };
    llm: { mechanical_mode: string; creative_mode: string; status: string };
  };
  config_problems?: string[];
}

export interface ProjectSummary {
  id: string;
  topic: string;
  working_title: string | null;
  status: string;
  content_pillar: string | null;
  target_duration_seconds: number;
  failure_reason: string | null;
  created_at: string;
}

export interface QualityCheckItem {
  name: string;
  passed: boolean;
  blocking: boolean;
  detail: string;
  measured: unknown;
}

export interface ProjectDetail extends ProjectSummary {
  status_detail: string | null;
  content_format: string | null;
  curiosity_gap: string | null;
  script: {
    id: string;
    version: number;
    selected_title: string | null;
    title_candidates: string[];
    selected_hook: string | null;
    hook_candidates: string[];
    narration: string;
    description: string | null;
    hashtags: string[];
    word_count: number | null;
    authoring_mode: string;
  } | null;
  scenes: {
    scene_number: number;
    narration: string;
    on_screen_text: string | null;
    visual_instruction: string | null;
    template_id: string;
    start_seconds: number | null;
    end_seconds: number | null;
    duration_seconds: number | null;
  }[];
  research: {
    claim: string;
    claim_type: string;
    confidence: string;
    verification_status: string;
    source: { title: string; url: string | null; publisher: string | null } | null;
  }[];
  render: {
    id: string;
    status: string;
    filename: string | null;
    width: number | null;
    height: number | null;
    fps: number | null;
    duration_seconds: number | null;
    bytes: number | null;
    loudness_lufs: number | null;
    peak_dbfs: number | null;
    error_message: string | null;
  } | null;
  quality: {
    verdict: string;
    checks: QualityCheckItem[];
    blocking_issues: string[];
    warnings: string[];
  } | null;
  assets: { asset_type: string; origin: string; license: string; provider: string | null }[];
  publishing: {
    mode: string;
    state: string;
    video: { filename: string | null; bytes: number | null; resolution: string | null };
    title: string | null;
    description: string | null;
    tags: string[];
    contains_synthetic_media: boolean;
    notes: string | null;
  } | null;
  published_video: {
    youtube_video_id: string;
    url: string;
    reconciled_at: string | null;
    method: string | null;
  } | null;
  timeline: { from: string | null; to: string; actor: string; reason: string | null; at: string }[];
}

export interface YouTubeStatus {
  implemented: boolean;
  enabled: boolean;
  credentials_present: boolean;
  connected: boolean;
  connection_status: string;
  google_account_email: string | null;
  granted_scopes: string[];
  missing_scopes: string[];
  access_token_expires_at: string | null;
  last_refreshed_at: string | null;
  last_error: string | null;
  consent_publishing_status: string;
  audit_status: string;
  warnings: string[];
  redirect_uri: string;
  config_problems: string[];
  known_limitation: string;
  channel: {
    youtube_channel_id: string | null;
    name: string;
    handle: string | null;
    thumbnail_url: string | null;
    subscriber_count: number | null;
    video_count: number | null;
    view_count: number | null;
    last_sync_at: string | null;
  };
}

export interface Channel {
  id: string;
  name: string;
  tagline: string | null;
  handle: string | null;
  niche: string | null;
  language: string;
  timezone: string;
  youtube_channel_id: string | null;
  connection_status: string;
  publishing_enabled: boolean;
  analytics_enabled: boolean;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });

  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    let code: string | undefined;
    try {
      const body = await response.json();
      message = body?.error?.message ?? message;
      code = body?.error?.code;
    } catch {
      // Non-JSON error body; the status line is the best we have.
    }
    throw new ApiError(message, response.status, code);
  }
  return response.json() as Promise<T>;
}

export const api = {
  systemStatus: () => request<SystemStatus>("/api/v1/system/status"),
  channel: () => request<Channel>("/api/v1/channel"),
  jobTypes: () => request<JobType[]>("/api/v1/jobs/types"),
  jobs: (params: { limit?: number; status?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.limit) query.set("limit", String(params.limit));
    if (params.status) query.set("status", params.status);
    const qs = query.toString();
    return request<JobList>(`/api/v1/jobs${qs ? `?${qs}` : ""}`);
  },
  job: (id: string) => request<JobDetail>(`/api/v1/jobs/${id}`),
  enqueue: (jobType: string, payload: Record<string, unknown> = {}) =>
    request<{ job: JobSummary; created: boolean }>("/api/v1/jobs", {
      method: "POST",
      body: JSON.stringify({ job_type: jobType, payload }),
    }),
  requeue: (id: string) =>
    request<JobSummary>(`/api/v1/jobs/${id}/requeue`, {
      method: "POST",
      body: JSON.stringify({ reset_attempts: true }),
    }),
  cancel: (id: string) =>
    request<JobSummary>(`/api/v1/jobs/${id}/cancel`, { method: "POST" }),

  projects: () => request<{ items: ProjectSummary[]; total: number }>("/api/v1/projects"),
  project: (id: string) => request<ProjectDetail>(`/api/v1/projects/${id}`),
  // renderId busts the browser's HTTP cache: without it, re-rendering a
  // project keeps the same URL and the browser silently serves the previous
  // video (the backend sets long-lived caching once it sees this param, so
  // pass it whenever it's known).
  videoUrl: (id: string, renderId?: string | null) =>
    `${API_URL}/api/v1/projects/${id}/video${renderId ? `?r=${renderId}` : ""}`,
  produce: (id: string) =>
    request<Record<string, unknown>>(`/api/v1/projects/${id}/produce`, { method: "POST" }),
  review: (id: string, decision: "approve" | "revise" | "reject", notes?: string) =>
    request<Record<string, unknown>>(`/api/v1/projects/${id}/review`, {
      method: "POST",
      body: JSON.stringify({ decision, notes }),
    }),
  recordPublished: (id: string, youtube_video_id: string) =>
    request<Record<string, unknown>>(`/api/v1/projects/${id}/published`, {
      method: "POST",
      body: JSON.stringify({ youtube_video_id }),
    }),

  youtubeStatus: () => request<YouTubeStatus>("/api/v1/youtube/status"),
  // Asks for the consent URL rather than following a redirect, so a
  // misconfiguration surfaces in the UI instead of on a Google error page.
  youtubeAuthUrl: () =>
    request<{ authorization_url: string }>("/api/v1/youtube/oauth/start?json=true"),
  youtubeSync: () => request<Record<string, unknown>>("/api/v1/youtube/sync", { method: "POST" }),
  youtubeRefresh: () =>
    request<Record<string, unknown>>("/api/v1/youtube/refresh", { method: "POST" }),
  youtubeDisconnect: () =>
    request<{ disconnected: boolean }>("/api/v1/youtube/disconnect", { method: "POST" }),
};
