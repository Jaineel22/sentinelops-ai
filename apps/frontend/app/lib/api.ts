// Thin fetch wrappers over the same-origin `/api/*` proxy (see next.config.mjs).
//
// All data-plane services validate the same JWT issued by apps/api. The
// browser remains same-origin; Next.js forwards this header through its proxy.

import { getToken } from "./auth";
import type {
  IncidentDetail,
  IncidentSummary,
  InvestigationDetail,
  ModelInfo,
  ReadyStats,
  RemediationListResponse,
  RemediationView,
  ApproverRole,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await fetch(path, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  const text = await res.text();
  if (!res.ok) {
    let detail = text;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (parsed.detail) detail = typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
    } catch {
      /* keep raw text */
    }
    throw new ApiError(res.status, detail || res.statusText);
  }
  return (text ? JSON.parse(text) : null) as T;
}

const qs = (params: Record<string, string | undefined>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) p.set(k, v);
  const s = p.toString();
  return s ? `?${s}` : "";
};

// -------------------------------------------------------------- incidents
export const incidents = {
  list: (f: { service?: string; status?: string; severity?: string; limit?: string } = {}) =>
    req<IncidentSummary[]>(`/api/incident/incidents${qs(f)}`),
  get: (id: string) => req<IncidentDetail>(`/api/incident/incidents/${id}`),
  acknowledge: (id: string) =>
    req<IncidentDetail>(`/api/incident/incidents/${id}/acknowledge`, { method: "POST" }),
  resolve: (id: string, reason: string, actor: string) =>
    req<IncidentDetail>(`/api/incident/incidents/${id}/resolve`, {
      method: "POST",
      body: JSON.stringify({ reason, actor }),
    }),
};

// ---------------------------------------------------------- investigations
export const investigations = {
  forIncident: (incidentId: string) =>
    req<InvestigationDetail>(`/api/rca/incidents/${incidentId}/investigation`),
  create: (incidentId: string) =>
    req<InvestigationDetail>(`/api/rca/investigations`, {
      method: "POST",
      body: JSON.stringify({ incident_id: incidentId }),
    }),
};

// ------------------------------------------------------------ remediations
export const remediations = {
  forIncident: (incidentId: string) =>
    req<RemediationListResponse>(`/api/remediation/remediations${qs({ incident_id: incidentId })}`),
  approve: (id: string, body: { approver_identity: string; approver_role: ApproverRole; reason: string }) =>
    req<RemediationView>(`/api/remediation/remediations/${id}/approve`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  reject: (id: string, body: { approver_identity: string; approver_role: ApproverRole; reason: string }) =>
    req<RemediationView>(`/api/remediation/remediations/${id}/reject`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  execute: (id: string, dryRun: boolean) =>
    req<RemediationView>(`/api/remediation/remediations/${id}/execute`, {
      method: "POST",
      body: JSON.stringify({ dry_run: dryRun }),
    }),
};

// ---------------------------------------------------------------- detector
export const detector = {
  modelInfo: () => req<ModelInfo>(`/api/detector/model-info`),
  readyStats: () => req<ReadyStats>(`/api/detector/ready/stats`),
};
