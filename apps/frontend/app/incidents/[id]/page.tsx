"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { incidents } from "@/app/lib/api";
import type { IncidentDetail } from "@/app/lib/types";
import { fmtTime, fmtDuration } from "@/app/lib/format";
import { Badge } from "@/app/components/Badge";
import { EvidenceList } from "@/app/components/EvidenceList";
import { StateHistory } from "@/app/components/StateHistory";
import { RelatedIncidents } from "@/app/components/RelatedIncidents";
import { RcaReport } from "@/app/components/RcaReport";
import { RemediationPanel } from "@/app/components/RemediationPanel";
import { currentUser, hasRole } from "@/app/lib/auth";

const AUTO_REFRESH_MS = 15_000;

export default function IncidentDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [inc, setInc] = useState<IncidentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState(false);

  const load = useCallback(async () => {
    try {
      setInc(await incidents.get(id));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  // Auto-refresh: an investigation or a remediation for this incident can
  // change state in the background (e.g. another operator acting on it).
  useEffect(() => {
    const timer = setInterval(() => void load(), AUTO_REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  const act = async (fn: () => Promise<IncidentDetail>) => {
    setActing(true);
    try {
      setInc(await fn());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setActing(false);
    }
  };

  if (error) return <p className="text-sm text-red-400">Failed to load incident: {error}</p>;
  if (!inc) return <p className="text-sm text-gray-500">Loading…</p>;

  const canAct = hasRole("approver");
  const canAck = canAct && inc.status === "OPEN";
  const canResolve = canAct && inc.status !== "RESOLVED";
  const actor = currentUser()?.username ?? "dashboard";

  return (
    <div className="space-y-5">
      <Link href="/incidents" className="text-xs text-blue-400 hover:underline">
        ← incidents
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-gray-100">{inc.title}</h1>
          <p className="mt-1 text-sm text-gray-500">
            {inc.service} · {inc.environment} · {inc.anomaly_count} anomalies ·{" "}
            {inc.distinct_abnormal_signals} distinct signals · {fmtDuration(inc.duration_seconds)}
          </p>
          <p className="mt-0.5 text-xs text-gray-600">
            started {fmtTime(inc.started_at)} · last evidence {fmtTime(inc.last_evidence_at)}
            {inc.resolved_at ? ` · resolved ${fmtTime(inc.resolved_at)}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge kind="severity" value={inc.severity} />
          <Badge kind="incidentStatus" value={inc.status} />
        </div>
      </div>

      {inc.severity_reasons.length > 0 && (
        <p className="text-xs text-gray-500">
          severity: {inc.severity_reasons.join("; ")}
        </p>
      )}

      <div className="flex items-center gap-2">
        <button
          disabled={!canAck || acting}
          onClick={() => void act(() => incidents.acknowledge(id))}
          className="rounded-md border border-gray-700 px-3 py-1.5 text-sm text-gray-200 hover:bg-gray-800 disabled:opacity-40"
        >
          Acknowledge
        </button>
        <button
          disabled={!canResolve || acting}
          onClick={() => void act(() => incidents.resolve(id, "resolved via dashboard", actor))}
          className="rounded-md border border-gray-700 px-3 py-1.5 text-sm text-gray-200 hover:bg-gray-800 disabled:opacity-40"
        >
          Resolve
        </button>
        {!canAct && <span className="text-xs text-gray-600">requires the approver role</span>}
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="space-y-5 lg:col-span-2">
          {inc.evidence.length > 0 && <EvidenceList evidence={inc.evidence} />}
          <RcaReport incidentId={id} />
          <RemediationPanel incidentId={id} />
        </div>
        <div className="space-y-5">
          <RelatedIncidents related={inc.related_incidents} />
          <StateHistory history={inc.history} />
        </div>
      </div>
    </div>
  );
}
