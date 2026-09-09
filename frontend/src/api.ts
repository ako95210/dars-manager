export type User = {
  id: string;
  email: string;
  display_name: string;
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
  };
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
  createJob: (projectId: string, file: File, model: string, language: string) => {
    const body = new FormData();
    body.append("file", file);
    body.append("project_id", projectId);
    body.append("model", model);
    body.append("language", language);
    return request<Job>("/api/jobs", { method: "POST", body });
  },
  job: (jobId: string) => request<Job>(`/api/jobs/${jobId}`),
  jobs: (projectId?: string) =>
    request<Job[]>(`/api/jobs${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`),
  pauseJob: (jobId: string) => request<Job>(`/api/jobs/${jobId}/pause`, { method: "POST" }),
  resumeJob: (jobId: string) => request<Job>(`/api/jobs/${jobId}/resume`, { method: "POST" }),
  cancelJob: (jobId: string) => request<Job>(`/api/jobs/${jobId}/cancel`, { method: "POST" }),
  deleteJob: (jobId: string) => request<void>(`/api/jobs/${jobId}`, { method: "DELETE" }),
  artifactUrl: (jobId: string, artifact: string) => `/api/jobs/${jobId}/artifacts/${artifact}`,
};
