"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { detector, incidents } from "@/app/lib/api";
import type { IncidentSummary, ModelInfo, ReadyStats } from "@/app/lib/types";
import { IncidentTable } from "@/app/components/IncidentTable";
import { fmtRelative } from "@/app/lib/format";

const AUTO_REFRESH_MS = 10_000;

function Stat({ label, value, tone }: { label: string; value: string | number; tone?: string }) {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
      <div className={`text-2xl font-semibold ${tone ?? "text-gray-100"}`}>{value}</div>
      <div className="text-xs uppercase tracking-wide text-gray-500">{label}</div>
    </div>
  );
}

export default function DashboardPage() {
  const [rows, setRows] = useState<IncidentSummary[]>([]);
  const [model, setModel] = useState<ModelInfo | null>(null);
  const [stats, setStats] = useState<ReadyStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const loadedOnce = useRef(false);

  const refresh = useCallback(async () => {
    if (!loadedOnce.current) setLoading(true);
    try {
      setRows(await incidents.list({ limit: "200" }));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
      loadedOnce.current = true;
    }
    detector
      .modelInfo()
      .then(setModel)
      .catch(() => setModel(null));
    detector
      .readyStats()
      .then(setStats)
      .catch(() => setStats(null));
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const timer = setInterval(() => void refresh(), AUTO_REFRESH_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  const active = rows.filter((i) => i.status !== "RESOLVED");
  const critical = active.filter((i) => i.severity === "CRITICAL");
  const recent = [...rows]
    .sort((a, b) => b.last_evidence_at.localeCompare(a.last_evidence_at))
    .slice(0, 10);

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-gray-100">Dashboard</h1>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Incidents (total)" value={rows.length} />
        <Stat label="Active" value={active.length} tone="text-amber-300" />
        <Stat label="Critical active" value={critical.length} tone="text-red-400" />
        <Stat
          label="Detector anomaly rate"
          value={stats ? `${stats.inference_stats.anomaly_rate}%` : "—"}
          tone={stats && !stats.healthy ? "text-amber-300" : "text-gray-100"}
        />
      </div>

      {model?.model_loaded && (
        <p className="text-xs text-gray-500">
          Model <span className="text-gray-300">{model.model_version}</span> ({model.model_type},{" "}
          {model.source}) ·{" "}
          {stats
            ? `${stats.inference_stats.total_inferences} inferences, last ${fmtRelative(
                stats.inference_stats.last_inference_time,
              )}`
            : "detector stats unavailable"}
          <span className="text-gray-700"> · refreshes every {AUTO_REFRESH_MS / 1000}s</span>
        </p>
      )}

      <section className="rounded-lg border border-gray-800">
        <div className="flex items-center justify-between border-b border-gray-800 px-4 py-2.5">
          <h2 className="text-sm font-medium text-gray-300">Recent incidents</h2>
          <Link href="/incidents" className="text-xs text-blue-400 hover:underline">
            all incidents →
          </Link>
        </div>
        <IncidentTable incidents={recent} loading={loading} error={error} />
      </section>
    </div>
  );
}
