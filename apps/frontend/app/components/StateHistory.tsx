import type { TransitionOut } from "@/app/lib/types";
import { fmtTime } from "@/app/lib/format";

export function StateHistory({ history }: { history: TransitionOut[] }) {
  if (history.length === 0) return null;
  return (
    <section className="rounded-lg border border-gray-800">
      <h2 className="border-b border-gray-800 px-4 py-2.5 text-sm font-medium text-gray-300">
        Lifecycle history
      </h2>
      <ol className="divide-y divide-gray-800">
        {history.map((h, idx) => (
          <li key={idx} className="flex items-start gap-3 px-4 py-2.5 text-sm">
            <span className="mt-0.5 font-mono text-xs text-gray-500">{fmtTime(h.created_at)}</span>
            <span className="text-gray-300">
              {h.from_status ? `${h.from_status} → ` : ""}
              <span className="text-gray-100">{h.to_status}</span>
              <span className="text-gray-600"> · {h.actor}</span>
              <span className="block text-xs text-gray-500">{h.reason}</span>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
