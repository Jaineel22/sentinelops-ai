import Link from "next/link";
import type { IncidentSummary } from "@/app/lib/types";
import { fmtRelative } from "@/app/lib/format";
import { Badge } from "./Badge";

export function IncidentTable({
  incidents,
  loading,
  error,
}: {
  incidents: IncidentSummary[];
  loading?: boolean;
  error?: string | null;
}) {
  if (loading) return <p className="p-6 text-sm text-gray-500">Loading incidents…</p>;
  if (error) return <p className="p-6 text-sm text-red-400">Failed to load incidents: {error}</p>;
  if (incidents.length === 0)
    return <p className="p-6 text-sm text-gray-500">No incidents match.</p>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-800 text-left text-xs uppercase tracking-wide text-gray-500">
            <th className="px-4 py-2 font-medium">Severity</th>
            <th className="px-4 py-2 font-medium">Status</th>
            <th className="px-4 py-2 font-medium">Title</th>
            <th className="px-4 py-2 font-medium">Service</th>
            <th className="px-4 py-2 font-medium text-right">Anomalies</th>
            <th className="px-4 py-2 font-medium">Last evidence</th>
          </tr>
        </thead>
        <tbody>
          {incidents.map((i) => (
            <tr key={i.id} className="border-b border-gray-800/70 hover:bg-gray-900/50">
              <td className="px-4 py-2">
                <Badge kind="severity" value={i.severity} />
              </td>
              <td className="px-4 py-2">
                <Badge kind="incidentStatus" value={i.status} />
              </td>
              <td className="max-w-md px-4 py-2">
                <Link
                  href={`/incidents/${i.id}`}
                  className="text-gray-200 hover:text-white hover:underline"
                >
                  {i.title}
                </Link>
              </td>
              <td className="px-4 py-2 text-gray-400">
                {i.service}
                <span className="text-gray-600"> · {i.environment}</span>
              </td>
              <td className="px-4 py-2 text-right tabular-nums text-gray-300">
                {i.anomaly_count}
              </td>
              <td className="px-4 py-2 text-gray-500" title={i.last_evidence_at}>
                {fmtRelative(i.last_evidence_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
