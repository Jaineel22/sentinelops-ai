"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, investigations } from "@/app/lib/api";
import type { InvestigationDetail } from "@/app/lib/types";
import { fmtDuration, titleCase } from "@/app/lib/format";
import { Badge } from "./Badge";

function Panel({ children }: { children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-gray-800">
      <h2 className="border-b border-gray-800 px-4 py-2.5 text-sm font-medium text-gray-300">
        Root cause analysis
      </h2>
      <div className="p-4">{children}</div>
    </section>
  );
}

export function RcaReport({ incidentId }: { incidentId: string }) {
  const [state, setState] = useState<"loading" | "none" | "ready" | "error">("loading");
  const [detail, setDetail] = useState<InvestigationDetail | null>(null);
  const [err, setErr] = useState<string>("");
  const [starting, setStarting] = useState(false);

  const load = useCallback(async () => {
    setState("loading");
    try {
      setDetail(await investigations.forIncident(incidentId));
      setState("ready");
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setState("none");
      } else {
        setErr(e instanceof Error ? e.message : String(e));
        setState("error");
      }
    }
  }, [incidentId]);

  useEffect(() => {
    void load();
  }, [load]);

  const start = async () => {
    setStarting(true);
    try {
      setDetail(await investigations.create(incidentId));
      setState("ready");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setState("error");
    } finally {
      setStarting(false);
    }
  };

  if (state === "loading") return <Panel>Loading investigation…</Panel>;
  if (state === "error") return <Panel><span className="text-red-400">{err}</span></Panel>;
  if (state === "none")
    return (
      <Panel>
        <div className="flex items-center justify-between gap-4">
          <p className="text-sm text-gray-500">No investigation has run for this incident.</p>
          <button
            onClick={start}
            disabled={starting}
            className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50"
          >
            {starting ? "Starting…" : "Start investigation"}
          </button>
        </div>
      </Panel>
    );

  const inv = detail!.investigation;
  const report = detail!.report;
  const terminalNoReport = report === null && inv.status !== "PENDING";

  return (
    <section className="rounded-lg border border-gray-800">
      <div className="flex items-center justify-between border-b border-gray-800 px-4 py-2.5">
        <h2 className="text-sm font-medium text-gray-300">Root cause analysis</h2>
        <div className="flex items-center gap-2 text-xs text-gray-500">
          <Badge kind="investigationStatus" value={inv.status} />
          <span>{inv.mode} mode</span>
          <span>· {inv.tool_call_count} tool calls</span>
          <span>· {inv.evidence_count} evidence</span>
          <button onClick={() => void load()} className="text-blue-400 hover:underline">
            refresh
          </button>
        </div>
      </div>
      <div className="space-y-4 p-4 text-sm">
        {!report && !terminalNoReport && (
          <p className="text-gray-500">
            Investigation is <span className="text-gray-300">{inv.status}</span> — refresh for the
            report.
          </p>
        )}
        {terminalNoReport && (
          <p className="text-amber-300/90">
            Investigation ended <span className="font-medium">{inv.status}</span>
            {inv.termination_reason ? ` — ${inv.termination_reason}` : ""}. No structured report was
            produced.
          </p>
        )}

        {report && (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Badge kind="investigationStatus" value={report.status} />
              <Badge kind="confidence" value={report.overall_confidence}>
                confidence {report.overall_confidence}
              </Badge>
            </div>

            <div>
              <h3 className="text-xs uppercase tracking-wide text-gray-500">Summary</h3>
              <p className="mt-1 text-gray-200">{report.summary}</p>
            </div>

            <div>
              <h3 className="text-xs uppercase tracking-wide text-gray-500">Root cause</h3>
              {report.root_cause ? (
                <div className="mt-1 rounded-md bg-gray-900/70 p-3">
                  <p className="text-gray-100">{report.root_cause.statement}</p>
                  <p className="mt-1 text-xs text-gray-500">
                    {report.root_cause.reasoning_summary}
                  </p>
                  <p className="mt-1 text-xs text-gray-600">
                    evidence: {report.root_cause.evidence_ids.join(", ") || "—"}
                  </p>
                </div>
              ) : (
                <p className="mt-1 text-gray-400">
                  Undetermined — insufficient evidence (not fabricated).
                </p>
              )}
            </div>

            {report.findings.length > 0 && (
              <div>
                <h3 className="text-xs uppercase tracking-wide text-gray-500">Findings</h3>
                <ul className="mt-1 list-inside list-disc space-y-1 text-gray-300">
                  {report.findings.map((f) => (
                    <li key={f.id}>{f.statement}</li>
                  ))}
                </ul>
              </div>
            )}

            {report.hypotheses.length > 0 && (
              <div>
                <h3 className="text-xs uppercase tracking-wide text-gray-500">Hypotheses</h3>
                <ul className="mt-1 space-y-1.5">
                  {report.hypotheses.map((h) => (
                    <li
                      key={h.id}
                      className="flex items-start gap-2 rounded-md bg-gray-900/70 p-2"
                    >
                      <Badge kind="verdict" value={h.verdict} />
                      <span className="text-gray-300">{h.statement}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div>
              <h3 className="text-xs uppercase tracking-wide text-gray-500">Recommended action</h3>
              <div className="mt-1 flex flex-wrap items-center gap-2 rounded-md bg-gray-900/70 p-2">
                <span className="font-medium text-amber-300">
                  {titleCase(report.recommended_action.action_type)}
                </span>
                <span className="text-gray-300">{report.recommended_action.description}</span>
                <span className="ml-auto text-xs text-orange-300">requires human approval</span>
              </div>
            </div>

            {report.unavailable_evidence_sources.length > 0 && (
              <div>
                <h3 className="text-xs uppercase tracking-wide text-gray-500">
                  Unavailable evidence sources
                </h3>
                <div className="mt-1 flex flex-wrap gap-1">
                  {report.unavailable_evidence_sources.map((s) => (
                    <span
                      key={s}
                      className="rounded bg-gray-800 px-1.5 py-0.5 text-xs text-gray-400"
                    >
                      {s.split(":")[0]}
                    </span>
                  ))}
                </div>
              </div>
            )}

            <p className="text-xs text-gray-600">
              {inv.step_count} steps · {fmtDuration(
                inv.completed_at && inv.started_at
                  ? (new Date(inv.completed_at).getTime() - new Date(inv.started_at).getTime()) /
                      1000
                  : 0,
              )}{" "}
              · uncertainty: {report.uncertainty}
            </p>
          </>
        )}
      </div>
    </section>
  );
}
