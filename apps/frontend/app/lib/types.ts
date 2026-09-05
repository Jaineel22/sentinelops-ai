// TypeScript mirrors of the real SentinelOps API response shapes.
// Sources:
//   incident-correlator  services/incident-correlator/incident_correlator/api.py
//   rca-agent            services/rca-agent/rca_agent/schemas.py + api/schemas.py
//   remediation-controller  services/remediation-controller/.../api/schemas.py
//   anomaly-detector     services/anomaly-detector/anomaly_detector/app.py

// ---------------------------------------------------------------- incidents
export type Severity = "INFO" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type IncidentStatus =
  | "OPEN"
  | "ACKNOWLEDGED"
  | "INVESTIGATING"
  | "MITIGATING"
  | "RESOLVED";

export interface IncidentSummary {
  id: string;
  correlation_key: string;
  service: string;
  environment: string;
  status: IncidentStatus;
  severity: Severity;
  title: string;
  anomaly_count: number;
  distinct_abnormal_signals: number;
  started_at: string;
  last_evidence_at: string;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
}

export interface EvidenceOut {
  event_id: string;
  detector: string;
  detector_version: string;
  anomaly_score: number;
  threshold: number;
  window_start: string;
  window_end: string;
  signals: Record<string, number>;
  abnormal_signals: string[];
  trace_id: string | null;
  occurred_at: string;
  correlation_reason: string;
}

export interface TransitionOut {
  from_status: IncidentStatus | null;
  to_status: IncidentStatus;
  actor: string;
  reason: string;
  severity_at_transition: Severity | null;
  created_at: string;
}

export interface RelatedIncidentOut {
  id: string;
  service: string;
  environment: string;
  status: IncidentStatus;
  severity: Severity;
  title: string;
  started_at: string;
  last_evidence_at: string;
}

export interface IncidentDetail extends IncidentSummary {
  severity_reasons: string[];
  abnormal_signal_names: string[];
  max_anomaly_score: number;
  max_error_rate: number;
  max_latency_p95_ms: number;
  detector: string;
  duration_seconds: number;
  acknowledged_at: string | null;
  resolution: string | null;
  evidence: EvidenceOut[];
  history: TransitionOut[];
  related_incidents: RelatedIncidentOut[];
}

// ------------------------------------------------------------- investigation
export type InvestigationStatus =
  | "PENDING"
  | "PLANNING"
  | "COLLECTING_EVIDENCE"
  | "ANALYZING"
  | "VERIFYING"
  | "COMPLETED"
  | "INSUFFICIENT_EVIDENCE"
  | "FAILED"
  | "TIMED_OUT";

export type Confidence = "UNKNOWN" | "LOW" | "MEDIUM" | "HIGH";
export type HypothesisVerdict = "SUPPORTED" | "REFUTED" | "UNVERIFIED" | "CONFLICTING";

export interface Investigation {
  id: string;
  incident_id: string;
  status: InvestigationStatus;
  trigger: "EVENT" | "MANUAL";
  mode: "mock" | "live";
  model: string | null;
  termination_reason: string | null;
  tool_call_count: number;
  step_count: number;
  evidence_count: number;
  overall_confidence: Confidence;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface InvestigationStep {
  seq: number;
  kind: string;
  phase: InvestigationStatus;
  description: string;
  tool_name: string | null;
  evidence_ids: string[];
  at: string;
}

export interface RcaEvidence {
  id: string;
  source_type: string;
  source_reference: string;
  trust_level: string;
  tool_name: string;
  service: string | null;
  summary: string;
  content: Record<string, unknown>;
  observed_at: string | null;
  collected_at: string;
}

export interface Finding {
  id: string;
  type: string;
  statement: string;
  evidence_ids: string[];
  confidence: Confidence;
}

export interface Hypothesis {
  id: string;
  statement: string;
  supporting_evidence_ids: string[];
  contradicting_evidence_ids: string[];
  assessment: string;
  verdict: HypothesisVerdict;
}

export interface RootCause {
  statement: string;
  confidence: Confidence;
  evidence_ids: string[];
  reasoning_summary: string;
}

export interface RecommendedAction {
  action_type: string;
  target_service: string | null;
  description: string;
  rationale: string;
  evidence_ids: string[];
  requires_human_approval: true;
}

export interface TimelineEntry {
  at: string;
  description: string;
  evidence_ids: string[];
}

export interface RcaReport {
  incident_id: string;
  investigation_id: string;
  status: "COMPLETED" | "INSUFFICIENT_EVIDENCE";
  summary: string;
  severity: string | null;
  affected_services: string[];
  timeline: TimelineEntry[];
  findings: Finding[];
  hypotheses: Hypothesis[];
  root_cause: RootCause | null;
  contributing_factors: Finding[];
  recommended_action: RecommendedAction;
  evidence: RcaEvidence[];
  overall_confidence: Confidence;
  uncertainty: string;
  unavailable_evidence_sources: string[];
}

export interface InvestigationDetail {
  investigation: Investigation;
  steps: InvestigationStep[];
  report: RcaReport | null;
}

// -------------------------------------------------------------- remediation
export type RemediationStatus =
  | "PROPOSED"
  | "POLICY_EVALUATION"
  | "PENDING_APPROVAL"
  | "APPROVED"
  | "EXECUTING"
  | "EXECUTED"
  | "VERIFYING"
  | "BLOCKED"
  | "REJECTED"
  | "EXPIRED"
  | "EXECUTION_FAILED"
  | "RECOVERED"
  | "RECOVERY_FAILED";

export type ApproverRole = "OPERATOR" | "INCIDENT_RESPONDER" | "ADMINISTRATOR";
export type RiskLevel = "LOW" | "MEDIUM" | "HIGH";

export interface RemediationApproval {
  approval_id: string;
  decision: string;
  approver_identity: string;
  approver_role: ApproverRole;
  reason: string;
  decided_at: string;
}

export interface RemediationExecution {
  execution_id?: string;
  executor?: string;
  status: string;
  dry_run: boolean;
  detail?: string;
  started_at?: string;
  finished_at?: string;
}

export interface RemediationVerification {
  status: string;
  recovered?: boolean;
  failure_reason: string | null;
  checked_at?: string;
}

export interface RemediationView {
  remediation_id: string;
  incident_id: string;
  investigation_id: string | null;
  trigger: string;
  proposed_by: string;
  action_type: string;
  target: { service_name: string; environment: string };
  parameters: Record<string, string | number | boolean>;
  risk_level: RiskLevel;
  requires_approval: boolean;
  source_recommendation: string;
  reason: string;
  expected_effect: string;
  evidence_references: string[];
  status: RemediationStatus;
  created_at: string;
  expires_at: string;
  policy: {
    outcome: string;
    policy_version: string;
    reason_codes: string[];
    violations: { code: string; rule: string; detail: string }[];
  };
  approval: RemediationApproval | null;
  execution: RemediationExecution | null;
  verification: RemediationVerification | null;
}

export interface RemediationListResponse {
  remediations: RemediationView[];
  count: number;
}

// ----------------------------------------------------------------- detector
export interface ModelInfo {
  model_loaded: boolean;
  source?: string;
  model_version?: string;
  model_type?: string;
  source_details?: Record<string, unknown>;
}

export interface InferenceStats {
  total_inferences: number;
  total_anomalies: number;
  anomaly_rate: number;
  avg_latency_ms: number;
  last_latency_ms: number;
  min_latency_ms: number;
  max_latency_ms: number;
  last_inference_time: string | null;
}

export interface ReadyStats {
  inference_stats: InferenceStats;
  uptime_seconds: number;
  healthy: boolean;
  health_reasons: string[];
}
