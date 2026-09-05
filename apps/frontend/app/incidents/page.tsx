"use client";

import { useEffect, useState } from "react";
import { incidents } from "@/app/lib/api";
import type { IncidentSummary } from "@/app/lib/types";
import { IncidentTable } from "@/app/components/IncidentTable";

const STATUSES = ["OPEN", "ACKNOWLEDGED", "INVESTIGATING", "MITIGATING", "RESOLVED"];
const SEVERITIES = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"];

export default function IncidentsPage() {
  const [rows, setRows] = useState<IncidentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [service, setService] = useState("");
  const [status, setStatus] = useState("");
  const [severity, setSeverity] = useState("");

  useEffect(() => {
    const t = setTimeout(() => {
      setLoading(true);
      incidents
        .list({ service: service || undefined, status: status || undefined, severity: severity || undefined, limit: "200" })
        .then((r) => {
          setRows(r);
          setError(null);
        })
        .catch((e) => setError(e instanceof Error ? e.message : String(e)))
        .finally(() => setLoading(false));
    }, 250);
    return () => clearTimeout(t);
  }, [service, status, severity]);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-gray-100">Incidents</h1>

      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-gray-800 bg-gray-950/40 p-3 text-sm">
        <input
          value={service}
          onChange={(e) => setService(e.target.value)}
          placeholder="service"
          className="rounded-md border border-gray-700 bg-gray-900 px-2 py-1.5 text-gray-100 placeholder:text-gray-600"
        />
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="rounded-md border border-gray-700 bg-gray-900 px-2 py-1.5 text-gray-100"
        >
          <option value="">any status</option>
          {STATUSES.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
        <select
          value={severity}
          onChange={(e) => setSeverity(e.target.value)}
          className="rounded-md border border-gray-700 bg-gray-900 px-2 py-1.5 text-gray-100"
        >
          <option value="">any severity</option>
          {SEVERITIES.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
        {(service || status || severity) && (
          <button
            onClick={() => {
              setService("");
              setStatus("");
              setSeverity("");
            }}
            className="text-gray-400 hover:text-gray-100"
          >
            clear
          </button>
        )}
        <span className="ml-auto text-xs text-gray-500">{rows.length} shown</span>
      </div>

      <div className="rounded-lg border border-gray-800">
        <IncidentTable incidents={rows} loading={loading} error={error} />
      </div>
    </div>
  );
}
