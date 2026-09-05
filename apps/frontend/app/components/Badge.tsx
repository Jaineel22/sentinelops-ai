import type { ReactNode } from "react";

// Full literal class strings so Tailwind's JIT keeps them.
const TONE: Record<string, string> = {
  blue: "bg-blue-500/15 text-blue-300 ring-1 ring-inset ring-blue-500/30",
  sky: "bg-sky-500/15 text-sky-300 ring-1 ring-inset ring-sky-500/30",
  violet: "bg-violet-500/15 text-violet-300 ring-1 ring-inset ring-violet-500/30",
  amber: "bg-amber-500/15 text-amber-300 ring-1 ring-inset ring-amber-500/30",
  orange: "bg-orange-500/15 text-orange-300 ring-1 ring-inset ring-orange-500/30",
  red: "bg-red-500/15 text-red-300 ring-1 ring-inset ring-red-500/30",
  green: "bg-green-500/15 text-green-300 ring-1 ring-inset ring-green-500/30",
  gray: "bg-gray-500/15 text-gray-300 ring-1 ring-inset ring-gray-500/30",
};

const SEVERITY_TONE: Record<string, string> = {
  INFO: "blue",
  LOW: "sky",
  MEDIUM: "amber",
  HIGH: "orange",
  CRITICAL: "red",
};

const INCIDENT_STATUS_TONE: Record<string, string> = {
  OPEN: "blue",
  ACKNOWLEDGED: "violet",
  INVESTIGATING: "amber",
  MITIGATING: "orange",
  RESOLVED: "green",
};

const INVESTIGATION_STATUS_TONE: Record<string, string> = {
  PENDING: "gray",
  PLANNING: "blue",
  COLLECTING_EVIDENCE: "blue",
  ANALYZING: "amber",
  VERIFYING: "amber",
  COMPLETED: "green",
  INSUFFICIENT_EVIDENCE: "amber",
  FAILED: "red",
  TIMED_OUT: "red",
};

const REMEDIATION_STATUS_TONE: Record<string, string> = {
  PROPOSED: "gray",
  POLICY_EVALUATION: "gray",
  PENDING_APPROVAL: "amber",
  APPROVED: "blue",
  EXECUTING: "blue",
  EXECUTED: "green",
  VERIFYING: "amber",
  BLOCKED: "red",
  REJECTED: "gray",
  EXPIRED: "gray",
  EXECUTION_FAILED: "red",
  RECOVERED: "green",
  RECOVERY_FAILED: "red",
};

const VERDICT_TONE: Record<string, string> = {
  SUPPORTED: "green",
  REFUTED: "red",
  UNVERIFIED: "gray",
  CONFLICTING: "amber",
};

const CONFIDENCE_TONE: Record<string, string> = {
  HIGH: "green",
  MEDIUM: "amber",
  LOW: "red",
  UNKNOWN: "gray",
};

export type BadgeKind =
  | "severity"
  | "incidentStatus"
  | "investigationStatus"
  | "remediationStatus"
  | "verdict"
  | "confidence"
  | "neutral";

const MAP: Record<BadgeKind, Record<string, string>> = {
  severity: SEVERITY_TONE,
  incidentStatus: INCIDENT_STATUS_TONE,
  investigationStatus: INVESTIGATION_STATUS_TONE,
  remediationStatus: REMEDIATION_STATUS_TONE,
  verdict: VERDICT_TONE,
  confidence: CONFIDENCE_TONE,
  neutral: {},
};

export function Badge({
  kind,
  value,
  children,
}: {
  kind: BadgeKind;
  value?: string;
  children?: ReactNode;
}) {
  const tone = TONE[MAP[kind][value ?? ""] ?? "gray"];
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${tone}`}
    >
      {children ?? value?.replace(/_/g, " ")}
    </span>
  );
}
