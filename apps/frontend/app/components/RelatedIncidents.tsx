import Link from "next/link";
import type { RelatedIncidentOut } from "@/app/lib/types";
import { Badge } from "./Badge";

export function RelatedIncidents({ related }: { related: RelatedIncidentOut[] }) {
  if (related.length === 0) return null;
  return (
    <section className="rounded-lg border border-gray-800">
      <h2 className="border-b border-gray-800 px-4 py-2.5 text-sm font-medium text-gray-300">
        Related incidents (cross-service)
      </h2>
      <ul className="divide-y divide-gray-800">
        {related.map((r) => (
          <li key={r.id}>
            <Link
              href={`/incidents/${r.id}`}
              className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm hover:bg-gray-900/50"
            >
              <span className="text-gray-300">
                {r.service}
                <span className="text-gray-600"> · {r.environment}</span>
              </span>
              <span className="flex items-center gap-2">
                <Badge kind="severity" value={r.severity} />
                <Badge kind="incidentStatus" value={r.status} />
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
