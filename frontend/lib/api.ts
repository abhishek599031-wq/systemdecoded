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
    youtube_analytics?: {
      implemented: boolean;
      enabled: boolean;
      daily_rows: number;
      window_rows?: number;
      feature_profiles?: number;
      latest_row_sync_at: string | null;
      source_of_truth: string;
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
    title: string | null;
    published_at: string;
    privacy_status: string | null;
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

export interface AnalyticsMetrics {
  views: number | null;
  engaged_views: number | null;
  likes: number | null;
  comments: number | null;
  shares: number | null;
  subscribers_gained: number | null;
  subscribers_lost: number | null;
  estimated_minutes_watched: number | null;
  average_view_duration: number | null;
  average_view_percentage: number | null;
}

export interface AnalyticsFreshness {
  status: "AVAILABLE" | "PENDING" | "UNAVAILABLE" | "AUTH_EXPIRED" | "ERROR";
  reason: string;
  latest_metric_date: string | null;
  latest_successful_sync: string | null;
  latest_job_id: string | null;
  latest_job_status: string | null;
}

export interface AnalyticsVideo {
  published_video_id: string;
  project_id: string;
  youtube_video_id: string;
  title: string | null;
  published_at: string;
  privacy_status: string | null;
  metrics: AnalyticsMetrics;
  freshness: AnalyticsFreshness;
}

export interface DailyAnalytics extends AnalyticsMetrics {
  metric_date: string;
  synced_at: string;
}

export interface AnalyticsWindow extends AnalyticsMetrics {
  window: string;
  status: string;
  start_date: string | null;
  end_date: string | null;
  days_present: number;
  days_expected: number;
  complete: boolean;
  provider?: string;
  synced_at?: string | null;
}

export interface ContentFeatureProfile {
  id: string;
  project_id: string;
  profile_version: number;
  system_derived: {
    duration_seconds: number | null;
    narration_duration_seconds: number | null;
    scene_count: number | null;
    script_word_count: number | null;
    narration_word_count: number | null;
    narration_words_per_minute: number | null;
    voice_provider: string | null;
    voice_model: string | null;
    voice_name: string | null;
    tts_speed: number | null;
    caption_event_count: number | null;
    render_width: number | null;
    render_height: number | null;
    template_families: string[];
    content_format: string | null;
    published_title: string | null;
    cta_present: boolean | null;
  };
  editorial: {
    topic_category: string | null;
    topic: string | null;
    subtopic: string | null;
    hook_type: string | null;
    hook_text: string | null;
    title_style: string | null;
    explanation_pattern: string | null;
    audience_level: string | null;
    technical_depth: string | null;
    cta_type: string | null;
    visual_storytelling_style: string | null;
    question_answered: string | null;
  };
  field_provenance: Record<string, "SYSTEM_DERIVED" | "MANUAL">;
  derived_at: string | null;
}

export interface AuthoritativeWindow extends AnalyticsMetrics {
  window: "D1" | "D3" | "D7" | "D28" | "D90";
  status: "PENDING" | "AVAILABLE" | "ERROR";
  start_date: string;
  end_date: string;
  provider: string;
  synced_at: string | null;
  last_error: string | null;
}

export interface LearningRecord {
  video_id: string;
  published_video_id: string;
  project_id: string;
  title: string | null;
  published_at: string;
  features: ContentFeatureProfile | null;
  performance: Record<"D1" | "D3" | "D7" | "D28" | "D90", AuthoritativeWindow | null>;
}

export interface ExperimentProject {
  id: string;
  topic: string;
  status: string;
  working_title: string | null;
}

export interface ContentExperiment {
  id: string;
  project_id: string;
  experiment_version: number;
  status: string;
  hypothesis: string;
  primary_variable: string;
  planned: Record<string, string | number | null>;
  control_reference: ExperimentProject | null;
  locked_at: string | null;
  locked_snapshot: Record<string, unknown>;
  project: ExperimentProject | null;
  published_video?: { id: string; youtube_video_id: string } | null;
  window_status?: Record<"D1" | "D3" | "D7" | "D28" | "D90", string | null>;
  created_at: string;
  updated_at: string;
}

export interface ExperimentList {
  items: ContentExperiment[];
  total: number;
  evidence: {
    number_of_published_experiments: number;
    complete_d1_samples: number;
    complete_d3_samples: number;
    complete_d7_samples: number;
    complete_d28_samples: number;
    complete_d90_samples: number;
  };
  observational_only: true;
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

  analyticsOverview: () =>
    request<{
      analytics_enabled: boolean;
      published_videos: number;
      videos_with_analytics: number;
      metrics: AnalyticsMetrics;
      latest_successful_sync: string | null;
      scope: string;
    }>("/api/v1/analytics/overview"),
  analyticsVideos: () =>
    request<{ items: AnalyticsVideo[]; total: number }>("/api/v1/analytics/videos"),
  analyticsDaily: (publishedVideoId: string) =>
    request<{ items: DailyAnalytics[]; total: number }>(
      `/api/v1/analytics/videos/${publishedVideoId}/daily`,
    ),
  analyticsWindows: (publishedVideoId: string) =>
    request<{ items: AnalyticsWindow[] }>(
      `/api/v1/analytics/videos/${publishedVideoId}/windows`,
    ),
  analyticsSync: (publishedVideoId?: string) =>
    request<{ job_id: string; created: boolean; status: string }>("/api/v1/analytics/sync", {
      method: "POST",
      body: JSON.stringify({ published_video_id: publishedVideoId ?? null }),
    }),
  contentRecords: () =>
    request<{ items: LearningRecord[]; total: number }>("/api/v1/content-intelligence/records"),
  updateContentProfile: (
    projectId: string,
    editorial: Partial<ContentFeatureProfile["editorial"]>,
  ) =>
    request<ContentFeatureProfile>(`/api/v1/content-intelligence/projects/${projectId}/profile`, {
      method: "PATCH",
      body: JSON.stringify(editorial),
    }),
  refreshContentIntelligence: (publishedVideoId: string) =>
    request<{ job_id: string; created: boolean; status: string }>(
      `/api/v1/content-intelligence/videos/${publishedVideoId}/refresh`,
      { method: "POST" },
    ),
  experiments: () => request<ExperimentList>("/api/v1/experiments"),
  createExperiment: (body: Record<string, unknown>) =>
    request<ContentExperiment>("/api/v1/experiments", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateExperiment: (id: string, body: Record<string, unknown>) =>
    request<ContentExperiment>(`/api/v1/experiments/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  planExperiment: (id: string) =>
    request<ContentExperiment>(`/api/v1/experiments/${id}/plan`, { method: "POST" }),
  launchExperiment: (id: string) =>
    request<Record<string, unknown>>(`/api/v1/experiments/${id}/launch`, { method: "POST" }),
  experimentComparison: (id: string) =>
    request<Record<string, unknown>>(`/api/v1/experiments/${id}/comparison`),
  experimentOutcome: (id: string) =>
    request<Record<string, unknown>>(`/api/v1/experiments/${id}/outcome`),
};
