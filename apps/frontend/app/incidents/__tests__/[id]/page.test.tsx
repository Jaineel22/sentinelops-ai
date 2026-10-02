import { act, render, screen } from "@testing-library/react";
import IncidentDetailPage from "@/app/incidents/[id]/page";
import { incidents } from "@/app/lib/api";
import { hasRole } from "@/app/lib/auth";

jest.mock("@/app/lib/api", () => ({
  incidents: { get: jest.fn(), acknowledge: jest.fn(), resolve: jest.fn() },
}));
jest.mock("@/app/lib/auth", () => ({
  hasRole: jest.fn(),
  currentUser: () => ({ username: "alice", role: "approver", disabled: false }),
}));
jest.mock("next/link", () => ({ __esModule: true, default: ({ children }: { children: React.ReactNode }) => children }));
jest.mock("@/app/components/EvidenceList", () => ({ EvidenceList: () => null }));
jest.mock("@/app/components/StateHistory", () => ({ StateHistory: () => null }));
jest.mock("@/app/components/RelatedIncidents", () => ({ RelatedIncidents: () => null }));
jest.mock("@/app/components/RcaReport", () => ({ RcaReport: () => null }));
jest.mock("@/app/components/RemediationPanel", () => ({ RemediationPanel: () => null }));
jest.mock("@/app/components/Badge", () => ({ Badge: () => null }));

const incident = {
  id: "i1",
  title: "Latency",
  service: "orders",
  environment: "dev",
  status: "OPEN",
  severity: "HIGH",
  anomaly_count: 1,
  distinct_abnormal_signals: 1,
  started_at: "2026-01-01T00:00:00Z",
  last_evidence_at: "2026-01-01T00:00:00Z",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  resolved_at: null,
  severity_reasons: [],
  evidence: [],
  history: [],
  related_incidents: [],
  duration_seconds: 1,
};

describe("IncidentDetail RBAC", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (incidents.get as jest.Mock).mockResolvedValue(incident);
  });

  it("shows lifecycle controls for approvers", async () => {
    (hasRole as jest.Mock).mockReturnValue(true);
    await act(async () => {
      render(<IncidentDetailPage params={Promise.resolve({ id: "i1" })} />);
    });
    expect(await screen.findByText("Acknowledge")).toBeInTheDocument();
    expect(screen.getByText("Resolve")).toBeInTheDocument();
  });

  it("hides lifecycle controls for viewers", async () => {
    (hasRole as jest.Mock).mockReturnValue(false);
    await act(async () => {
      render(<IncidentDetailPage params={Promise.resolve({ id: "i1" })} />);
    });
    expect(await screen.findByText(/requires the approver role/)).toBeInTheDocument();
    expect(screen.getByText("Acknowledge")).toBeDisabled();
  });
});
