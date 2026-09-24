import type {
  CandidateReviewPatch, Capabilities, Category, ChunkInfo, ComplianceAudit,
  ConstraintsResponse, Explanation, Fragment, GenerateRequest, IterationDetail,
  IterationSummary, LayerInfo, ProjectDocument, ProjectListItem, ProjectSummary,
  RegDocument, RegulatoryRule, RuleCandidate, RuleCandidateStatus, RunStatus,
  SpeciesInfo,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(status: number, code: string | null, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

/** Parses both FastAPI error shapes: {detail:{code,message}} and {detail:"..."} */
async function toApiError(res: Response): Promise<ApiError> {
  let code: string | null = null;
  let message = `HTTP ${res.status} ${res.statusText}`;
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === "string") {
      message = detail;
    } else if (detail && typeof detail === "object") {
      code = typeof detail.code === "string" ? detail.code : null;
      if (typeof detail.message === "string") message = detail.message;
    }
  } catch {
    /* non-JSON body — keep status text */
  }
  return new ApiError(res.status, code, message);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch (e) {
    throw new ApiError(0, "NETWORK_ERROR",
      "Backend unreachable (is the API server running on port 8000?)");
  }
  if (!res.ok) throw await toApiError(res);
  return (await res.json()) as T;
}

function json(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export const api = {
  getCapabilities: () => request<Capabilities>("/api/capabilities"),

  uploadProject: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<ProjectDocument>("/api/projects", { method: "POST", body: form });
  },

  listProjects: () => request<ProjectListItem[]>("/api/projects"),

  /** Rich admin-dashboard listing (iteration 3 contract). */
  getProjectsSummary: () => request<ProjectSummary[]>("/api/projects/summary"),

  /** Per-tree law-compliance audit; 409 NO_RESULT when no generation exists. */
  getCompliance: (projectId: string) =>
    request<ComplianceAudit>(`/api/projects/${projectId}/compliance`),

  getDocument: (projectId: string) =>
    request<ProjectDocument>(`/api/projects/${projectId}/document`),

  patchLayerClassification: (projectId: string, layerName: string, category: Category) =>
    request<LayerInfo>(
      `/api/projects/${projectId}/layers/${encodeURIComponent(layerName)}/classification`,
      { ...json({ category }), method: "PATCH" },
    ),

  getChunks: (projectId: string) =>
    request<ChunkInfo[]>(`/api/projects/${projectId}/chunks`),

  getRules: () => request<RegulatoryRule[]>("/api/rules"),

  patchRule: (ruleId: string, body: {
    enabled?: boolean;
    assumption?: { assumedDistanceM?: number; enabled?: boolean };
  }) => request<RegulatoryRule>(`/api/rules/${ruleId}`, { ...json(body), method: "PATCH" }),

  getSpecies: () => request<SpeciesInfo[]>("/api/species"),

  getConstraints: (projectId: string) =>
    request<ConstraintsResponse>(`/api/projects/${projectId}/constraints`),

  generate: (projectId: string, body: GenerateRequest) =>
    request<{ runId: string }>(`/api/projects/${projectId}/generate`, json(body)),

  getRunStatus: (projectId: string, runId: string) =>
    request<RunStatus>(`/api/projects/${projectId}/runs/${runId}`),

  getIterations: (projectId: string) =>
    request<IterationSummary[]>(`/api/projects/${projectId}/iterations`),

  getIteration: (projectId: string, iterationId: string) =>
    request<IterationDetail>(`/api/projects/${projectId}/iterations/${iterationId}`),

  getExplanation: (projectId: string, placementId: string) =>
    request<Explanation>(`/api/projects/${projectId}/placements/${placementId}/explanation`),

  exportDxfUrl: (projectId: string) => `/api/projects/${projectId}/export/dxf`,
  exportReportUrl: (projectId: string) => `/api/projects/${projectId}/export/report`,

  // --- Regulations feature (WS4) — docs/API_CONTRACT.md, frozen contract ---

  uploadRegDocument: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<RegDocument>("/api/regulations/documents", { method: "POST", body: form });
  },

  listRegDocuments: () => request<RegDocument[]>("/api/regulations/documents"),

  getRegDocument: (documentId: string) =>
    request<RegDocument>(`/api/regulations/documents/${documentId}`),

  getRegFragments: (documentId: string, opts?: { path?: string; offset?: number; limit?: number }) => {
    const q = new URLSearchParams();
    if (opts?.path) q.set("path", opts.path);
    if (opts?.offset != null) q.set("offset", String(opts.offset));
    if (opts?.limit != null) q.set("limit", String(opts.limit));
    const qs = q.toString();
    return request<Fragment[]>(`/api/regulations/documents/${documentId}/fragments${qs ? `?${qs}` : ""}`);
  },

  /** 202 {jobId}; throws ApiError 409 code LLM_UNAVAILABLE when no local LLM. */
  extractRegDocument: (documentId: string) =>
    request<{ jobId: string }>(`/api/regulations/documents/${documentId}/extract`, { method: "POST" }),

  listRegCandidates: (opts?: { documentId?: string; status?: RuleCandidateStatus }) => {
    const q = new URLSearchParams();
    if (opts?.documentId) q.set("documentId", opts.documentId);
    if (opts?.status) q.set("status", opts.status);
    const qs = q.toString();
    return request<RuleCandidate[]>(`/api/regulations/candidates${qs ? `?${qs}` : ""}`);
  },

  verifyRegCandidate: (candidateId: string) =>
    request<RuleCandidate>(`/api/regulations/candidates/${candidateId}/verify`, { method: "POST" }),

  patchRegCandidate: (candidateId: string, body: CandidateReviewPatch) =>
    request<RuleCandidate>(`/api/regulations/candidates/${candidateId}`, {
      ...json(body), method: "PATCH",
    }),

  activateRegCandidate: (candidateId: string) =>
    request<RuleCandidate>(`/api/regulations/candidates/${candidateId}/activate`, { method: "POST" }),

  deactivateRegCandidate: (candidateId: string) =>
    request<RuleCandidate>(`/api/regulations/candidates/${candidateId}/deactivate`, { method: "POST" }),
};

/**
 * Subscribes to the run's SSE stream; falls back to 700ms polling if the
 * stream errors before completion. Calls onStatus for every update and
 * resolves when the run reaches COMPLETED or FAILED.
 */
export function watchRun(
  projectId: string,
  runId: string,
  onStatus: (s: RunStatus) => void,
): Promise<RunStatus> {
  const isTerminal = (s: RunStatus) => s.state === "COMPLETED" || s.state === "FAILED";

  return new Promise((resolve, reject) => {
    let settled = false;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;

    const finish = (s: RunStatus) => {
      if (settled) return;
      settled = true;
      if (pollTimer) clearTimeout(pollTimer);
      resolve(s);
    };

    const poll = async () => {
      if (settled) return;
      try {
        const s = await api.getRunStatus(projectId, runId);
        onStatus(s);
        if (isTerminal(s)) return finish(s);
      } catch (e) {
        if (!settled) {
          settled = true;
          return reject(e);
        }
      }
      pollTimer = setTimeout(poll, 700);
    };

    const es = new EventSource(`/api/projects/${projectId}/runs/${runId}/events`);
    es.onmessage = (ev) => {
      try {
        const s = JSON.parse(ev.data) as RunStatus;
        onStatus(s);
        if (isTerminal(s)) {
          es.close();
          finish(s);
        }
      } catch {
        /* ignore malformed event */
      }
    };
    es.onerror = () => {
      // SSE unavailable or dropped before a terminal state — poll instead.
      es.close();
      if (!settled) void poll();
    };
  });
}
