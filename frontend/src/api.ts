export type User = {
  id: string;
  email: string;
  display_name: string;
  role: "client" | "admin";
};

export type Project = {
  id: string;
  title: string;
  description: string;
  created_at: string;
  updated_at: string;
};

export type Job = {
  id: string;
  project_id: string;
  tool: "audio_pipeline" | "audio_selection" | string;
  parent_job_id?: string | null;
  source_asset_id?: string | null;
  source_expires_at?: string | null;
  state: "queued" | "running" | "paused" | "cancelling" | "completed" | "cancelled" | "failed" | "expired";
  stage: string;
  message: string;
  progress: number;
  created_at: number;
  updated_at: number;
  error: string | null;
  artifacts: string[];
  metrics: {
    segments?: number;
    parts?: number;
    duration_seconds?: number;
    elapsed_seconds?: number;
    selected_parts?: number;
    template_id?: string;
    template_version?: number;
    output_format?: string;
  };
};

export type Asset = {
  id: string;
  project_id: string;
  original_name: string;
  content_type: string;
  size_bytes: number;
  status: "pending" | "ready";
  checksum_sha256: string | null;
  expires_at: string;
};

type UploadReservation = {
  asset: Asset;
  upload: {
    method: "PUT" | "POST";
    url: string;
    fields: Record<string, string>;
  };
};

export type UsageEvent = {
  id: string;
  project_id: string | null;
  project_title: string;
  job_id: string | null;
  provider: string;
  service: string;
  model: string;
  quantity: number;
  unit: string;
  currency: string;
  amount: string;
  status: "estimated" | "confirmed" | "reconciled";
  occurred_at: string;
};

export type TranscriptionQuote = {
  provider: string;
  model: string;
  duration_seconds: number;
  billed_seconds: number;
  currency: string;
  amount: string;
  unit_amount: string;
};

export type AnalysisSegment = {
  start: number;
  end: number;
  text: string;
};

export type CoursePart = {
  index: number;
  start: number;
  end: number;
  title: string;
  description: string;
  transcript: string;
};

export type JobAnalysis = {
  schema: number;
  audio_name: string;
  duration_seconds: number;
  checksum_sha256: string;
  segments: AnalysisSegment[];
  parts: CoursePart[];
};

export type TemplateZone = {
  kind: "title" | "speaker" | "date" | "episode";
  x: number;
  y: number;
  width: number;
  height: number;
  font_scale: number;
  color: string;
  align: "left" | "center" | "right";
};

export type BrandTemplate = {
  id: string;
  name: string;
  original_name: string;
  source_kind: "image" | "video";
  usage_mode: "static_frame" | "animated";
  status: "pending" | "ready";
  width: number | null;
  height: number | null;
  duration_seconds: number | null;
  version: number;
  frame_seconds: number;
  zones: TemplateZone[];
  preview_url: string | null;
  created_at: string;
  updated_at: string;
};

type TemplateUploadReservation = {
  template: BrandTemplate;
  upload: {
    method: "PUT" | "POST";
    url: string;
    fields: Record<string, string>;
  };
};

export type Payment = {
  id: string;
  amount: string;
  currency: string;
  method: string;
  reference: string;
  note: string;
  period: string;
  paid_at: string;
};

export type BillingSummary = {
  user: Pick<User, "id" | "email" | "display_name">;
  period: string;
  period_start: string;
  period_end: string;
  currency: string;
  confirmed_cost: string;
  estimated_cost: string;
  paid: string;
  balance: string;
  usage: UsageEvent[];
  payments: Payment[];
};

type ApiOptions = RequestInit & { body?: BodyInit | null };

async function request<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const response = await fetch(path, {
    ...options,
    credentials: "include",
    headers: {
      ...(options.body && typeof options.body === "string"
        ? { "Content-Type": "application/json" }
        : {}),
      ...options.headers,
    },
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? "Une erreur inattendue est survenue.");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  me: () => request<User>("/api/auth/me"),
  login: (email: string, password: string) =>
    request<User>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  projects: () => request<Project[]>("/api/projects"),
  createProject: (title: string, description: string) =>
    request<Project>("/api/projects", {
      method: "POST",
      body: JSON.stringify({ title, description }),
    }),
  updateProject: (projectId: string, title: string, description: string) =>
    request<Project>(`/api/projects/${projectId}`, {
      method: "PUT",
      body: JSON.stringify({ title, description }),
    }),
  deleteProject: (projectId: string) =>
    request<void>(`/api/projects/${projectId}`, { method: "DELETE" }),
  createJob: async (
    projectId: string,
    file: File,
    language: string,
    estimatedDurationSeconds: number,
    onStage?: (stage: "reserve" | "upload" | "validate" | "start") => void,
  ) => {
    onStage?.("reserve");
    const reservation = await request<UploadReservation>("/api/uploads", {
      method: "POST",
      body: JSON.stringify({
        project_id: projectId,
        filename: file.name,
        content_type: file.type || "application/octet-stream",
        size_bytes: file.size,
      }),
    });

    try {
      onStage?.("upload");
      let uploadResponse: Response;
      if (reservation.upload.method === "POST") {
        const body = new FormData();
        Object.entries(reservation.upload.fields).forEach(([key, value]) => body.append(key, value));
        body.append("file", file);
        uploadResponse = await fetch(reservation.upload.url, { method: "POST", body });
      } else {
        uploadResponse = await fetch(reservation.upload.url, {
          method: "PUT",
          body: file,
          credentials: "include",
          headers: { "Content-Type": file.type || "application/octet-stream" },
        });
      }
      if (!uploadResponse.ok) throw new Error("L’envoi du fichier temporaire a échoué.");

      onStage?.("validate");
      await request<Asset>(`/api/uploads/${reservation.asset.id}/complete`, { method: "POST" });
    } catch (reason) {
      await request<void>(`/api/uploads/${reservation.asset.id}`, { method: "DELETE" }).catch(() => undefined);
      throw reason;
    }
    // Once validated, keep the asset if the start response is interrupted so
    // the user can retry without uploading a large file again.
    onStage?.("start");
    return request<Job>("/api/jobs/from-asset", {
      method: "POST",
      body: JSON.stringify({
        asset_id: reservation.asset.id,
        language,
        estimated_duration_seconds: estimatedDurationSeconds,
      }),
    });
  },
  quoteTranscription: (durationSeconds: number) =>
    request<TranscriptionQuote>("/api/transcription/quote", {
      method: "POST",
      body: JSON.stringify({ duration_seconds: durationSeconds }),
    }),
  job: (jobId: string) => request<Job>(`/api/jobs/${jobId}`),
  jobs: (projectId?: string) =>
    request<Job[]>(`/api/jobs${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`),
  jobAnalysis: (jobId: string) => request<JobAnalysis>(`/api/jobs/${jobId}/analysis`),
  updateJobAnalysis: (
    jobId: string,
    checksumSha256: string,
    parts: Pick<CoursePart, "index" | "start" | "end" | "title" | "description">[],
  ) => request<JobAnalysis>(`/api/jobs/${jobId}/analysis`, {
    method: "PUT",
    body: JSON.stringify({ checksum_sha256: checksumSha256, parts }),
  }),
  createAudioExport: (jobId: string, checksumSha256: string, partIndices: number[]) =>
    request<Job>(`/api/jobs/${jobId}/exports/audio`, {
      method: "POST",
      body: JSON.stringify({
        checksum_sha256: checksumSha256,
        part_indices: partIndices,
      }),
    }),
  brandTemplates: () => request<BrandTemplate[]>("/api/brand/templates"),
  createBrandTemplate: async (
    name: string,
    file: File,
    usageMode: "static_frame" | "animated",
    frameSeconds: number,
    onStage?: (stage: "reserve" | "upload" | "validate") => void,
  ) => {
    onStage?.("reserve");
    const reservation = await request<TemplateUploadReservation>("/api/brand/templates", {
      method: "POST",
      body: JSON.stringify({
        name,
        filename: file.name,
        content_type: file.type || "application/octet-stream",
        size_bytes: file.size,
        usage_mode: usageMode,
        frame_seconds: frameSeconds,
      }),
    });
    try {
      onStage?.("upload");
      let response: Response;
      if (reservation.upload.method === "POST") {
        const body = new FormData();
        Object.entries(reservation.upload.fields).forEach(([key, value]) => body.append(key, value));
        body.append("file", file);
        response = await fetch(reservation.upload.url, { method: "POST", body });
      } else {
        response = await fetch(reservation.upload.url, {
          method: "PUT",
          body: file,
          credentials: "include",
          headers: { "Content-Type": file.type || "application/octet-stream" },
        });
      }
      if (!response.ok) throw new Error("L’envoi du template a échoué.");
      onStage?.("validate");
      return await request<BrandTemplate>(
        `/api/brand/templates/${reservation.template.id}/complete`,
        { method: "POST" },
      );
    } catch (reason) {
      await request<void>(`/api/brand/templates/${reservation.template.id}`, {
        method: "DELETE",
      }).catch(() => undefined);
      throw reason;
    }
  },
  deleteBrandTemplate: (templateId: string) =>
    request<void>(`/api/brand/templates/${templateId}`, { method: "DELETE" }),
  updateBrandTemplate: (templateId: string, name: string, zones: TemplateZone[]) =>
    request<BrandTemplate>(`/api/brand/templates/${templateId}`, {
      method: "PUT",
      body: JSON.stringify({ name, zones }),
    }),
  createVideoExport: (
    jobId: string,
    checksumSha256: string,
    partIndices: number[],
    template: BrandTemplate,
    outputFormat: "16:9" | "1:1" | "9:16",
    values: { title: string; speaker: string; date: string; episode: string },
  ) => request<Job>(`/api/jobs/${jobId}/exports/video`, {
    method: "POST",
    body: JSON.stringify({
      checksum_sha256: checksumSha256,
      part_indices: partIndices,
      template_id: template.id,
      template_version: template.version,
      output_format: outputFormat,
      ...values,
    }),
  }),
  billingSummary: (month?: string) =>
    request<BillingSummary>(`/api/billing/summary${month ? `?month=${encodeURIComponent(month)}` : ""}`),
  clientBillingSummaries: (month?: string) =>
    request<BillingSummary[]>(`/api/admin/billing/clients${month ? `?month=${encodeURIComponent(month)}` : ""}`),
  recordManualPayment: (
    userId: string,
    amount: string,
    period: string,
    reference: string,
    note: string,
  ) => request<Payment>("/api/admin/billing/payments", {
    method: "POST",
    body: JSON.stringify({ user_id: userId, amount, period, reference, note }),
  }),
  pauseJob: (jobId: string) => request<Job>(`/api/jobs/${jobId}/pause`, { method: "POST" }),
  resumeJob: (jobId: string) => request<Job>(`/api/jobs/${jobId}/resume`, { method: "POST" }),
  cancelJob: (jobId: string) => request<Job>(`/api/jobs/${jobId}/cancel`, { method: "POST" }),
  deleteJob: (jobId: string) => request<void>(`/api/jobs/${jobId}`, { method: "DELETE" }),
  deleteJobSource: (jobId: string) =>
    request<Job>(`/api/jobs/${jobId}/source`, { method: "DELETE" }),
  artifactUrl: (jobId: string, artifact: string) => `/api/jobs/${jobId}/artifacts/${artifact}`,
};
