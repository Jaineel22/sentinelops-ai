"use client";

import { useEffect, useState } from "react";
import { detector } from "@/app/lib/api";
import type { ModelInfo, ReadyStats } from "@/app/lib/types";
import { fmtRelative } from "@/app/lib/format";

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-gray-500">{label}</div>
      <div className="text-sm text-gray-200">{value}</div>
    </div>
  );
}

export default function ModelsPage() {
  const [model, setModel] = useState<ModelInfo | null>(null);
  const [stats, setStats] = useState<ReadyStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([detector.modelInfo(), detector.readyStats()])
      .then(([m, s]) => {
        setModel(m);
        setStats(s);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <div className="space-y-5">
      <h1 className="text-xl font-semibold text-gray-100">Model performance</h1>

      {error && (
        <p className="text-sm text-red-400">
          anomaly-detector unreachable ({error}). Start it with{" "}
          <code className="text-gray-400">make run-detector</code> or{" "}
          <code className="text-gray-400">docker compose up anomaly-detector</code>.
        </p>
      )}

      {model && (
        <section className="rounded-lg border border-gray-800 p-4">
          <h2 className="mb-3 text-sm font-medium text-gray-300">Live model provenance (/model-info)</h2>
          {model.model_loaded ? (
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="Version" value={model.model_version} />
              <Field label="Type" value={model.model_type} />
              <Field label="Source" value={model.source} />
              {model.source_details && Object.keys(model.source_details).length > 0 && (
                <div className="sm:col-span-3">
                  <div className="text-xs uppercase tracking-wide text-gray-500">Source details</div>
                  <pre className="mt-1 overflow-x-auto rounded bg-gray-900/70 p-2 text-xs text-gray-300">
                    {JSON.stringify(model.source_details, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          ) : (
            <p className="text-sm text-gray-500">No model is loaded yet.</p>
          )}
        </section>
      )}

      {stats && (
        <section className="rounded-lg border border-gray-800 p-4">
          <div className="mb-3 flex items-center gap-2">
            <h2 className="text-sm font-medium text-gray-300">Inference stats (/ready/stats)</h2>
            <span
              className={`rounded-md px-2 py-0.5 text-xs ${
                stats.healthy
                  ? "bg-green-500/15 text-green-300"
                  : "bg-amber-500/15 text-amber-300"
              }`}
            >
              {stats.healthy ? "healthy" : "degraded"}
            </span>
          </div>
          <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-4">
            <Field label="Total inferences" value={stats.inference_stats.total_inferences} />
            <Field label="Anomalies" value={stats.inference_stats.total_anomalies} />
            <Field label="Anomaly rate" value={`${stats.inference_stats.anomaly_rate}%`} />
            <Field label="Uptime (s)" value={stats.uptime_seconds} />
            <Field label="Avg latency" value={`${stats.inference_stats.avg_latency_ms} ms`} />
            <Field label="p-last latency" value={`${stats.inference_stats.last_latency_ms} ms`} />
            <Field label="min / max latency" value={`${stats.inference_stats.min_latency_ms} / ${stats.inference_stats.max_latency_ms} ms`} />
            <Field label="Last inference" value={fmtRelative(stats.inference_stats.last_inference_time)} />
          </div>
          {stats.health_reasons.length > 0 && (
            <p className="mt-3 text-xs text-amber-300/90">
              {stats.health_reasons.join(" · ")}
            </p>
          )}
        </section>
      )}

      <p className="text-xs text-gray-600">
        MLflow experiment metrics, the model registry, and PSI drift reports are CLI / MLflow-server
        surfaces, not HTTP APIs on this service — see <code className="text-gray-500">make phase6-summary</code>,{" "}
        <code className="text-gray-500">python -m ml.mlops get-champion</code>, and the MLflow UI on{" "}
        <code className="text-gray-500">:5000</code>.
      </p>
    </div>
  );
}
