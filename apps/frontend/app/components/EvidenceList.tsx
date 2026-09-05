"use client";

import { useState } from "react";
import type { EvidenceOut } from "@/app/lib/types";
import { fmtTime } from "@/app/lib/format";

export function EvidenceList({ evidence }: { evidence: EvidenceOut[] }) {
  const [open, setOpen] = useState<string | null>(null);

  return (
    <section className="rounded-lg border border-gray-800">
      <h2 className="border-b border-gray-800 px-4 py-2.5 text-sm font-medium text-gray-300">
        Anomaly evidence ({evidence.length})
      </h2>
      <ul className="divide-y divide-gray-800">
        {evidence.map((e) => {
          const expanded = open === e.event_id;
          return (
            <li key={e.event_id} className="px-4 py-2.5 text-sm">
              <button
                onClick={() => setOpen(expanded ? null : e.event_id)}
                className="flex w-full items-center justify-between gap-4 text-left"
              >
                <span className="flex items-center gap-3">
                  <span className="font-mono text-xs text-gray-500">
                    {e.event_id.slice(0, 12)}
                  </span>
                  <span className="text-gray-300">
                    score {e.anomaly_score.toFixed(3)}{" "}
                    <span className="text-gray-600">/ thr {e.threshold.toFixed(2)}</span>
                  </span>
                  {e.abnormal_signals.length > 0 && (
                    <span className="text-amber-300/90">
                      {e.abnormal_signals.join(", ")}
                    </span>
                  )}
                </span>
                <span className="shrink-0 text-xs text-gray-500">
                  {fmtTime(e.occurred_at)} {expanded ? "▲" : "▼"}
                </span>
              </button>
              {expanded && (
                <div className="mt-3 space-y-2 border-t border-gray-800 pt-3 text-xs">
                  <div className="text-gray-400">
                    <span className="text-gray-500">window </span>
                    {fmtTime(e.window_start)} → {fmtTime(e.window_end)}
                    <span className="text-gray-600">
                      {" "}
                      · {e.detector} {e.detector_version}
                    </span>
                  </div>
                  <div className="text-gray-400">
                    <span className="text-gray-500">correlation reason: </span>
                    {e.correlation_reason}
                  </div>
                  <div className="grid grid-cols-2 gap-1 sm:grid-cols-3">
                    {Object.entries(e.signals).map(([k, v]) => (
                      <div
                        key={k}
                        className="flex justify-between rounded bg-gray-900/70 px-2 py-1 font-mono"
                      >
                        <span className="text-gray-500">{k}</span>
                        <span className="text-gray-300">{Number(v).toFixed(3)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
