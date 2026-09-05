"use client";

import { useCallback, useEffect, useState } from "react";
import { remediations } from "@/app/lib/api";
import type { ApproverRole, RemediationView } from "@/app/lib/types";
import { fmtTime, titleCase } from "@/app/lib/format";
import { hasRole } from "@/app/lib/auth";
import { Badge } from "./Badge";

const AUTO_REFRESH_MS = 15_000;

const ROLES: ApproverRole[] = ["OPERATOR", "INCIDENT_RESPONDER", "ADMINISTRATOR"];

function ApprovalForm({
  rem,
  onDone,
}: {
  rem: RemediationView;
  onDone: () => void;
}) {
  const [identity, setIdentity] = useState("");
  const [role, setRole] = useState<ApproverRole>("INCIDENT_RESPONDER");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [err, setErr] = useState("");

  const submit = async (decision: "approve" | "reject") => {
    if (!identity.trim()) {
      setErr("approver identity is required");
      return;
    }
    setBusy(decision);
    setErr("");
    try {
      const body = { approver_identity: identity.trim(), approver_role: role, reason };
      await (decision === "approve"
        ? remediations.approve(rem.remediation_id, body)
        : remediations.reject(rem.remediation_id, body));
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="mt-3 space-y-2 border-t border-gray-800 pt-3">
      <div className="grid gap-2 sm:grid-cols-2">
        <input
          value={identity}
          onChange={(e) => setIdentity(e.target.value)}
          placeholder="approver identity (e.g. alice@corp)"
          className="rounded-md border border-gray-700 bg-gray-900 px-2 py-1.5 text-sm text-gray-100 placeholder:text-gray-600"
        />
        <select
          value={role}
          onChange={(e) => setRole(e.target.value as ApproverRole)}
          className="rounded-md border border-gray-700 bg-gray-900 px-2 py-1.5 text-sm text-gray-100"
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {titleCase(r)}
            </option>
          ))}
        </select>
      </div>
      <input
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="reason (optional)"
        className="w-full rounded-md border border-gray-700 bg-gray-900 px-2 py-1.5 text-sm text-gray-100 placeholder:text-gray-600"
      />
      {err && <p className="text-xs text-red-400">{err}</p>}
      <div className="flex gap-2">
        <button
          onClick={() => void submit("approve")}
          disabled={!!busy}
          className="rounded-md bg-green-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-500 disabled:opacity-50"
        >
          {busy === "approve" ? "Approving…" : "Approve"}
        </button>
        <button
          onClick={() => void submit("reject")}
          disabled={!!busy}
          className="rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-500 disabled:opacity-50"
        >
          {busy === "reject" ? "Rejecting…" : "Reject"}
        </button>
      </div>
    </div>
  );
}

function ExecuteControls({ rem, onDone }: { rem: RemediationView; onDone: () => void }) {
  const [dryRun, setDryRun] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const run = async () => {
    setBusy(true);
    setErr("");
    try {
      await remediations.execute(rem.remediation_id, dryRun);
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 flex items-center gap-3 border-t border-gray-800 pt-3">
      <label className="flex items-center gap-1.5 text-sm text-gray-400">
        <input
          type="checkbox"
          checked={dryRun}
          onChange={(e) => setDryRun(e.target.checked)}
          className="rounded border-gray-700 bg-gray-900"
        />
        dry run
      </label>
      <button
        onClick={() => void run()}
        disabled={busy}
        className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50"
      >
        {busy ? "Executing…" : dryRun ? "Preview execution" : "Execute"}
      </button>
      {err && <p className="text-xs text-red-400">{err}</p>}
    </div>
  );
}

function RemediationCard({ rem, onChange }: { rem: RemediationView; onChange: () => void }) {
  const canApprove = hasRole("approver");
  return (
    <div className="px-4 py-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-gray-200">{titleCase(rem.action_type)}</span>
        <Badge kind="remediationStatus" value={rem.status} />
        <span className="text-xs text-gray-500">
          risk {rem.risk_level.toLowerCase()} · target {rem.target.service_name} ·{" "}
          {rem.target.environment}
        </span>
        <span className="ml-auto text-xs text-gray-600">expires {fmtTime(rem.expires_at)}</span>
      </div>
      <p className="mt-1 text-gray-400">{rem.reason || rem.expected_effect}</p>

      <p className="mt-1 text-xs text-gray-600">
        policy: <span className="text-gray-400">{rem.policy.outcome}</span>
        {rem.policy.reason_codes.length > 0 && ` (${rem.policy.reason_codes.join(", ")})`}
      </p>

      {rem.approval && (
        <p className="mt-1 text-xs text-gray-500">
          {rem.approval.decision} by {rem.approval.approver_identity} (
          {titleCase(rem.approval.approver_role)}) · {fmtTime(rem.approval.decided_at)}
          {rem.approval.reason ? ` — ${rem.approval.reason}` : ""}
        </p>
      )}
      {rem.execution && (
        <p className="mt-1 text-xs text-gray-500">
          execution: {rem.execution.status}
          {rem.execution.dry_run ? " (dry run)" : ""}
          {rem.execution.detail ? ` — ${rem.execution.detail}` : ""}
        </p>
      )}
      {rem.verification && (
        <p className="mt-1 text-xs text-gray-500">
          recovery: {rem.verification.status}
          {rem.verification.failure_reason ? ` — ${rem.verification.failure_reason}` : ""}
        </p>
      )}

      {rem.status === "PENDING_APPROVAL" &&
        (canApprove ? (
          <ApprovalForm rem={rem} onDone={onChange} />
        ) : (
          <p className="mt-3 border-t border-gray-800 pt-3 text-xs text-gray-600">
            requires the <span className="text-gray-400">approver</span> role to approve or reject
          </p>
        ))}
      {rem.status === "APPROVED" &&
        (canApprove ? (
          <ExecuteControls rem={rem} onDone={onChange} />
        ) : (
          <p className="mt-3 border-t border-gray-800 pt-3 text-xs text-gray-600">
            requires the <span className="text-gray-400">approver</span> role to execute
          </p>
        ))}
    </div>
  );
}

export function RemediationPanel({ incidentId }: { incidentId: string }) {
  const [rows, setRows] = useState<RemediationView[] | null>(null);
  const [err, setErr] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);

  const load = useCallback(async () => {
    try {
      const res = await remediations.forIncident(incidentId);
      setRows(res.remediations);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [incidentId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => void load(), AUTO_REFRESH_MS);
    return () => clearInterval(id);
  }, [autoRefresh, load]);

  return (
    <section className="rounded-lg border border-gray-800">
      <div className="flex items-center justify-between border-b border-gray-800 px-4 py-2.5">
        <h2 className="text-sm font-medium text-gray-300">Remediation</h2>
        <div className="flex items-center gap-3 text-xs">
          <label className="flex items-center gap-1.5 text-gray-500">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="rounded border-gray-700 bg-gray-900"
            />
            auto-refresh (15s)
          </label>
          <button onClick={() => void load()} className="text-blue-400 hover:underline">
            refresh
          </button>
        </div>
      </div>
      {err && <p className="px-4 py-3 text-sm text-red-400">{err}</p>}
      {!err && rows === null && <p className="px-4 py-3 text-sm text-gray-500">Loading…</p>}
      {rows?.length === 0 && (
        <p className="px-4 py-3 text-sm text-gray-500">
          No remediation has been proposed for this incident. The remediation-controller creates one
          from an RCA recommendation (see <code className="text-gray-400">scripts/remediation_e2e_scenario.py</code>).
        </p>
      )}
      {rows && rows.length > 0 && (
        <div className="divide-y divide-gray-800">
          {rows.map((r) => (
            <RemediationCard key={r.remediation_id} rem={r} onChange={load} />
          ))}
        </div>
      )}
    </section>
  );
}
